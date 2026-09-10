from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

from quotadeck.core.flashbudget import DEFAULT_FLASH_INTERVAL_MINUTES
from quotadeck.core.models import AccountRef, DisplayMode
from quotadeck.devices.aula_f108.constants import LCD_DEFAULT_BUDGET, LCD_SOFT_CAP

log = logging.getLogger("quotadeck")

CONFIG_VERSION = 4
DEFAULT_SCENE_HOLD_SECONDS = 5
DEFAULT_MIN_UPLOAD_MINUTES = DEFAULT_FLASH_INTERVAL_MINUTES
LEGACY_DEFAULT_FRAME_BUDGET = 32
LEGACY_DEFAULT_MIN_UPLOAD_MINUTES = 10

POLL_SECONDS_MIN = 15
POLL_SECONDS_MAX = 3600
SCENE_HOLD_SECONDS_MIN = 2
SCENE_HOLD_SECONDS_MAX = 20
MIN_UPLOAD_MINUTES_MIN = 1
MIN_UPLOAD_MINUTES_MAX = 720


def app_dir() -> Path:
    if os.name == "nt":
        root = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    else:
        root = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    path = root / "QuotaDeck"
    path.mkdir(parents=True, exist_ok=True)
    (path / "logs").mkdir(exist_ok=True)
    return path


def default_config_path() -> Path:
    return app_dir() / "config.json"


def default_theme_dir() -> Path:
    import sys

    if getattr(sys, "_MEIPASS", None):
        bundled = Path(sys._MEIPASS) / "themes" / "quotadeck-crew"
        if bundled.is_dir():
            return bundled
    here = Path(__file__).resolve().parents[2]
    return here / "themes" / "quotadeck-crew"


@dataclass
class AccountConfig:
    provider: str
    account_id: str
    alias: str
    enabled: bool = True
    source_path: str = ""
    source_kind: str = "cli"
    source_label: str = ""


@dataclass
class AppConfig:
    accounts: list[AccountConfig] = field(default_factory=list)
    display_mode: DisplayMode = DisplayMode.SMART
    theme: str = "quotadeck-crew"
    poll_seconds: int = 60
    scene_hold_seconds: int = DEFAULT_SCENE_HOLD_SECONDS
    min_upload_minutes: int = DEFAULT_MIN_UPLOAD_MINUTES
    ui_language: str = "ko"
    max_age_minutes: int = 60
    daily_flash_limit: int = 100
    frame_budget: int = LCD_DEFAULT_BUDGET
    launch_at_startup: bool = False
    config_version: int = CONFIG_VERSION

    def enabled_keys(self) -> list[str]:
        return [f"{a.provider}:{a.account_id}" for a in self.accounts if a.enabled]

    def alias_for(self, provider: str, account_id: str, fallback: str) -> str:
        for item in self.accounts:
            if item.provider == provider and item.account_id == account_id and item.alias:
                return item.alias
        return fallback


def account_config_from_ref(
    account: AccountRef,
    *,
    enabled: bool = True,
    alias: str | None = None,
) -> AccountConfig:
    return AccountConfig(
        provider=account.provider,
        account_id=account.account_id,
        alias=(alias or account.display_name).upper(),
        enabled=enabled,
        source_path=account.source_path,
        source_kind=account.source_kind or "cli",
        source_label=account.source_label,
    )


def _hold_seconds(raw: dict) -> int:
    if "scene_hold_seconds" not in raw:
        return DEFAULT_SCENE_HOLD_SECONDS
    try:
        value = int(raw.get("scene_hold_seconds") or DEFAULT_SCENE_HOLD_SECONDS)
    except (TypeError, ValueError):
        value = DEFAULT_SCENE_HOLD_SECONDS
    # A value present in a legacy file is an explicit user choice. Preserve it
    # while constraining it to the firmware-compatible UI range.
    return max(SCENE_HOLD_SECONDS_MIN, min(SCENE_HOLD_SECONDS_MAX, value))


def _clamp_int(value: object, lo: int, hi: int, fallback: int) -> int:
    try:
        number = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError, OverflowError):
        return fallback
    return max(lo, min(hi, number))


def _frame_budget(raw: dict) -> int:
    """Load the frame budget and migrate the old timing default."""
    value = _clamp_int(
        raw.get("frame_budget", LCD_DEFAULT_BUDGET),
        1,
        LCD_SOFT_CAP,
        LCD_DEFAULT_BUDGET,
    )
    try:
        version = int(raw.get("config_version", 1))
    except (TypeError, ValueError, OverflowError):
        version = 1
    if version < CONFIG_VERSION and value == LEGACY_DEFAULT_FRAME_BUDGET:
        return LCD_DEFAULT_BUDGET
    return value


def _min_upload_minutes(raw: dict) -> int:
    """Load the flash interval and migrate the old 10-minute default."""
    value = _clamp_int(
        raw.get("min_upload_minutes", DEFAULT_MIN_UPLOAD_MINUTES),
        MIN_UPLOAD_MINUTES_MIN,
        MIN_UPLOAD_MINUTES_MAX,
        DEFAULT_MIN_UPLOAD_MINUTES,
    )
    try:
        version = int(raw.get("config_version", 1))
    except (TypeError, ValueError, OverflowError):
        version = 1
    if version < CONFIG_VERSION and value == LEGACY_DEFAULT_MIN_UPLOAD_MINUTES:
        return DEFAULT_MIN_UPLOAD_MINUTES
    return value


def _account_from_dict(raw_account: object) -> AccountConfig | None:
    if not isinstance(raw_account, dict):
        return None
    if not raw_account.get("provider") or not raw_account.get("account_id"):
        return None
    allowed = {field.name for field in fields(AccountConfig)}
    values = {key: value for key, value in raw_account.items() if key in allowed}
    values.setdefault("alias", str(raw_account["account_id"]))
    try:
        return AccountConfig(**values)
    except (TypeError, ValueError):
        return None


def load_config(path: Path | None = None) -> AppConfig:
    target = path or default_config_path()
    if not target.exists():
        return AppConfig()
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("config root must be a JSON object")
        raw_accounts = raw.get("accounts", [])
        if not isinstance(raw_accounts, list):
            raw_accounts = []
        accounts = [
            account
            for item in raw_accounts
            if (account := _account_from_dict(item)) is not None
        ]
        try:
            mode = DisplayMode(raw.get("display_mode", DisplayMode.SMART.value))
        except (TypeError, ValueError):
            mode = DisplayMode.SMART
        return AppConfig(
            accounts=accounts,
            display_mode=mode,
            theme=str(raw.get("theme") or "quotadeck-crew"),
            poll_seconds=_clamp_int(
                raw.get("poll_seconds", 60), POLL_SECONDS_MIN, POLL_SECONDS_MAX, 60
            ),
            scene_hold_seconds=_hold_seconds(raw),
            min_upload_minutes=_min_upload_minutes(raw),
            max_age_minutes=_clamp_int(raw.get("max_age_minutes", 60), 1, 1440, 60),
            daily_flash_limit=_clamp_int(raw.get("daily_flash_limit", 100), 1, 1000, 100),
            frame_budget=_frame_budget(raw),
            launch_at_startup=bool(raw.get("launch_at_startup", False)),
            ui_language="en" if raw.get("ui_language") == "en" else "ko",
            config_version=CONFIG_VERSION,
        )
    except Exception:
        # Keep the broken file for diagnosis, then fall back to defaults so a
        # corrupt config can never schedule pathological poll/flash behaviour.
        log.exception("config %s unreadable; using defaults", target)
        backup = target.with_suffix(".json.bad")
        try:
            os.replace(target, backup)
        except OSError:
            pass
        return AppConfig()


def save_config(config: AppConfig, path: Path | None = None) -> Path:
    target = path or default_config_path()
    payload = asdict(config)
    payload["display_mode"] = config.display_mode.value
    payload["config_version"] = CONFIG_VERSION
    tmp = target.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    os.replace(tmp, target)
    return target
