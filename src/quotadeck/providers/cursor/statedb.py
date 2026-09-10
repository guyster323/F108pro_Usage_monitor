from __future__ import annotations

import json
import os
import sqlite3
import tempfile
from dataclasses import dataclass
from pathlib import Path

from quotadeck.providers.jwtutil import decode_payload


@dataclass
class CursorAuth:
    access_token: str
    email: str | None
    plan: str | None
    user_id: str
    source: str
    source_kind: str = "app"
    source_label: str = "Cursor App"

def _strip_quotes(value: str | None) -> str | None:
    if value is None:
        return None
    return value.strip().strip('"')


def ide_db_path() -> Path:
    appdata = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    return Path(appdata) / "Cursor" / "User" / "globalStorage" / "state.vscdb"


def cli_auth_path() -> Path:
    appdata = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    return Path(appdata) / "Cursor" / "auth.json"

def _user_id(token: str) -> str:
    payload = decode_payload(token)
    sub = str(payload.get("sub") or "")
    return sub.split("|")[-1] if sub else "unknown"

def read_ide_auth() -> CursorAuth | None:
    db_path = ide_db_path()
    if not db_path.is_file():
        return None
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(prefix="quotadeck-vscdb-", suffix=".vscdb", delete=False) as tmp:
            tmp_path = Path(tmp.name)
        tmp_path.write_bytes(db_path.read_bytes())
        conn = sqlite3.connect(f"file:{tmp_path.as_posix()}?mode=ro", uri=True)
        try:
            cur = conn.cursor()
            def get(key: str) -> str | None:
                row = cur.execute("SELECT value FROM ItemTable WHERE key = ?", (key,)).fetchone()
                if not row or row[0] is None:
                    return None
                value = row[0]
                if isinstance(value, bytes):
                    value = value.decode("utf-8", "replace")
                return _strip_quotes(str(value))
            token = get("cursorAuth/accessToken")
            if not token:
                return None
            return CursorAuth(
                access_token=token,
                email=get("cursorAuth/cachedEmail"),
                plan=get("cursorAuth/stripeMembershipType"),
                user_id=_user_id(token),
                source=str(db_path),
                source_kind="app",
                source_label="Cursor App",
            )
        finally:
            conn.close()
    except OSError:
        return None
    finally:
        if tmp_path and tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass

def read_cli_auth() -> CursorAuth | None:
    path = cli_auth_path()
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    token = raw.get("accessToken") or raw.get("access_token")
    if not token:
        return None
    return CursorAuth(
        access_token=token,
        email=None,
        plan=None,
        user_id=_user_id(token),
        source=str(path),
        source_kind="cli",
        source_label="Cursor CLI",
    )

def discover_cursor_auths() -> list[CursorAuth]:
    found: list[CursorAuth] = []
    seen: set[str] = set()
    for auth in (read_ide_auth(), read_cli_auth()):
        if auth is None or auth.user_id in seen:
            continue
        seen.add(auth.user_id)
        found.append(auth)
    return found


def load_cursor_auth() -> CursorAuth | None:
    auths = discover_cursor_auths()
    return auths[0] if auths else None

def load_cursor_auth_for(account: object) -> CursorAuth | None:
    source_path = str(getattr(account, "source_path", "") or "")
    account_id = str(getattr(account, "account_id", "") or "")
    ide = read_ide_auth()
    cli = read_cli_auth()
    if source_path:
        if ide and ide.source == source_path:
            return ide
        if cli and cli.source == source_path:
            return cli
    if account_id:
        if ide and ide.user_id == account_id:
            return ide
        if cli and cli.user_id == account_id:
            return cli
    return ide or cli
