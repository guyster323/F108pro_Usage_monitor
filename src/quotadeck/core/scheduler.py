from __future__ import annotations

import hashlib
import json
import logging
import time
from contextlib import nullcontext
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from quotadeck.config import (
    AppConfig,
    default_flash_state_path,
    default_theme_dir,
    load_config,
)
from quotadeck.core.flashbudget import (
    FlashBudget,
    FlashStateLockError,
    lock_flash_state,
)
from quotadeck.core.models import (
    AccountRef,
    DisplayMode,
    MetricMode,
    Severity,
    UsageSnapshot,
    display_windows,
    normalize_remaining,
)
from quotadeck.core.severity import RESET_HOLD, detect_reset, snapshot_severity
from quotadeck.devices.aula_f108.device import F108Device
from quotadeck.devices.aula_f108.payload import Frame
from quotadeck.discovery.accounts import select_accounts
from quotadeck.providers.base import all_providers
from quotadeck.renderer.encode import write_gif
from quotadeck.renderer.canvas import usage_bar_boundary, usage_percent_label
from quotadeck.renderer.layout import (
    USAGE_BAR,
    compact_cost_pair,
    compact_token_pair,
    cumulative_bar_caption,
)
from quotadeck.renderer.scenes import (
    _order_cumulative,
    render_cumulative_playlist,
    render_playlist,
)
from quotadeck.renderer.sprites import load_theme
from quotadeck.usage.display import CumulativeSnapshot
from quotadeck.usage.models import CostCurrency
from quotadeck.usage.service import CumulativeUsageService
log = logging.getLogger("quotadeck")

DisplaySnapshot = UsageSnapshot | CumulativeSnapshot


def _quota_bucket(value: object) -> int | None:
    remaining = normalize_remaining(value)
    return None if remaining is None else int(remaining // 5) * 5


def _reset_utc(value: object) -> datetime | None:
    """Normalize provider reset values before cross-window comparisons."""

    if not isinstance(value, datetime):
        return None
    try:
        offset = value.utcoffset()
        if value.tzinfo is None or offset is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    except (OSError, OverflowError, TypeError, ValueError):
        return None


def render_hash(
    snapshots: list[UsageSnapshot],
    severities: dict[str, Severity],
    *,
    theme: str,
    mode: str,
    hold_seconds: int = 5,
    frame_budget: int = 32,
) -> str:
    rows = []
    for snap in snapshots:
        remaining = snap.critical_remaining
        bucket = None if remaining is None else int(remaining // 5) * 5
        reset_bucket = None
        times = [
            normalized
            for window in snap.windows
            if (normalized := _reset_utc(window.resets_at)) is not None
        ]
        if times:
            soonest = min(times)
            reset_bucket = int(soonest.timestamp() // 1800)
        rows.append(
            {
                "k": snap.key,
                "a": snap.display_name,
                "s": severities.get(snap.key, Severity.STALE).value,
                "b": bucket,
                "w": [
                    {
                        "id": window.id,
                        "label": window.label,
                        "b": _quota_bucket(window.remaining_percent),
                    }
                    for window in display_windows(snap)
                ],
                "r": reset_bucket,
                "st": snap.status,
            }
        )
    payload = json.dumps(
        {
            "rows": rows,
            "theme": theme,
            "mode": mode,
            "hold": hold_seconds,
            "frame_budget": frame_budget,
        },
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def cumulative_render_hash(
    snapshots: list[CumulativeSnapshot],
    *,
    theme: str,
    mode: str,
    hold_seconds: int = 5,
    frame_budget: int = 32,
    currency: CostCurrency = CostCurrency.USD,
    usd_to_krw_rate: Decimal | int | float | str = 1400,
) -> str:
    """Hash only values that can change a cumulative LCD card."""

    try:
        selected_currency = (
            currency
            if isinstance(currency, CostCurrency)
            else CostCurrency(str(currency))
        )
    except (TypeError, ValueError):
        selected_currency = CostCurrency.USD

    try:
        canonical_rate = Decimal(str(usd_to_krw_rate))
    except (InvalidOperation, TypeError, ValueError):
        canonical_rate = Decimal(0)
    if not canonical_rate.is_finite() or canonical_rate <= 0:
        canonical_rate = Decimal(0)

    ordered = _order_cumulative(snapshots, DisplayMode(mode))
    rows = []
    for snap in ordered:
        ratio = snap.ratio
        try:
            percent = None if ratio is None else float(ratio) * 100.0
        except (TypeError, ValueError, OverflowError):
            percent = None
        rows.append(
            {
                "k": snap.key,
                "a": snap.display_name,
                "period": snap.period.value,
                "caption": cumulative_bar_caption(snap, selected_currency),
                "tokens": compact_token_pair(snap.this_tokens, snap.average_tokens),
                "ratio": usage_percent_label(percent),
                "bar_boundary": usage_bar_boundary(USAGE_BAR, percent),
                "cost": compact_cost_pair(
                    snap.this_cost_usd,
                    snap.average_cost_usd,
                    currency=selected_currency,
                    usd_to_krw_rate=canonical_rate,
                ),
                "sprite": snap.sprite_state,
                "severity": snap.severity.value,
            }
        )
    payload = json.dumps(
        {
            "rows": rows,
            "theme": theme,
            "mode": mode,
            "metric": "cumulative",
            "hold": hold_seconds,
            "frame_budget": frame_budget,
        },
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
@dataclass
class SchedulerState:
    previous: dict[str, UsageSnapshot] = field(default_factory=dict)
    severity: dict[str, Severity] = field(default_factory=dict)
    reset_until: dict[str, datetime] = field(default_factory=dict)
    last_hash: str | None = None
    last_frames: list[Frame] = field(default_factory=list)
    last_polled: list[DisplaySnapshot] = field(default_factory=list)

class QuotaDeckRuntime:
    def __init__(
        self,
        config: AppConfig | None = None,
        *,
        mock: bool = False,
        flash_state_path: str | Path | None = None,
    ) -> None:
        self.config = config or load_config()
        self.mock = mock
        self.state = SchedulerState()
        if flash_state_path is not None:
            self.flash_state_path: Path | None = Path(flash_state_path)
        elif mock:
            self.flash_state_path = None
        else:
            self.flash_state_path = default_flash_state_path()
        self.budget = FlashBudget(
            min_interval=timedelta(minutes=self.config.min_upload_minutes),
            max_age=timedelta(minutes=self.config.max_age_minutes),
            daily_limit=self.config.daily_flash_limit,
        )
        if self.flash_state_path is not None:
            self.budget.restore(self.flash_state_path)
        self.providers = {p.id: p for p in all_providers()}
        self.cumulative = CumulativeUsageService(
            cache_seconds=max(15, min(60, self.config.poll_seconds)),
        )
    def selected_accounts(self) -> list[AccountRef]:
        started = time.monotonic()
        log.info("event=account_selection_start providers=%d", len(self.providers))
        discovered = []
        for provider in self.providers.values():
            try:
                discovered.extend(provider.discover())
            except Exception:
                # One damaged local credential store must not suppress every
                # other provider from the LCD or settings preview.
                log.exception(
                    "event=provider_discovery_failed provider=%s",
                    provider.id,
                )
        selected = select_accounts(discovered, self.config)
        log.info(
            "event=account_selection_complete discovered=%d selected=%d duration_ms=%d",
            len(discovered),
            len(selected),
            int((time.monotonic() - started) * 1000),
        )
        return selected
    def poll(self) -> list[DisplaySnapshot]:
        started = time.monotonic()
        log.info("event=provider_poll_start")
        if self.config.metric_mode is MetricMode.CUMULATIVE:
            snapshots = self.cumulative.snapshots(
                self.selected_accounts(),
                period=self.config.cumulative_period,
            )
            for snap in snapshots:
                self.state.severity[snap.key] = snap.severity
            self._retain_active_accounts({snap.key for snap in snapshots})
            self.state.last_polled = list(snapshots)
            log.info(
                "event=provider_poll_complete accounts=%d duration_ms=%d metric=cumulative",
                len(snapshots),
                int((time.monotonic() - started) * 1000),
            )
            return list(snapshots)

        snapshots: list[UsageSnapshot] = []
        now = datetime.now(timezone.utc)
        for account in self.selected_accounts():
            provider = self.providers[account.provider]
            fetch_started = time.monotonic()
            log.info("event=provider_fetch_start provider=%s", account.provider)
            try:
                snap = provider.fetch(account)
            except Exception:
                log.exception(
                    "event=provider_fetch_failed provider=%s duration_ms=%d",
                    account.provider,
                    int((time.monotonic() - fetch_started) * 1000),
                )
                # Keep the remaining providers visible when one integration
                # has a damaged response, credential store, or local runtime.
                snap = UsageSnapshot(
                    provider=account.provider,
                    account_id=account.account_id,
                    display_name=account.display_name,
                    plan=account.plan,
                    windows=[],
                    status="error",
                    fetched_at=now,
                    error="Provider usage could not be fetched.",
                    source_path=account.source_path,
                )
            log.info(
                "event=provider_fetch_complete provider=%s duration_ms=%d status=%s windows=%d",
                account.provider,
                int((time.monotonic() - fetch_started) * 1000),
                snap.status,
                len(snap.windows),
            )
            snap.display_name = self.config.alias_for(account.provider, account.account_id, snap.display_name)
            prev = self.state.previous.get(snap.key)
            if detect_reset(prev, snap):
                self.state.reset_until[snap.key] = now + RESET_HOLD
            sev = snapshot_severity(snap, self.state.severity.get(snap.key), self.state.reset_until.get(snap.key))
            self.state.severity[snap.key] = sev
            self.state.previous[snap.key] = snap
            snapshots.append(snap)
        self._retain_active_accounts({snap.key for snap in snapshots})
        self.state.last_polled = list(snapshots)
        log.info(
            "event=provider_poll_complete accounts=%d duration_ms=%d",
            len(snapshots),
            int((time.monotonic() - started) * 1000),
        )
        return snapshots

    def _retain_active_accounts(self, active_keys: set[str]) -> None:
        """Drop status from accounts no longer returned by discovery/selection."""

        for mapping in (
            self.state.previous,
            self.state.severity,
            self.state.reset_until,
        ):
            for key in set(mapping) - active_keys:
                del mapping[key]

    @staticmethod
    def _cumulative_snapshots(
        snapshots: list[DisplaySnapshot],
    ) -> list[CumulativeSnapshot]:
        if any(not isinstance(item, CumulativeSnapshot) for item in snapshots):
            raise TypeError("cumulative mode received a quota snapshot")
        return [item for item in snapshots if isinstance(item, CumulativeSnapshot)]

    @staticmethod
    def _quota_snapshots(snapshots: list[DisplaySnapshot]) -> list[UsageSnapshot]:
        if any(not isinstance(item, UsageSnapshot) for item in snapshots):
            raise TypeError("quota mode received a cumulative snapshot")
        return [item for item in snapshots if isinstance(item, UsageSnapshot)]

    def build_frames(self, snapshots: list[DisplaySnapshot]) -> list[Frame]:
        started = time.monotonic()
        log.info("event=render_start accounts=%d", len(snapshots))
        theme_path = default_theme_dir()
        theme = load_theme(theme_path)
        if self.config.metric_mode is MetricMode.CUMULATIVE:
            frames = render_cumulative_playlist(
                self._cumulative_snapshots(snapshots),
                theme,
                mode=self.config.display_mode,
                frame_budget=self.config.frame_budget,
                hold_ms=self.config.scene_hold_seconds * 1000,
                currency=self.config.cost_currency,
                usd_to_krw_rate=self.config.usd_to_krw_rate,
            )
        else:
            frames = render_playlist(
                self._quota_snapshots(snapshots),
                self.state.severity,
                theme,
                mode=self.config.display_mode,
                frame_budget=self.config.frame_budget,
                hold_ms=self.config.scene_hold_seconds * 1000,
            )
        log.info(
            "event=render_complete accounts=%d frames=%d duration_ms=%d",
            len(snapshots),
            len(frames),
            int((time.monotonic() - started) * 1000),
        )
        return frames
    def maybe_upload(self, snapshots: list[DisplaySnapshot], *, force: bool = False) -> str:
        if self.config.metric_mode is MetricMode.CUMULATIVE:
            digest = cumulative_render_hash(
                self._cumulative_snapshots(snapshots),
                theme=self.config.theme,
                mode=self.config.display_mode.value,
                hold_seconds=self.config.scene_hold_seconds,
                frame_budget=self.config.frame_budget,
                currency=self.config.cost_currency,
                usd_to_krw_rate=self.config.usd_to_krw_rate,
            )
        else:
            digest = render_hash(
                self._quota_snapshots(snapshots),
                self.state.severity,
                theme=self.config.theme,
                mode=self.config.display_mode.value,
                hold_seconds=self.config.scene_hold_seconds,
                frame_budget=self.config.frame_budget,
            )
        transaction = (
            lock_flash_state(self.flash_state_path)
            if self.flash_state_path is not None
            else nullcontext()
        )
        try:
            with transaction:
                # Runtime instances may have been created from the same stale
                # state. Reload only after taking the interprocess lock so the
                # daily cap and cooldown decision use the latest committed row.
                if (
                    self.flash_state_path is not None
                    and self.flash_state_path.exists()
                    and not self.budget.restore(self.flash_state_path)
                ):
                    return "deferred: flash state is unreadable"
                return self._maybe_upload_locked(snapshots, digest=digest, force=force)
        except FlashStateLockError as exc:
            return f"deferred: {exc}"

    def _maybe_upload_locked(
        self,
        snapshots: list[DisplaySnapshot],
        *,
        digest: str,
        force: bool,
    ) -> str:
        """Perform one decision/write transaction while the state lock is held."""

        now = datetime.now(timezone.utc)
        changed = digest != self.state.last_hash
        aged = self.budget.stale(now)
        log.info(
            "event=upload_decision changed=%s aged=%s force=%s mock=%s",
            changed,
            aged,
            force,
            self.mock,
        )
        if not changed and not aged and not force:
            return "unchanged"
        # Staleness requests a refresh but must not bypass the flash cooldown.
        # Only an explicit user action may force an early rewrite.
        allowed, reason = self.budget.can_upload(now, force=force)
        if not allowed:
            return f"deferred: {reason}"
        frames = self.build_frames(snapshots)
        self.state.last_frames = frames

        # Persist a conservative write-ahead reservation before touching the
        # device. A crash or partial transfer may already wear flash; retaining
        # that count fails safe and prevents restart-based cap bypasses.
        previous_budget = (
            self.budget.last_upload,
            self.budget.uploads_today,
            self.budget.day,
        )
        self.budget.record(now)
        if self.flash_state_path is not None:
            try:
                self.budget.persist(self.flash_state_path)
            except (OSError, ValueError, TypeError):
                (
                    self.budget.last_upload,
                    self.budget.uploads_today,
                    self.budget.day,
                ) = previous_budget
                log.warning(
                    "could not reserve flash wear state; upload blocked",
                    exc_info=True,
                )
                return "deferred: flash state could not be reserved"
        if self.mock:
            out = Path.cwd() / "preview.gif"
            write_gif(frames, out)
            self.state.last_hash = digest
            return f"preview {out} ({len(frames)} frames)"
        log.info("event=keyboard_session_start frames=%d", len(frames))
        with F108Device() as device:
            device.upload_frames(frames, progress=_log_progress)
        log.info("event=keyboard_session_complete frames=%d", len(frames))
        self.state.last_hash = digest
        return f"uploaded {len(frames)} frames"
    def tick(self, *, force: bool = False) -> str:
        snapshots = self.poll()
        return self.maybe_upload(snapshots, force=force)
    def run_forever(self) -> None:
        log.info("QuotaDeck scheduler started")
        while True:
            try:
                result = self.tick()
                log.info(result)
            except KeyboardInterrupt:
                raise
            except Exception:
                log.exception("tick failed")
            time.sleep(max(15, self.config.poll_seconds))

def _log_progress(current: int, total: int, phase: str) -> None:
    if current == total or current % 25 == 0:
        log.info("event=keyboard_upload_progress phase=%s current=%d total=%d", phase, current, total)
