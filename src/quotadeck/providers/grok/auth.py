from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

OIDC_SCOPE = "https://auth.x.ai::b1a00492-073a-47ea-816f-4c329264a828"


@dataclass
class GrokAuth:
    key: str
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

def read_grok_auth() -> GrokAuth | None:
    path = grok_home() / "auth.json"
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
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
    if not key or not user_id:
        return None
    expires = entry.get("expires_at")
    expires_at = None
    if expires:
        try:
            expires_at = datetime.fromisoformat(str(expires).replace("Z", "+00:00"))
        except ValueError:
            expires_at = None
    if expires_at is None and entry.get("create_time"):
        try:
            created = datetime.fromisoformat(str(entry["create_time"]).replace("Z", "+00:00"))
            expires_at = created + timedelta(days=30)
        except ValueError:
            pass
    return GrokAuth(
        key=key,
        user_id=str(user_id),
        email=entry.get("email"),
        expires_at=expires_at,
        auth_mode=str(entry.get("auth_mode") or "oidc"),
        path=path,
    )
