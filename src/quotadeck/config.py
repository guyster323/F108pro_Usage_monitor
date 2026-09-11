from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field, fields
from math import isfinite
from pathlib import Path
from typing import TYPE_CHECKING

from quotadeck.core.models import AccountRef, DisplayMode, MetricMode

if TYPE_CHECKING:
    from quotadeck.usage.models import CostCurrency, UsagePeriod

CONFIG_VERSION = 5
DEFAULT_SCENE_HOLD_SECONDS = 5
MIN_UPLOAD_MINUTES = 1
MAX_UPLOAD_MINUTES = 120
DEFAULT_USD_TO_KRW_RATE = 1400.0
MIN_USD_TO_KRW_RATE = 500.0
MAX_USD_TO_KRW_RATE = 5000.0


def clamp_min_upload_minutes(value: object) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError):
        parsed = 10
    return max(MIN_UPLOAD_MINUTES, min(MAX_UPLOAD_MINUTES, parsed))


def clamp_usd_to_krw_rate(value: object) -> float:
    """Keep a manually entered exchange rate finite and operationally sane."""

    if isinstance(value, bool):
        parsed = DEFAULT_USD_TO_KRW_RATE
    else:
        try:
            parsed = float(value)
        except (TypeError, ValueError, OverflowError):
            parsed = DEFAULT_USD_TO_KRW_RATE
    if not isfinite(parsed):
        parsed = DEFAULT_USD_TO_KRW_RATE
    return max(MIN_USD_TO_KRW_RATE, min(MAX_USD_TO_KRW_RATE, parsed))


def _default_cumulative_period() -> UsagePeriod:
    # Import lazily: usage package discovery imports AppConfig while starting.
    from quotadeck.usage.models import UsagePeriod

    return UsagePeriod.MONTHLY


def _default_cost_currency() -> CostCurrency:
    from quotadeck.usage.models import CostCurrency

    return CostCurrency.KRW

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


def default_flash_state_path() -> Path:
    return app_dir() / "flash-state.json"


_BUNDLED_THEME_PARTS = ("bundled", "themes", "quotadeck-crew")


def bundled_theme_dir() -> Path:
    """Return the install-scheme-independent package-data theme location."""

    return Path(__file__).resolve().parent.joinpath(*_BUNDLED_THEME_PARTS)


def _source_checkout_theme_dir() -> Path | None:
    """Find the generated canonical theme only from this exact source checkout."""

    package_dir = Path(__file__).resolve().parent
    try:
        root = package_dir.parents[1]
    except IndexError:
        return None
    expected_package = root / "src" / "quotadeck"
    if package_dir != expected_package.resolve():
        return None
    if not (root / "pyproject.toml").is_file():
        return None
    theme = root / "themes" / "quotadeck-crew"
    return theme if theme.is_dir() else None


def default_theme_dir() -> Path:
    import sys

    # Editable/source runs use the generated top-level theme. The sprite
    # freshness check guarantees that its package-data mirror is identical.
    source_theme = _source_checkout_theme_dir()
    if source_theme is not None:
        return source_theme

    # Wheels install this directory beside the Python modules. Unlike a
    # ``data-files`` path, it follows --target, --user, virtualenv and other
    # installation schemes automatically. PyInstaller also reconstructs it
    # below ``_MEIPASS/quotadeck`` through ``collect_data_files``.
    packaged_theme = bundled_theme_dir()
    if packaged_theme.is_dir():
        return packaged_theme

    # Keep compatibility with an older executable layout while preferring the
    # package-data location for all newly built executables.
    if getattr(sys, "_MEIPASS", None):
        legacy_executable_theme = Path(sys._MEIPASS) / "themes" / "quotadeck-crew"
        if legacy_executable_theme.is_dir():
            return legacy_executable_theme

    # Return the intended package path so a damaged installation reports a
    # useful missing-theme location instead of silently consulting another
    # interpreter's global sysconfig prefix.
    return packaged_theme


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
    metric_mode: MetricMode = MetricMode.QUOTA
    cumulative_period: UsagePeriod = field(default_factory=_default_cumulative_period)
    cost_currency: CostCurrency = field(default_factory=_default_cost_currency)
    usd_to_krw_rate: float = DEFAULT_USD_TO_KRW_RATE
    theme: str = "quotadeck-crew"
    poll_seconds: int = 60
    scene_hold_seconds: int = DEFAULT_SCENE_HOLD_SECONDS
    min_upload_minutes: int = 10
    ui_language: str = "ko"
    max_age_minutes: int = 60
    daily_flash_limit: int = 100
    frame_budget: int = 32
    launch_at_startup: bool = False
    config_version: int = CONFIG_VERSION

    def __post_init__(self) -> None:
        self.cumulative_period = _cumulative_period(self.cumulative_period)
        self.cost_currency = _cost_currency(self.cost_currency)
        self.min_upload_minutes = clamp_min_upload_minutes(self.min_upload_minutes)
        self.usd_to_krw_rate = clamp_usd_to_krw_rate(self.usd_to_krw_rate)

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
    value = int(raw.get("scene_hold_seconds") or DEFAULT_SCENE_HOLD_SECONDS)
    # An old default and an explicitly chosen value are indistinguishable in a
    # legacy JSON file. Preserve every present value; only new/missing settings
    # receive the new five-second default.
    return max(2, min(20, value))

def _account_from_dict(raw_account: dict) -> AccountConfig:
    allowed = {field.name for field in fields(AccountConfig)}
    return AccountConfig(**{key: value for key, value in raw_account.items() if key in allowed})


def _display_mode(value: object) -> DisplayMode:
    try:
        return DisplayMode(str(value))
    except ValueError:
        return DisplayMode.SMART


def _metric_mode(value: object) -> MetricMode:
    """Migrate legacy/malformed settings without making the UI unstartable."""

    try:
        return MetricMode(str(value))
    except ValueError:
        return MetricMode.QUOTA


def _cumulative_period(value: object) -> UsagePeriod:
    """Migrate missing/malformed period preferences to the new monthly view."""

    from quotadeck.usage.models import UsagePeriod

    try:
        return value if isinstance(value, UsagePeriod) else UsagePeriod(str(value))
    except ValueError:
        return UsagePeriod.MONTHLY


def _cost_currency(value: object) -> CostCurrency:
    """Migrate missing/malformed currency preferences to Korean won."""

    from quotadeck.usage.models import CostCurrency

    try:
        return value if isinstance(value, CostCurrency) else CostCurrency(str(value))
    except ValueError:
        return CostCurrency.KRW

def load_config(path: Path | None = None) -> AppConfig:
    target = path or default_config_path()
    if not target.exists():
        return AppConfig()
    raw = json.loads(target.read_text(encoding="utf-8"))
    accounts = [_account_from_dict(item) for item in raw.get("accounts", [])]
    mode = _display_mode(raw.get("display_mode", DisplayMode.SMART.value))
    # Configs through v3 did not contain metric_mode. They continue to show
    # remaining quota until the user deliberately selects cumulative usage.
    metric_mode = _metric_mode(raw.get("metric_mode", MetricMode.QUOTA.value))
    return AppConfig(
        accounts=accounts,
        display_mode=mode,
        metric_mode=metric_mode,
        cumulative_period=_cumulative_period(raw.get("cumulative_period", "monthly")),
        cost_currency=_cost_currency(raw.get("cost_currency", "krw")),
        usd_to_krw_rate=clamp_usd_to_krw_rate(
            raw.get("usd_to_krw_rate", DEFAULT_USD_TO_KRW_RATE)
        ),
        theme=raw.get("theme", "quotadeck-crew"),
        poll_seconds=int(raw.get("poll_seconds", 60)),
        scene_hold_seconds=_hold_seconds(raw),
        min_upload_minutes=clamp_min_upload_minutes(raw.get("min_upload_minutes", 10)),
        max_age_minutes=int(raw.get("max_age_minutes", 60)),
        daily_flash_limit=int(raw.get("daily_flash_limit", 100)),
        frame_budget=int(raw.get("frame_budget", 32)),
        launch_at_startup=bool(raw.get("launch_at_startup", False)),
        ui_language="en" if raw.get("ui_language") == "en" else "ko",
        config_version=CONFIG_VERSION,
    )

def save_config(config: AppConfig, path: Path | None = None) -> Path:
    target = path or default_config_path()
    payload = asdict(config)
    payload["display_mode"] = config.display_mode.value
    payload["metric_mode"] = config.metric_mode.value
    payload["cumulative_period"] = config.cumulative_period.value
    payload["cost_currency"] = config.cost_currency.value
    payload["usd_to_krw_rate"] = clamp_usd_to_krw_rate(config.usd_to_krw_rate)
    payload["config_version"] = CONFIG_VERSION
    target.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return target
