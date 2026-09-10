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
from quotadeck.core.models import (
    AccountRef,
    DisplayMode,
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
from quotadeck.renderer.scenes import render_playlist
from quotadeck.renderer.sprites import load_theme
log = logging.getLogger("quotadeck")


def _quota_bucket(value: object) -> int | None:
    remaining = normalize_remaining(value)
    return None if remaining is None else int(remaining // 5) * 5


def render_hash(
    snapshots: list[UsageSnapshot],
    severities: dict[str, Severity],
    *,
    theme: str,
    mode: str,
    hold_seconds: int = 5,
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
        {"rows": rows, "theme": theme, "mode": mode, "hold": hold_seconds},
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

class QuotaDeckRuntime:
    def __init__(self, config: AppConfig | None = None, *, mock: bool = False) -> None:
        self.config = config or load_config()
        self.mock = mock
        self.state = SchedulerState()
        self.budget = FlashBudget(
            min_interval=timedelta(minutes=self.config.min_upload_minutes),
            max_age=timedelta(minutes=self.config.max_age_minutes),
            daily_limit=self.config.daily_flash_limit,
        )
        self.providers = {p.id: p for p in all_providers()}
    def selected_accounts(self) -> list[AccountRef]:
        discovered = []
        for provider in self.providers.values():
            discovered.extend(provider.discover())
        return select_accounts(discovered, self.config)
    def poll(self) -> list[UsageSnapshot]:
        snapshots: list[UsageSnapshot] = []
        now = datetime.now(timezone.utc)
        for account in self.selected_accounts():
            provider = self.providers[account.provider]
            snap = provider.fetch(account)
            snap.display_name = self.config.alias_for(account.provider, account.account_id, snap.display_name)
            prev = self.state.previous.get(snap.key)
            if detect_reset(prev, snap):
                self.state.reset_until[snap.key] = now + RESET_HOLD
            sev = snapshot_severity(snap, self.state.severity.get(snap.key), self.state.reset_until.get(snap.key))
            self.state.severity[snap.key] = sev
            self.state.previous[snap.key] = snap
            snapshots.append(snap)
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
    def maybe_upload(self, snapshots: list[UsageSnapshot], *, force: bool = False) -> str:
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
            return "unchanged"
        allowed, reason = self.budget.can_upload(force=force or aged)
        if not allowed:
            return f"deferred: {reason}"
        frames = self.build_frames(snapshots)
        self.state.last_frames = frames
        if self.mock:
            out = Path.cwd() / "preview.gif"
            write_gif(frames, out)
            self.budget.record()
            self.state.last_hash = digest
            return f"preview {out} ({len(frames)} frames)"
        with F108Device() as device:
            device.upload_frames(frames, progress=_log_progress)
        self.budget.record()
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
        log.info("%s %s/%s", phase, current, total)
