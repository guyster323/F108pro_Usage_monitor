from __future__ import annotations

import json
from pathlib import Path

from quotadeck.config import AppConfig, load_config, save_config


def test_corrupt_config_falls_back_to_defaults(tmp_path: Path) -> None:
    target = tmp_path / "config.json"
    target.write_text("{broken json", encoding="utf-8")
    config = load_config(target)
    assert config.poll_seconds == 60
    assert config.accounts == []
    assert not target.exists()
    assert target.with_suffix(".json.bad").exists()


def test_wrong_root_type_falls_back(tmp_path: Path) -> None:
    target = tmp_path / "config.json"
    target.write_text(json.dumps([1, 2, 3]), encoding="utf-8")
    config = load_config(target)
    assert config.poll_seconds == 60


def test_out_of_range_values_are_clamped(tmp_path: Path) -> None:
    target = tmp_path / "config.json"
    target.write_text(
        json.dumps(
            {
                "poll_seconds": 1,
                "scene_hold_seconds": 9999,
                "min_upload_minutes": 0,
                "daily_flash_limit": -5,
                "frame_budget": "not-a-number",
                "display_mode": "bogus",
            }
        ),
        encoding="utf-8",
    )
    config = load_config(target)
    assert config.poll_seconds == 15
    assert config.scene_hold_seconds == 20
    assert config.min_upload_minutes == 1
    assert config.daily_flash_limit == 1
    assert config.frame_budget == 32
    assert config.display_mode.value == "smart"


def test_malformed_accounts_are_skipped(tmp_path: Path) -> None:
    target = tmp_path / "config.json"
    target.write_text(
        json.dumps(
            {
                "accounts": [
                    {"provider": "codex", "account_id": "a", "alias": "A"},
                    {"alias": "no provider"},
                    "not a dict",
                ]
            }
        ),
        encoding="utf-8",
    )
    config = load_config(target)
    assert len(config.accounts) == 1
    assert config.accounts[0].provider == "codex"


def test_save_is_atomic_and_roundtrips(tmp_path: Path) -> None:
    target = tmp_path / "config.json"
    config = AppConfig(poll_seconds=90, min_upload_minutes=20)
    save_config(config, target)
    assert target.exists()
    assert not target.with_suffix(".json.tmp").exists()
    loaded = load_config(target)
    assert loaded.poll_seconds == 90
    assert loaded.min_upload_minutes == 20
