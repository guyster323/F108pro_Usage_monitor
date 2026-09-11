from __future__ import annotations

import json
import os
import tomllib
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from math import isfinite
from pathlib import Path
from typing import Literal

from quotadeck.core.mask import email_local
from quotadeck.core.models import AccountRef
from quotadeck.providers.jwtutil import decode_payload

MAX_HISTORY_DISCOVERY_ENTRIES = 100_000
MAX_AUTH_JSON_BYTES = 1024 * 1024

@dataclass
class CodexAuth:
    home: Path
    access_token: str | None = field(repr=False)
    id_token: str | None = field(repr=False)
    refresh_token: str | None = field(repr=False)
    account_id: str | None
    last_refresh: datetime | None
    email: str | None
    plan: str | None
    stale: bool
    mode: Literal["oauth", "api_key"] = "oauth"

    @property
    def is_api_key_only(self) -> bool:
        return self.mode == "api_key"


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except (OSError, OverflowError, ValueError):
        return None


def _secret_text(value: object) -> str | None:
    """Return an OAuth secret only when valid; callers never pass API keys."""

    return value if isinstance(value, str) and bool(value.strip()) else None


def _secret_is_present(value: object) -> bool:
    """Check credential presence without retaining it in discovered metadata."""

    return isinstance(value, str) and bool(value.strip())


def _process_openai_api_key_present() -> bool:
    """Check the process API-key signal without ever returning its value."""

    return any(
        _secret_is_present(os.environ.get(name))
        for name in ("OPENAI_API_KEY", "CODEX_API_KEY")
    )


def _is_explicit_api_key_mode(value: object) -> bool:
    """Recognize Codex's serialized ``AuthMode::ApiKey`` spelling variants."""

    if not isinstance(value, str):
        return False
    normalized = "".join(character for character in value.casefold() if character.isalnum())
    return normalized == "apikey"


def _path_key(path: Path) -> str:
    try:
        return str(path.expanduser().resolve())
    except OSError:
        return str(path.expanduser().absolute())


def _fallback_account_id(home: Path) -> str:
    """Return a stable, non-reversible identifier for homes without account IDs."""

    digest = sha256(_path_key(home).encode("utf-8", errors="surrogatepass")).hexdigest()
    return f"home-{digest[:12]}"


def _environment_api_home() -> Path:
    """Choose the only home to which a process-global key may be attributed."""

    configured = os.environ.get("CODEX_HOME")
    if isinstance(configured, str) and configured.strip():
        return Path(configured).expanduser()
    return Path.home() / ".codex"


def process_api_key_applies_to(home: Path) -> bool:
    """Return whether this process-global key can be assigned to ``home``.

    Account discovery can inspect several independent Codex profile homes, but
    ``OPENAI_API_KEY`` has no profile identifier.  Attribute it only to the
    explicitly selected ``CODEX_HOME`` (or the default ``~/.codex``) instead of
    cloning one API-key identity across every profile found on disk.
    """

    return (
        _process_openai_api_key_present()
        and _path_key(home) == _path_key(_environment_api_home())
    )


_OFFICIAL_OPENAI_BASE_URLS = {
    "https://api.openai.com",
    "https://api.openai.com/v1",
}


def _official_openai_base_url(value: object) -> bool:
    if not isinstance(value, str) or not value.strip():
        return True
    return value.strip().rstrip("/").casefold() in _OFFICIAL_OPENAI_BASE_URLS


def _codex_config_is_official_only(raw: object) -> bool:
    if not isinstance(raw, dict):
        return False

    configurations: list[dict[str, object]] = [raw]
    profiles = raw.get("profiles")
    if profiles is not None:
        if not isinstance(profiles, dict):
            return False
        for profile in profiles.values():
            if not isinstance(profile, dict):
                return False
            configurations.append(profile)

    for config in configurations:
        provider_name = config.get("model_provider", "openai")
        if (
            not isinstance(provider_name, str)
            or provider_name.strip().casefold() != "openai"
        ):
            return False
        for name in ("base_url", "openai_base_url", "openai_api_base"):
            if name in config and not _official_openai_base_url(config.get(name)):
                return False

    providers = raw.get("model_providers")
    if providers is not None:
        if not isinstance(providers, dict):
            return False
        for name, provider in providers.items():
            if not isinstance(name, str) or name.strip().casefold() != "openai":
                return False
            if not isinstance(provider, dict):
                return False
            if not _official_openai_base_url(provider.get("base_url")):
                return False
    return True


def _codex_pricing_config_paths(home: Path) -> tuple[Path, ...] | None:
    """Return bounded user/project/managed config layers, or fail closed."""

    candidates: list[Path] = [home.expanduser() / "config.toml"]
    try:
        profile_configs = sorted(home.expanduser().glob("*.config.toml"))
    except OSError:
        return None
    if len(profile_configs) > 64:
        return None
    candidates.extend(profile_configs)
    candidates.append(Path.cwd() / ".codex" / "config.toml")
    candidates.append(Path("/etc/codex/config.toml"))
    program_data = os.environ.get("PROGRAMDATA")
    if isinstance(program_data, str) and program_data.strip():
        candidates.append(Path(program_data) / "OpenAI" / "Codex" / "config.toml")
    for name in ("CODEX_CONFIG", "CODEX_CONFIG_FILE"):
        configured = os.environ.get(name)
        if isinstance(configured, str) and configured.strip():
            candidates.append(Path(configured).expanduser())

    unique: list[Path] = []
    seen: set[str] = set()
    for path in candidates:
        key = _path_key(path)
        if key not in seen:
            seen.add(key)
            unique.append(path)
    return tuple(unique)


def uses_official_openai_pricing_endpoint(home: Path) -> bool:
    """Conservatively gate first-party list-price conversion.

    An API key proves neither the endpoint nor the price schedule. Azure,
    OpenAI-compatible gateways, and custom Codex model providers therefore
    keep cumulative dollars unavailable.
    """

    for name in ("AZURE_OPENAI_ENDPOINT", "OPENAI_API_TYPE", "OPENAI_API_VERSION"):
        value = os.environ.get(name)
        if isinstance(value, str) and value.strip():
            return False
    for name in ("OPENAI_BASE_URL", "OPENAI_API_BASE"):
        if not _official_openai_base_url(os.environ.get(name)):
            return False

    config_paths = _codex_pricing_config_paths(home)
    if config_paths is None:
        return False
    for config_path in config_paths:
        if not config_path.exists():
            continue
        try:
            if not config_path.is_file() or config_path.stat().st_size > 1024 * 1024:
                return False
            raw = tomllib.loads(config_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, tomllib.TOMLDecodeError, RecursionError):
            return False
        if not _codex_config_is_official_only(raw):
            return False
    return True


def _has_retained_local_history(home: Path) -> bool:
    """Detect local token ledgers without opening or decoding their contents."""

    for directory_name in ("sessions", "archived_sessions"):
        root = home.expanduser() / directory_name
        if not root.is_dir():
            continue
        try:
            resolved_root = root.resolve()
            entries_seen = 0

            def raise_walk_error(error: OSError) -> None:
                raise error

            for directory, directories, filenames in os.walk(
                root,
                topdown=True,
                onerror=raise_walk_error,
                followlinks=False,
            ):
                entries_seen += len(directories) + len(filenames)
                if entries_seen > MAX_HISTORY_DISCOVERY_ENTRIES:
                    return False
                for filename in filenames:
                    if not filename.casefold().endswith(".jsonl"):
                        continue
                    path = Path(directory) / filename
                    if (
                        path.is_file()
                        and not path.is_symlink()
                        and path.resolve().is_relative_to(resolved_root)
                    ):
                        return True
        except (OSError, RuntimeError):
            continue
    return False


def read_auth(
    home: Path,
    *,
    include_process_environment: bool = True,
) -> CodexAuth | None:
    """Read Codex auth metadata without retaining any API-key value.

    Codex considers either an explicit API auth mode, a key in ``auth.json``,
    or the process ``OPENAI_API_KEY`` to be an API-auth signal.  The optional
    flag lets discovery distinguish a file-backed identity from a process-only
    key before conservatively assigning that global key to one local home.
    """

    path = home / "auth.json"
    process_api_key = include_process_environment and _process_openai_api_key_present()
    raw: dict[str, object] = {}
    if path.is_file():
        try:
            if path.stat().st_size > MAX_AUTH_JSON_BYTES:
                if not process_api_key:
                    return None
                parsed = {}
            else:
                parsed = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(parsed, dict):
                raw = parsed
            elif not process_api_key:
                return None
        except (OSError, UnicodeError, json.JSONDecodeError, RecursionError):
            if not process_api_key:
                return None
    elif not process_api_key:
        return None

    token_value = raw.get("tokens") or {}
    tokens = token_value if isinstance(token_value, dict) else {}
    access = _secret_text(tokens.get("access_token") or tokens.get("accessToken"))
    ident = _secret_text(tokens.get("id_token") or tokens.get("idToken"))
    refresh = _secret_text(tokens.get("refresh_token") or tokens.get("refreshToken"))
    account_id = tokens.get("account_id") or tokens.get("accountId")
    has_oauth = access is not None or ident is not None
    file_api_key = any(
        _secret_is_present(raw.get(name))
        for name in (
            "OPENAI_API_KEY",
            "openai_api_key",
            "CODEX_API_KEY",
            "codex_api_key",
        )
    )
    explicit_api_key_mode = _is_explicit_api_key_mode(raw.get("auth_mode"))
    if not has_oauth and not file_api_key and not process_api_key:
        return None

    # Codex treats explicit AuthMode::ApiKey, a stored key, or the process key
    # as API authentication.  Retained OAuth tokens never override those
    # signals.  Do not retain or derive an identity from inactive OAuth
    # material: an API key cannot safely be attributed to that OAuth account.
    mode: Literal["oauth", "api_key"] = (
        "api_key"
        if explicit_api_key_mode or file_api_key or process_api_key
        else "oauth"
    )
    if mode == "api_key":
        return CodexAuth(
            home=home,
            access_token=None,
            id_token=None,
            refresh_token=None,
            account_id=None,
            last_refresh=None,
            email=None,
            plan="api",
            stale=False,
            mode="api_key",
        )

    payload = decode_payload(ident or "") or decode_payload(access or "")
    auth_value = payload.get("https://api.openai.com/auth")
    auth_ns = auth_value if isinstance(auth_value, dict) else {}
    profile_value = payload.get("https://api.openai.com/profile")
    profile = profile_value if isinstance(profile_value, dict) else {}
    if not account_id:
        organizations = payload.get("organizations")
        first_organization = (
            organizations[0]
            if isinstance(organizations, list)
            and organizations
            and isinstance(organizations[0], dict)
            else {}
        )
        account_id = (
            payload.get("chatgpt_account_id")
            or auth_ns.get("chatgpt_account_id")
            or first_organization.get("id")
        )
    email = payload.get("email") or profile.get("email")
    plan = auth_ns.get("chatgpt_plan_type") or payload.get("chatgpt_plan_type")
    last_refresh_value = raw.get("last_refresh")
    last_refresh = _parse_iso(last_refresh_value if isinstance(last_refresh_value, str) else None)
    stale = False
    exp = payload.get("exp")
    now = datetime.now(timezone.utc)
    if isinstance(exp, (int, float)) and not isinstance(exp, bool):
        try:
            expiration = float(exp)
            if isfinite(expiration):
                stale = datetime.fromtimestamp(
                    expiration,
                    tz=timezone.utc,
                ) <= now + timedelta(minutes=5)
        except (OSError, OverflowError, ValueError):
            stale = False
    elif last_refresh and now - last_refresh > timedelta(days=8):
        stale = True
    return CodexAuth(
        home=home,
        access_token=access,
        id_token=ident,
        refresh_token=refresh,
        account_id=str(account_id) if account_id else None,
        last_refresh=last_refresh,
        email=email if isinstance(email, str) else None,
        plan="api" if mode == "api_key" else str(plan) if plan else None,
        stale=stale,
        mode=mode,
    )

def candidate_homes() -> list[Path]:
    homes: list[Path] = []
    env = os.environ.get("CODEX_HOME")
    if env:
        homes.append(Path(env))
    user = Path.home()
    homes.append(user / ".codex")
    homes.extend(sorted(user.glob(".codex-*")))
    appdata = os.environ.get("APPDATA")
    if appdata:
        orca = Path(appdata) / "orca" / "codex-accounts"
        if orca.is_dir():
            homes.extend(sorted(p / "home" for p in orca.iterdir() if p.is_dir()))
    bar = user / ".codexbar" / "config.json"
    if bar.is_file():
        try:
            if bar.stat().st_size > MAX_AUTH_JSON_BYTES:
                raise ValueError("CodexBar config is too large")
            cfg = json.loads(bar.read_text(encoding="utf-8"))
            for provider in cfg.get("providers") or []:
                for extra in provider.get("codexProfileHomePaths") or []:
                    homes.append(Path(os.path.expanduser(extra)))
        except (OSError, UnicodeError, ValueError, json.JSONDecodeError, RecursionError):
            pass
    unique: list[Path] = []
    seen: set[str] = set()
    for home in homes:
        try:
            key = str(home.resolve())
        except OSError:
            key = str(home)
        if key in seen:
            continue
        seen.add(key)
        unique.append(home)
    return unique

def discover_codex_accounts() -> list[AccountRef]:
    found: list[AccountRef] = []
    seen_ids: set[str] = set()
    for home in candidate_homes():
        process_key_here = process_api_key_applies_to(home)
        auth = read_auth(
            home,
            include_process_environment=process_key_here,
        )
        # With no file-backed identity, retained history is the minimum signal
        # needed to create a useful process-key account.  This also avoids
        # inventing an account from an unrelated environment variable alone.
        if (
            auth is not None
            and process_key_here
            and not (home / "auth.json").is_file()
            and not _has_retained_local_history(home)
        ):
            auth = None
        if auth is None:
            continue
        account_id = auth.account_id or _fallback_account_id(home)
        if account_id in seen_ids:
            continue
        seen_ids.add(account_id)
        local = email_local(auth.email) or home.name
        alias = (auth.plan or local).upper()
        if any(item.display_name == alias for item in found):
            alias = f"{local.upper()[:6]}-{account_id[:4].upper()}"
        found.append(
            AccountRef(
                provider="codex",
                account_id=account_id,
                display_name=alias,
                source_path=str(home),
                plan=auth.plan,
                email_local=local,
                source_kind="cli",
                source_label="Codex CLI",
                extra={
                    "stale": "1" if auth.stale else "0",
                    "auth_mode": auth.mode,
                    "pricing_scope": (
                        "official"
                        if uses_official_openai_pricing_endpoint(home)
                        else "custom"
                    ),
                },
            )
        )
    return found
