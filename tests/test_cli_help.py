import argparse

import pytest

from quotadeck.cli import _account_seconds, main


def test_help_exits_zero() -> None:
    assert main([]) == 0


def test_render_help_includes_account_hold(capsys) -> None:
    try:
        main(["render", "--help"])
    except SystemExit as exc:
        assert exc.code == 0
    output = capsys.readouterr().out
    assert "--hold-seconds" in output
    assert "--mode {smart,fixed}" in output


@pytest.mark.parametrize("value", ["0", "-1", "nan", "inf", "20.01", "5.01"])
def test_render_hold_rejects_unsafe_values(value: str) -> None:
    with pytest.raises(argparse.ArgumentTypeError):
        _account_seconds(value)


def test_render_hold_accepts_firmware_ticks() -> None:
    assert _account_seconds("5") == 5.0
    assert _account_seconds("7.5") == 7.5
    assert _account_seconds("5.02") == 5.02
