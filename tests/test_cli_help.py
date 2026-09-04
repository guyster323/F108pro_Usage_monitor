from quotadeck.cli import main


def test_help_exits_zero() -> None:
    assert main([]) == 0
