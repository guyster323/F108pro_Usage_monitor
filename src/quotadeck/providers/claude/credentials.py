from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass
class ClaudeAuth:
    access_token: str
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

def read_claude_auth() -> ClaudeAuth | None:
    path = credentials_path()
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    oauth = raw.get("claudeAiOauth") or {}
    token = oauth.get("accessToken") or oauth.get("access_token")
    if not token:
        return None
    expires = oauth.get("expiresAt") or oauth.get("expires_at")
    expires_at = None
    if isinstance(expires, (int, float)):
        ts = float(expires)
        if ts > 1e12:
            ts /= 1000.0
        expires_at = datetime.fromtimestamp(ts, tz=timezone.utc)
    return ClaudeAuth(
        access_token=token,
        expires_at=expires_at,
        subscription=oauth.get("subscriptionType") or oauth.get("rateLimitTier"),
        scopes=list(oauth.get("scopes") or []),
        path=path,
    )
