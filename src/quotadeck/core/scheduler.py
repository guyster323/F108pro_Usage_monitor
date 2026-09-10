from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from quotadeck.config import AppConfig, default_theme_dir, load_config
from quotadeck.core.flashbudget import FlashBudget
from quotadeck.core.models import AccountRef, DisplayMode, Severity, UsageSnapshot
from quotadeck.core.severity import RESET_HOLD, detect_reset, snapshot_severity
from quotadeck.core.statefile import PersistedState, default_state_path, load_state, save_state
from quotadeck.devices.aula_f108.device import F108Device
from quotadeck.devices.aula_f108.payload import Frame
from quotadeck.discovery.accounts import select_accounts
from quotadeck.providers.base import all_providers
from quotadeck.renderer.encode import write_gif
from quotadeck.renderer.scenes import render_playlist
from quotadeck.renderer.sprites import load_theme

log = logging.getLogger("quotadeck")


def render_hash(
    snapshots: list[UsageSnapshot],
    severities: dict[str, Severity],
    *,
    theme: str,
    mode: str,
    hold_seconds: int = 10,
) -> str:
    rows = []
    for snap in snapshots:
        remaining = snap.critical_remaining
        bucket = None if remaining is None else int(remaining // 5) * 5
        reset_bucket = None
        times = [w.resets_at for w in snap.windows if w.resets_at]
        if times:
            soonest = min(times)
            reset_bucket = int(soonest.timestamp() // 1800)
        # The layout renders the first two windows individually, so each
        # displayed window contributes its own 5% bucket. Relying only on the
        # minimum window would miss visible changes in the other window.
        shown = [
            {
                "id": w.id,
                "l": w.label,
                "b": int(w.remaining_percent // 5) * 5,
            }
            for w in snap.windows[:2]
        ]
        rows.append(
            {
                "k": snap.key,
                "a": snap.display_name,
                "s": severities.get(snap.key, Severity.STALE).value,
                "b": bucket,
                "w": shown,
                "r": reset_bucket,
                "st": snap.status,
            }
        )
    payload = json.dumps(
        {"rows": rows, "theme": theme, "mode": mode, "hold": hold_seconds},
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


@dataclass
class SchedulerState:
    previous: dict[str, UsageSnapshot] = field(default_factory=dict)
    severity: dict[str, Severity] = field(default_factory=dict)
    reset_until: dict[str, datetime] = field(default_factory=dict)
    last_errors: dict[str, str] = field(default_factory=dict)
    last_success: dict[str, datetime] = field(default_factory=dict)
    last_poll: datetime | None = None
    last_hash: str | None = None
    last_frames: list[Frame] = field(default_factory=list)


@dataclass
class UploadResult:
    """Structured outcome of one upload attempt (or deliberate skip)."""

    uploaded: bool
    code: str  # uploaded | preview | unchanged | cooldown | daily_limit | no_accounts | device_offline | device_busy | device_error
    detail: str = ""
    next_allowed: datetime | None = None
    frames: int = 0

    def message(self) -> str:
        if self.code == "uploaded":
            return f"uploaded {self.frames} frames"
        if self.code == "preview":
            return f"preview {self.detail} ({self.frames} frames)"
        if self.code == "unchanged":
            return "unchanged"
        if self.code == "device_error":
            return f"upload failed: {self.detail}"
        return f"deferred: {self.detail or self.code}"


class QuotaDeckRuntime:
    def __init__(
        self,
        config: AppConfig | None = None,
        *,
        mock: bool = False,
        state_path: Path | None = None,
    ) -> None:
        self.config = config or load_config()
        self.mock = mock
        self.state = SchedulerState()
        self.budget = FlashBudget(
            min_interval=timedelta(minutes=self.config.min_upload_minutes),
            max_age=timedelta(minutes=self.config.max_age_minutes),
            daily_limit=self.config.daily_flash_limit,
        )
        self.providers = {p.id: p for p in all_providers()}
        # Persist flash budget across restarts so relaunching the app cannot
        # bypass the daily limit or the minimum write interval. Mock runs stay
        # off-disk unless a path is provided explicitly (tests).
        self._state_path: Path | None = state_path if state_path is not None else (None if mock else default_state_path())
        if self._state_path is not None:
            persisted = load_state(self._state_path)
            self.budget.last_upload = persisted.last_upload
            self.budget.uploads_today = persisted.uploads_today
            self.budget.day = persisted.day
            self.state.last_hash = persisted.last_hash

    def update_config(self, config: AppConfig) -> None:
        """Apply new settings without discarding budget/history state."""
        self.config = config
        self.budget.min_interval = timedelta(minutes=config.min_upload_minutes)
        self.budget.max_age = timedelta(minutes=config.max_age_minutes)
        self.budget.daily_limit = config.daily_flash_limit
        if config.accounts:
            keys = set(config.enabled_keys())
            for table in (
                self.state.previous,
                self.state.severity,
                self.state.reset_until,
                self.state.last_errors,
                self.state.last_success,
            ):
                for key in [k for k in table if k not in keys]:
                    del table[key]

    def _persist(self) -> None:
        if self._state_path is None:
            return
        try:
            save_state(
                PersistedState(
                    last_upload=self.budget.last_upload,
                    uploads_today=self.budget.uploads_today,
                    day=self.budget.day,
                    last_hash=self.state.last_hash,
                ),
                self._state_path,
            )
        except Exception:
            log.exception("failed to persist runtime state")

    def selected_accounts(self) -> list[AccountRef]:
        discovered = []
        for provider in self.providers.values():
            try:
                discovered.extend(provider.discover())
            except Exception:
                log.exception("discovery failed for provider %s", provider.id)
        return select_accounts(discovered, self.config)

    def poll(self) -> list[UsageSnapshot]:
        snapshots: list[UsageSnapshot] = []
        now = datetime.now(timezone.utc)
        for account in self.selected_accounts():
            provider = self.providers[account.provider]
            try:
                snap = provider.fetch(account)
            except Exception as exc:
                # Isolate one account's unexpected failure so the remaining
                # accounts keep refreshing. Keep the last good snapshot around.
                log.exception("fetch failed for %s", account.key)
                self.state.last_errors[account.key] = str(exc)
                snap = UsageSnapshot(
                    provider=account.provider,
                    account_id=account.account_id,
                    display_name=account.display_name,
                    plan=account.plan,
                    windows=[],
                    status="error",
                    fetched_at=now,
                    error=str(exc),
                    source_path=account.source_path,
                )
                snap.display_name = self.config.alias_for(account.provider, account.account_id, snap.display_name)
                self.state.severity[snap.key] = snapshot_severity(snap, self.state.severity.get(snap.key))
                snapshots.append(snap)
                continue
            snap.display_name = self.config.alias_for(account.provider, account.account_id, snap.display_name)
            prev = self.state.previous.get(snap.key)
            if detect_reset(prev, snap):
                self.state.reset_until[snap.key] = now + RESET_HOLD
            sev = snapshot_severity(snap, self.state.severity.get(snap.key), self.state.reset_until.get(snap.key))
            self.state.severity[snap.key] = sev
            if snap.status == "ok":
                self.state.previous[snap.key] = snap
                self.state.last_success[snap.key] = now
                self.state.last_errors.pop(snap.key, None)
            else:
                self.state.last_errors[snap.key] = snap.error or snap.status
            snapshots.append(snap)
        self.state.last_poll = now
        return snapshots

    def build_frames(self, snapshots: list[UsageSnapshot]) -> list[Frame]:
        theme_path = default_theme_dir()
        theme = load_theme(theme_path)
        return render_playlist(
            snapshots,
            self.state.severity,
            theme,
            mode=self.config.display_mode,
            frame_budget=self.config.frame_budget,
            hold_ms=self.config.scene_hold_seconds * 1000,
        )

    def maybe_upload(self, snapshots: list[UsageSnapshot], *, force: bool = False) -> UploadResult:
        if not snapshots:
            return UploadResult(uploaded=False, code="no_accounts", detail="no accounts to display")
        digest = render_hash(
            snapshots,
            self.state.severity,
            theme=self.config.theme,
            mode=self.config.display_mode.value,
            hold_seconds=self.config.scene_hold_seconds,
        )
        changed = digest != self.state.last_hash
        aged = self.budget.stale()
        if not changed and not aged and not force:
            return UploadResult(uploaded=False, code="unchanged", next_allowed=self.budget.next_allowed())
        # max_age only widens what counts as "worth re-checking"; it must not
        # bypass the minimum write interval or the daily flash limit.
        allowed, reason = self.budget.can_upload(force=force)
        if not allowed:
            code = "daily_limit" if reason.startswith("daily") else "cooldown"
            return UploadResult(
                uploaded=False,
                code=code,
                detail=reason,
                next_allowed=self.budget.next_allowed(),
            )
        frames = self.build_frames(snapshots)
        self.state.last_frames = frames
        if self.mock:
            out = Path.cwd() / "preview.gif"
            write_gif(frames, out)
            self.budget.record()
            self.state.last_hash = digest
            self._persist()
            return UploadResult(uploaded=True, code="preview", detail=str(out), frames=len(frames))
        # Device checks never raise dialogs here: polling keeps running and
        # only the LCD write is deferred when the keyboard is unavailable.
        from quotadeck.devices.aula_f108.device import aula_software_running, enumerate_interfaces, wired_mode_ok

        if not wired_mode_ok(enumerate_interfaces()):
            return UploadResult(uploaded=False, code="device_offline", detail="keyboard not in wired mode")
        running = aula_software_running()
        if running:
            return UploadResult(uploaded=False, code="device_busy", detail=", ".join(running))
        try:
            with F108Device() as device:
                device.upload_frames(frames, progress=_log_progress)
        except Exception as exc:
            log.exception("LCD upload failed")
            return UploadResult(uploaded=False, code="device_error", detail=str(exc))
        self.budget.record()
        self.state.last_hash = digest
        self._persist()
        return UploadResult(uploaded=True, code="uploaded", frames=len(frames))

    def tick(self, *, force: bool = False) -> str:
        return self.tick_result(force=force).message()

    def tick_result(self, *, force: bool = False) -> UploadResult:
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
        log.info("%s %s/%s", phase, current, total)
