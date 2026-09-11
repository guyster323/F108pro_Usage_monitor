from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path


MAX_HISTORY_DISCOVERY_ENTRIES = 100_000
MAX_CREDENTIAL_JSON_BYTES = 1024 * 1024
MAX_MANAGED_SETTINGS_FRAGMENTS = 256


@dataclass
class ClaudeAuth:
    access_token: str = field(repr=False)
    expires_at: datetime | None
    subscription: str | None
    scopes: list[str]
    path: Path

    @property
    def stale(self) -> bool:
        if self.expires_at is None:
            return False
        return datetime.now(timezone.utc) >= self.expires_at
    @property
    def has_profile_scope(self) -> bool:
        return "user:profile" in self.scopes


def credentials_path() -> Path:
    override = os.environ.get("CLAUDE_SECURESTORAGE_CONFIG_DIR") or os.environ.get("CLAUDE_CONFIG_DIR")
    root = Path(override) if override else Path.home() / ".claude"
    return root / ".credentials.json"


def projects_path() -> Path:
    return credentials_path().parent / "projects"


def has_anthropic_api_key() -> bool:
    """Return only whether API-key billing is configured, never the key value."""

    value = os.environ.get("ANTHROPIC_API_KEY")
    if isinstance(value, str) and bool(value.strip()):
        return True
    return _settings_env_has("ANTHROPIC_API_KEY")


def uses_official_anthropic_pricing_endpoint() -> bool:
    """Return false for detected gateways whose token rates cannot be inferred."""

    value = os.environ.get("ANTHROPIC_BASE_URL")
    if isinstance(value, str) and value.strip() and not _official_anthropic_url(value):
        return False
    for name in (
        "ANTHROPIC_BEDROCK_BASE_URL",
        "ANTHROPIC_VERTEX_BASE_URL",
        "ANTHROPIC_FOUNDRY_BASE_URL",
    ):
        configured = os.environ.get(name)
        if isinstance(configured, str) and configured.strip():
            return False

    for path in _claude_settings_paths():
        if not path.exists():
            continue
        try:
            if not path.is_file() or path.stat().st_size > MAX_CREDENTIAL_JSON_BYTES:
                return False
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError, RecursionError):
            return False
        if not isinstance(raw, dict):
            return False
        force_login_method = raw.get("forceLoginMethod")
        if force_login_method is not None:
            if not isinstance(force_login_method, str):
                return False
            if force_login_method.strip().casefold() == "gateway":
                return False
        force_gateway_url = raw.get("forceLoginGatewayUrl")
        if force_gateway_url is not None:
            if not isinstance(force_gateway_url, str):
                return False
            # This setting exists specifically to route Claude through a
            # gateway.  Even an Anthropic-looking value does not prove that
            # retained history was billed at the public API list price.
            if force_gateway_url.strip():
                return False
        settings_env = raw.get("env")
        if settings_env is None:
            continue
        if not isinstance(settings_env, dict):
            return False
        base_url = settings_env.get("ANTHROPIC_BASE_URL")
        if (
            isinstance(base_url, str)
            and base_url.strip()
            and not _official_anthropic_url(base_url)
        ):
            return False
        for name in (
            "CLAUDE_CODE_USE_BEDROCK",
            "CLAUDE_CODE_USE_VERTEX",
            "CLAUDE_CODE_USE_FOUNDRY",
            "ANTHROPIC_BEDROCK_BASE_URL",
            "ANTHROPIC_VERTEX_BASE_URL",
            "ANTHROPIC_FOUNDRY_BASE_URL",
        ):
            configured = settings_env.get(name)
            if isinstance(configured, str) and configured.strip():
                return False
    return True


def _official_anthropic_url(value: str) -> bool:
    normalized = value.strip().rstrip("/").casefold()
    return normalized in {
        "https://api.anthropic.com",
        "https://api.anthropic.com/v1",
    }


def _claude_settings_paths() -> tuple[Path, ...]:
    config_root = credentials_path().parent
    candidates = [
        config_root / "settings.json",
        config_root / "settings.local.json",
        Path.cwd() / ".claude" / "settings.json",
        Path.cwd() / ".claude" / "settings.local.json",
    ]
    managed_roots = [
        Path("/etc/claude-code"),
        Path("/Library/Application Support/ClaudeCode"),
    ]
    program_data = os.environ.get("PROGRAMDATA")
    if isinstance(program_data, str) and program_data.strip():
        managed_roots.append(Path(program_data) / "ClaudeCode")
    program_files = os.environ.get("PROGRAMFILES")
    if isinstance(program_files, str) and program_files.strip():
        managed_roots.append(Path(program_files) / "ClaudeCode")
    elif os.name == "nt":
        managed_roots.append(Path(r"C:\Program Files") / "ClaudeCode")
    for managed_root in managed_roots:
        candidates.append(managed_root / "managed-settings.json")
        candidates.extend(
            _bounded_managed_fragments(managed_root / "managed-settings.d")
        )
    unique: list[Path] = []
    seen: set[str] = set()
    for path in candidates:
        try:
            key = str(path.expanduser().resolve())
        except OSError:
            key = str(path.expanduser().absolute())
        if key not in seen:
            seen.add(key)
            unique.append(path)
    return tuple(unique)


def _bounded_managed_fragments(directory: Path) -> tuple[Path, ...]:
    """List managed JSON fragments without an unbounded discovery walk.

    Returning the directory itself on an unreadable or oversized collection
    deliberately makes the pricing endpoint check fail closed.
    """

    if not directory.exists():
        return ()
    if directory.is_symlink() or not directory.is_dir():
        return (directory,)
    found: list[Path] = []
    seen = 0
    try:
        with os.scandir(directory) as entries:
            for entry in entries:
                seen += 1
                if seen > MAX_MANAGED_SETTINGS_FRAGMENTS:
                    return (directory,)
                if entry.name.casefold().endswith(".json"):
                    if not entry.is_file(follow_symlinks=False):
                        return (directory,)
                    found.append(Path(entry.path))
    except OSError:
        return (directory,)
    return tuple(sorted(found, key=lambda path: str(path).casefold()))


def has_ambiguous_auth_environment() -> bool:
    """Detect alternate Claude auth selectors without retaining credentials.

    A direct ``ANTHROPIC_API_KEY`` cannot identify the active billing path when
    Claude Code is also configured for a cloud provider or bearer token.  These
    signals therefore suppress OAuth quota calls and local price estimates.
    """

    alternate_auth_variables = (
        "ANTHROPIC_AUTH_TOKEN",
        "CLAUDE_CODE_USE_BEDROCK",
        "CLAUDE_CODE_USE_VERTEX",
        "CLAUDE_CODE_USE_FOUNDRY",
    )
    if any(
        isinstance(value := os.environ.get(name), str) and bool(value.strip())
        for name in alternate_auth_variables
    ):
        return True
    return _settings_env_has(*alternate_auth_variables)


def _settings_env_has(*names: str) -> bool:
    """Check configured Claude settings without retaining any secret value."""

    wanted = set(names)
    for path in _claude_settings_paths():
        if not path.exists():
            continue
        try:
            if not path.is_file() or path.stat().st_size > MAX_CREDENTIAL_JSON_BYTES:
                continue
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError, RecursionError):
            continue
        if not isinstance(raw, dict):
            continue
        settings_env = raw.get("env")
        if not isinstance(settings_env, dict):
            continue
        for name in wanted:
            value = settings_env.get(name)
            if isinstance(value, str) and bool(value.strip()):
                return True
    return False


def has_local_project_history(root: Path | None = None) -> bool:
    """Detect retained Claude response logs without reading conversation data."""

    projects = root or projects_path()
    if not projects.is_dir():
        return False
    try:
        resolved_root = projects.resolve()
        entries_seen = 0

        def raise_walk_error(error: OSError) -> None:
            raise error

        for directory, directories, filenames in os.walk(
            projects,
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
        return False
    except (OSError, RuntimeError):
        return False

def read_claude_auth() -> ClaudeAuth | None:
    path = credentials_path()
    if not path.is_file():
        return None
    try:
        if path.stat().st_size > MAX_CREDENTIAL_JSON_BYTES:
            return None
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError, RecursionError):
        return None
    if not isinstance(raw, dict):
        return None
    oauth = raw.get("claudeAiOauth") or {}
    if not isinstance(oauth, dict):
        return None
    token = oauth.get("accessToken") or oauth.get("access_token")
    if not isinstance(token, str) or not token.strip():
        return None
    expires = oauth.get("expiresAt") or oauth.get("expires_at")
    expires_at = None
    if isinstance(expires, (int, float)):
        try:
            ts = float(expires)
            if ts > 1e12:
                ts /= 1000.0
            expires_at = datetime.fromtimestamp(ts, tz=timezone.utc)
        except (OSError, OverflowError, ValueError):
            expires_at = None
    raw_scopes = oauth.get("scopes")
    scopes = (
        [item for item in raw_scopes if isinstance(item, str)]
        if isinstance(raw_scopes, list)
        else []
    )
    subscription = oauth.get("subscriptionType") or oauth.get("rateLimitTier")
    return ClaudeAuth(
        access_token=token,
        expires_at=expires_at,
        subscription=subscription if isinstance(subscription, str) else None,
        scopes=scopes,
        path=path,
    )
