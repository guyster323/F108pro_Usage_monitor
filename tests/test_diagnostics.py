from __future__ import annotations

import io
import logging
import sys
from contextlib import redirect_stderr
from pathlib import Path

from quotadeck.core.mask import mask_text
from quotadeck.diagnostics import configure_diagnostics


def _combined_logs(directory: Path) -> str:
    return "\n".join(
        path.read_text(encoding="utf-8", errors="replace")
        for path in sorted(directory.glob("quotadeck-*.log*"))
    )


def test_mask_text_covers_persistent_log_secrets(monkeypatch) -> None:
    monkeypatch.setenv("USERPROFILE", r"C:\Users\PrivateName")
    raw = (
        "Authorization: Bearer opaque-secret-value "
        'access_token="access-secret-value" '
        "refreshToken=refresh-secret-value password=hunter-secret "
        "X-Api-Key: api-secret-value "
        "Cookie: session=private-cookie-value\n"
        "mail=user.name@example.com path=C:\\Users\\PrivateName\\app"
    )
    masked = mask_text(raw)
    for secret in (
        "opaque-secret-value",
        "access-secret-value",
        "refresh-secret-value",
        "hunter-secret",
        "api-secret-value",
        "private-cookie-value",
        "user.name",
        "PrivateName",
    ):
        assert secret not in masked
    assert "Authorization:" in masked
    assert "access_token=" in masked
    assert "…@example.com" in masked
    assert "%USERPROFILE%" in masked


def test_diagnostics_rotate_redact_and_close(tmp_path: Path) -> None:
    session = configure_diagnostics(
        "test",
        log_dir=tmp_path,
        max_bytes=512,
        backup_count=2,
        install_hooks=False,
        enable_faults=False,
        check_previous=False,
    )
    try:
        assert configure_diagnostics("test", log_dir=tmp_path) is session
        for index in range(80):
            session.event("rollover_probe", index=index, padding="x" * 80)
        session.event("unicode_probe", note="트레이", token="sk-private-secret-value")
        try:
            raise RuntimeError("Bearer opaque-private-secret")
        except RuntimeError:
            session.exception("traceback_probe")
        session.mark_shutdown("test_complete")
        session.flush()

        text = _combined_logs(tmp_path)
        assert "트레이" in text
        assert "sk-private-secret-value" not in text
        assert "opaque-private-secret" not in text
        assert "event=process_end clean=true" in text
        assert session.log_path.with_name(session.log_path.name + ".1").exists()
        assert not session.log_path.with_name(session.log_path.name + ".3").exists()
    finally:
        path = session.log_path
        session.close()

    # Closing releases the Windows file handle as well as the Python handler.
    moved = path.with_suffix(".moved")
    path.replace(moved)
    assert moved.exists()


def test_next_ui_session_reports_previous_unclean_log(tmp_path: Path) -> None:
    first = configure_diagnostics(
        "ui",
        log_dir=tmp_path,
        install_hooks=False,
        enable_faults=False,
        check_previous=False,
    )
    first.event("event_loop_started")
    first.flush()
    first.close()

    second = configure_diagnostics(
        "ui",
        log_dir=tmp_path,
        install_hooks=False,
        enable_faults=False,
        check_previous=True,
    )
    try:
        second.mark_shutdown("test_complete")
        second.flush()
        text = second.log_path.read_text(encoding="utf-8")
        assert "event=previous_ui_session_unclean" in text
        assert first.log_path.name in text
    finally:
        second.close()


def test_clean_ui_session_ignores_header_only_crash_log(tmp_path: Path) -> None:
    first = configure_diagnostics(
        "ui",
        log_dir=tmp_path,
        install_hooks=False,
        enable_faults=True,
        check_previous=False,
    )
    first.mark_shutdown("tray_quit")
    first.close()
    assert first.crash_path.exists()

    second = configure_diagnostics(
        "ui",
        log_dir=tmp_path,
        install_hooks=False,
        enable_faults=False,
        check_previous=True,
    )
    try:
        second.mark_shutdown("tray_quit")
        second.flush()
        text = second.log_path.read_text(encoding="utf-8")
        assert "event=previous_ui_session_unclean" not in text
    finally:
        second.close()


def test_clean_ui_marker_is_found_after_rollover(tmp_path: Path) -> None:
    first = configure_diagnostics(
        "ui",
        log_dir=tmp_path,
        max_bytes=512,
        backup_count=2,
        install_hooks=False,
        enable_faults=False,
        check_previous=False,
    )
    first.mark_shutdown("tray_quit")
    # Simulate a few late Qt teardown messages moving process_end into .1.
    for index in range(20):
        first.event("late_qt_teardown", index=index, padding="x" * 80)
        if (
            first.log_path.with_name(first.log_path.name + ".1").exists()
            and "event=process_end" not in first.log_path.read_text(encoding="utf-8")
        ):
            break
    assert "event=process_end clean=true" in _combined_logs(tmp_path)
    first.close()

    second = configure_diagnostics(
        "ui",
        log_dir=tmp_path,
        install_hooks=False,
        enable_faults=False,
        check_previous=True,
    )
    try:
        second.mark_shutdown("tray_quit")
        second.flush()
        text = second.log_path.read_text(encoding="utf-8")
        assert "event=previous_ui_session_unclean" not in text
    finally:
        second.close()


def test_diagnostics_uses_one_owned_file_handler(tmp_path: Path) -> None:
    session = configure_diagnostics(
        "test",
        log_dir=tmp_path,
        install_hooks=False,
        enable_faults=False,
        check_previous=False,
    )
    try:
        configure_diagnostics("test", log_dir=tmp_path)
        # FileHandler derives from StreamHandler, so check by path capability.
        owned = [
            handler
            for handler in logging.getLogger("quotadeck").handlers
            if getattr(handler, "_quotadeck_owned", False) and hasattr(handler, "baseFilename")
        ]
        assert len(owned) == 1
    finally:
        session.mark_shutdown("test_complete")
        session.close()


def test_unhandled_exception_hook_does_not_echo_raw_secret(tmp_path: Path) -> None:
    session = configure_diagnostics(
        "test",
        log_dir=tmp_path,
        console=False,
        install_hooks=True,
        enable_faults=False,
        check_previous=False,
    )
    redirected = io.StringIO()
    try:
        try:
            raise RuntimeError("Bearer raw-hook-secret-value")
        except RuntimeError:
            exc_type, exc_value, traceback = sys.exc_info()
            with redirect_stderr(redirected):
                sys.excepthook(exc_type, exc_value, traceback)
        session.mark_shutdown("test_complete")
        session.flush()
        combined = _combined_logs(tmp_path)
        assert "raw-hook-secret-value" not in combined
        assert "raw-hook-secret-value" not in redirected.getvalue()
        assert "event=python_unhandled_exception" in combined
    finally:
        session.close()
