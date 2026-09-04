from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from quotadeck.core.mask import email_local
from quotadeck.core.models import AccountRef
from quotadeck.providers.jwtutil import decode_payload


@dataclass
class CodexAuth:
    home: Path
    access_token: str | None
    id_token: str | None
    refresh_token: str | None
    account_id: str | None
    last_refresh: datetime | None
    email: str | None
    plan: str | None
    stale: bool


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def read_auth(home: Path) -> CodexAuth | None:
    path = home / "auth.json"
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    tokens = raw.get("tokens") or {}
    access = tokens.get("access_token") or tokens.get("accessToken")
    ident = tokens.get("id_token") or tokens.get("idToken")
    refresh = tokens.get("refresh_token") or tokens.get("refreshToken")
    account_id = tokens.get("account_id") or tokens.get("accountId")
    payload = decode_payload(ident or "") or decode_payload(access or "")
    auth_ns = payload.get("https://api.openai.com/auth") or {}
    profile = payload.get("https://api.openai.com/profile") or {}
    if not account_id:
        account_id = (
            payload.get("chatgpt_account_id")
            or auth_ns.get("chatgpt_account_id")
            or ((payload.get("organizations") or [{}])[0].get("id") if payload.get("organizations") else None)
        )
    email = payload.get("email") or profile.get("email")
    plan = auth_ns.get("chatgpt_plan_type") or payload.get("chatgpt_plan_type")
    last_refresh = _parse_iso(raw.get("last_refresh"))
    stale = False
    exp = payload.get("exp")
    now = datetime.now(timezone.utc)
    if isinstance(exp, (int, float)):
        stale = datetime.fromtimestamp(exp, tz=timezone.utc) <= now + timedelta(minutes=5)
    elif last_refresh and now - last_refresh > timedelta(days=8):
        stale = True
    if not access and not ident:
        return None
    return CodexAuth(
        home=home,
        access_token=access,
        id_token=ident,
        refresh_token=refresh,
        account_id=str(account_id) if account_id else None,
        last_refresh=last_refresh,
        email=email,
        plan=str(plan) if plan else None,
        stale=stale,
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
            cfg = json.loads(bar.read_text(encoding="utf-8"))
            for provider in cfg.get("providers") or []:
                for extra in provider.get("codexProfileHomePaths") or []:
                    homes.append(Path(os.path.expanduser(extra)))
        except (OSError, json.JSONDecodeError):
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
        auth = read_auth(home)
        if auth is None:
            continue
        account_id = auth.account_id or f"home-{abs(hash(str(home))) % 10**8:08d}"
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
                extra={"stale": "1" if auth.stale else "0"},
            )
        )
    return found
