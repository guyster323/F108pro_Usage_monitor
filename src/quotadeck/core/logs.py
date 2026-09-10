from __future__ import annotations

import logging
import logging.handlers
import sys

_CONFIGURED = False


def setup_logging(*, console: bool = False) -> None:
    """Attach a shared rotating file log for both GUI and CLI runs.

    The EXE is packaged with console=False, so file logging is the only way to
    diagnose tray-resident behaviour. Secrets must be masked by callers before
    logging; structured secret fields are never logged here.
    """
    global _CONFIGURED
    if _CONFIGURED:
        return
    _CONFIGURED = True
    from quotadeck.config import app_dir

    logger = logging.getLogger("quotadeck")
    logger.setLevel(logging.INFO)
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    try:
        handler = logging.handlers.RotatingFileHandler(
            app_dir() / "logs" / "quotadeck.log",
            maxBytes=1_000_000,
            backupCount=3,
            encoding="utf-8",
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)
    except OSError:
        pass
    if console:
        stream = logging.StreamHandler(sys.stderr)
        stream.setFormatter(formatter)
        logger.addHandler(stream)
