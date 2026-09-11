from __future__ import annotations

import argparse

from quotadeck import cli
from quotadeck.config import AppConfig
from quotadeck.core.flashbudget import FlashBudget


def test_raw_cli_upload_uses_shared_daily_cap(
    tmp_path,
    monkeypatch,
) -> None:
    state_path = tmp_path / "flash-state.json"
    device_writes: list[int] = []
    monkeypatch.setattr(cli, "default_flash_state_path", lambda: state_path)
    monkeypatch.setattr(
        cli,
        "load_config",
        lambda: AppConfig(daily_flash_limit=1),
    )
    monkeypatch.setattr(cli, "aula_software_running", lambda: [])

    class SuccessfulDevice:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def upload_frames(self, frames, progress):
            _ = progress
            device_writes.append(len(frames))

    monkeypatch.setattr(
        "quotadeck.devices.aula_f108.device.F108Device",
        SuccessfulDevice,
    )
    args = argparse.Namespace(solid="000000", gif=None, mock=False)

    assert cli.cmd_upload(args) == 0
    assert cli.cmd_upload(args) == 1
    assert device_writes == [1]
    restored = FlashBudget()
    assert restored.restore(state_path)
    assert restored.uploads_today == 1
