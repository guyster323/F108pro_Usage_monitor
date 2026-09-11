from __future__ import annotations

import sys

from quotadeck.diagnostics import configure_diagnostics


_ui_bootstrap = bool(getattr(sys, "frozen", False)) or (len(sys.argv) > 1 and sys.argv[1] == "ui")
_session = None
if _ui_bootstrap:
    # Start the file logger before importing the CLI/PySide dependency graph.
    # A broken frozen import would otherwise disappear with console=False.
    _session = configure_diagnostics(
        "ui",
        console=sys.stderr is not None and not bool(getattr(sys, "frozen", False)),
    )

try:
    from quotadeck.cli import main
except BaseException:
    if _session is not None:
        _session.exception("bootstrap_import_failed")
    raise


def entrypoint() -> int:
    return main()


if __name__ == "__main__":
    raise SystemExit(entrypoint())
