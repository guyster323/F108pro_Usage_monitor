from __future__ import annotations

import json
import os
import tomllib
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

OIDC_SCOPE = "https://auth.x.ai::b1a00492-073a-47ea-816f-4c329264a828"
MAX_GROK_CONFIG_BYTES = 1024 * 1024


@dataclass
class GrokAuth:
    # The OAuth bearer must never appear in logs or diagnostics through repr().
    key: str = field(repr=False)
    user_id: str
    email: str | None
    expires_at: datetime | None
    auth_mode: str
    path: Path
    @property
    def stale(self) -> bool:
        if self.expires_at is None:
            return False
        return datetime.now(timezone.utc) >= self.expires_at


def grok_home() -> Path:
    env = os.environ.get("GROK_HOME")
    return Path(env) if env else Path.home() / ".grok"


def grok_sessions_root() -> Path:
    """Return the official Grok Build session root without reading content."""

    return grok_home() / "sessions"


def has_xai_api_key() -> bool:
    """Report whether direct API auth is configured without returning its value.

    Grok Build treats ``XAI_API_KEY`` as a fallback only when no stored session
    token is active.  Callers therefore still need to check ``read_grok_auth``
    first when deciding which account mode is effective.
    """

    value = os.environ.get("XAI_API_KEY")
    return bool(value and value.strip())


def _configured_model_credential(raw: object) -> bool:
    """Detect a possible per-model credential without returning its value."""

    if not isinstance(raw, dict):
        return False
    models = raw.get("model")
    if not isinstance(models, dict):
        return False
    for model in models.values():
        if not isinstance(model, dict):
            continue
        inline = model.get("api_key")
        if isinstance(inline, str) and inline.strip():
            return True
        environment_names = model.get("env_key")
        if isinstance(environment_names, str):
            environment_names = [environment_names]
        if isinstance(environment_names, list):
            for name in environment_names:
                if not isinstance(name, str) or not name.strip():
                    continue
                value = os.environ.get(name)
                if isinstance(value, str) and value.strip():
                    return True
        # A named helper mints a model bearer token. QuotaDeck deliberately
        # does not execute it, so the resulting billing identity is unknown.
        provider = model.get("auth_provider")
        if isinstance(provider, str) and provider.strip():
            return True
    return False


def _read_grok_config(path: Path) -> object:
    try:
        if path.stat().st_size > MAX_GROK_CONFIG_BYTES:
            return None
        data = path.read_bytes()
        if path.suffix.casefold() == ".json":
            return json.loads(data.decode("utf-8"))
        return tomllib.loads(data.decode("utf-8"))
    except (
        OSError,
        UnicodeError,
        json.JSONDecodeError,
        tomllib.TOMLDecodeError,
        RecursionError,
    ):
        return None


def has_possible_model_credentials() -> bool:
    """Conservatively detect Grok Build per-model auth selectors.

    Grok Build gives a selected model's ``api_key``, populated ``env_key`` or
    ``auth_provider`` precedence over a stored login.  QuotaDeck cannot know a
    future invocation's ``--model`` selection, so any such usable declaration
    makes the account-wide billing mode ambiguous.  Parsed secret values are
    used only as presence signals and are never retained in returned objects.
    """

    home = grok_home()
    paths = [
        Path("/etc/grok/managed_config.toml"),
        home / "managed_config.toml",
        home / "config.toml",
        home / "requirements.toml",
        Path("/etc/grok/requirements.toml"),
    ]
    configured_path = os.environ.get("GROK_CONFIG_PATH")
    if isinstance(configured_path, str) and configured_path.strip():
        paths.append(Path(configured_path).expanduser())
    seen: set[str] = set()
    for path in paths:
        try:
            key = str(path.resolve())
        except OSError:
            key = str(path.absolute())
        if key in seen:
            continue
        seen.add(key)
        if path.is_file() and _configured_model_credential(_read_grok_config(path)):
            return True

    inline = os.environ.get("GROK_CONFIG")
    if isinstance(inline, str) and inline.strip():
        try:
            if _configured_model_credential(json.loads(inline)):
                return True
        except (json.JSONDecodeError, RecursionError):
            pass
    return False


def read_grok_auth() -> GrokAuth | None:
    path = grok_home() / "auth.json"
    if not path.is_file():
        return None
    try:
        if path.stat().st_size > MAX_GROK_CONFIG_BYTES:
            return None
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError, RecursionError):
        return None
    if not isinstance(raw, dict):
        return None
    entry = raw.get(OIDC_SCOPE)
    if not isinstance(entry, dict):
        for value in raw.values():
            if isinstance(value, dict) and value.get("auth_mode") == "oidc":
                entry = value
                break
    if not isinstance(entry, dict):
        return None
    if entry.get("auth_mode") == "api_key":
        return None
    key = entry.get("key")
    user_id = entry.get("user_id") or entry.get("userId")
    if (
        not isinstance(key, str)
        or not key.strip()
        or not isinstance(user_id, str)
        or not user_id.strip()
    ):
        return None
    email = entry.get("email")
    if not isinstance(email, str) or not email.strip():
        email = None
    raw_auth_mode = entry.get("auth_mode")
    auth_mode = (
        raw_auth_mode.strip()
        if isinstance(raw_auth_mode, str) and raw_auth_mode.strip()
        else "oidc"
    )
    expires = entry.get("expires_at")
    expires_at = None
    if expires:
        try:
            expires_at = datetime.fromisoformat(str(expires).replace("Z", "+00:00"))
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            else:
                expires_at = expires_at.astimezone(timezone.utc)
        except (OSError, OverflowError, ValueError):
            expires_at = None
    if expires_at is None and entry.get("create_time"):
        try:
            created = datetime.fromisoformat(str(entry["create_time"]).replace("Z", "+00:00"))
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            else:
                created = created.astimezone(timezone.utc)
            expires_at = created + timedelta(days=30)
        except (OSError, OverflowError, ValueError):
            pass
    return GrokAuth(
        key=key,
        user_id=user_id,
        email=email,
        expires_at=expires_at,
        auth_mode=auth_mode,
        path=path,
    )
