from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

log = logging.getLogger("quotadeck")


@dataclass
class PersistedState:
    """Runtime state that must survive restarts so flash budgets are not reset.

    Never store credentials or raw API responses here.
    """

    last_upload: datetime | None = None
    uploads_today: int = 0
    day: date | None = None
    last_hash: str | None = None


def default_state_path() -> Path:
    from quotadeck.config import app_dir

    return app_dir() / "state.json"


def load_state(path: Path | None = None) -> PersistedState:
    target = path or default_state_path()
    if not target.exists():
        return PersistedState()
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
        last_upload = None
        if raw.get("last_upload"):
            last_upload = datetime.fromisoformat(raw["last_upload"])
            if last_upload.tzinfo is None:
                last_upload = last_upload.replace(tzinfo=timezone.utc)
        day = date.fromisoformat(raw["day"]) if raw.get("day") else None
        uploads_today = max(0, int(raw.get("uploads_today", 0)))
        return PersistedState(
            last_upload=last_upload,
            uploads_today=uploads_today,
            day=day,
            last_hash=raw.get("last_hash") or None,
        )
    except Exception:
        log.exception("state file %s unreadable; starting fresh", target)
        return PersistedState()


def save_state(state: PersistedState, path: Path | None = None) -> Path:
    target = path or default_state_path()
    payload = {
        "last_upload": state.last_upload.isoformat() if state.last_upload else None,
        "uploads_today": state.uploads_today,
        "day": state.day.isoformat() if state.day else None,
        "last_hash": state.last_hash,
    }
    tmp = target.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    os.replace(tmp, target)
    return target
