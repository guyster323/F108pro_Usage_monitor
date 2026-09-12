from __future__ import annotations

import ctypes
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from quotadeck.devices.aula_f108 import transport_win32 as win32


class _Function:
    def __call__(self, *args):
        return 1


def _dll(*names: str):
    return SimpleNamespace(**{name: _Function() for name in names})


def _transport(kernel, *, feature=None, lcd=0x1234567887654321):
    transport = object.__new__(win32.Win32Transport)
    transport._io_lock = win32.threading.RLock()
    transport._kernel = kernel
    transport._lcd = lcd
    transport._feature = feature
    return transport


def _feature_buffer(payload: bytes):
    buf = (ctypes.c_ubyte * (win32.REPORT_LEN + 1))()
    for index, value in enumerate(payload):
        buf[index] = value
    return buf


def _acked_feature_payload() -> bytes:
    # Distinct markers: skipping a 64-byte payload would move ACK off index 3.
    return bytes([0x04, 0x18, 0xAA, 0x01]) + bytes([0xBB] * (win32.REPORT_LEN - 4))


def _set_dword(pointer, value: int) -> None:
    ctypes.cast(pointer, win32.LPDWORD).contents.value = value


class Win32AbiTests(unittest.TestCase):
    def test_pointer_returning_apis_have_explicit_prototypes(self) -> None:
        hid = _dll(
            "HidD_GetHidGuid",
            "HidD_GetAttributes",
            "HidD_GetPreparsedData",
            "HidD_FreePreparsedData",
            "HidP_GetCaps",
        )
        setup = _dll(
            "SetupDiGetClassDevsW",
            "SetupDiEnumDeviceInterfaces",
            "SetupDiGetDeviceInterfaceDetailW",
            "SetupDiDestroyDeviceInfoList",
        )
        kernel = _dll(
            "CreateFileW",
            "DeviceIoControl",
            "WriteFile",
            "ReadFile",
            "CreateEventW",
            "WaitForSingleObject",
            "CancelIoEx",
            "GetOverlappedResult",
            "CloseHandle",
        )

        win32._bind_apis(hid, setup, kernel)

        self.assertIs(setup.SetupDiGetClassDevsW.restype, win32.HANDLE)
        self.assertIs(kernel.CreateFileW.restype, win32.HANDLE)
        self.assertIs(kernel.CreateEventW.restype, win32.HANDLE)
        self.assertIs(kernel.ReadFile.argtypes[0], win32.HANDLE)
        self.assertIs(kernel.WriteFile.argtypes[0], win32.HANDLE)
        self.assertIs(kernel.CloseHandle.argtypes[0], win32.HANDLE)

    def test_windows_structure_layouts_are_fixed_width(self) -> None:
        self.assertEqual(ctypes.sizeof(win32.HIDD_ATTRIBUTES), 12)
        self.assertEqual(ctypes.sizeof(win32.HIDP_CAPS), 64)
        if ctypes.sizeof(ctypes.c_void_p) == 8:
            self.assertEqual(ctypes.sizeof(win32.SP_DEVICE_INTERFACE_DATA), 32)
            self.assertEqual(ctypes.sizeof(win32.OVERLAPPED), 32)
            self.assertEqual(win32.OVERLAPPED.hEvent.offset, 24)
        else:
            self.assertEqual(ctypes.sizeof(win32.SP_DEVICE_INTERFACE_DATA), 28)
            self.assertEqual(ctypes.sizeof(win32.OVERLAPPED), 20)
            self.assertEqual(win32.OVERLAPPED.hEvent.offset, 16)

    def test_invalid_handle_accepts_pointer_objects(self) -> None:
        self.assertTrue(win32._invalid_handle(ctypes.c_void_p()))
        self.assertTrue(win32._invalid_handle(ctypes.c_void_p(-1)))
        self.assertFalse(win32._invalid_handle(ctypes.c_void_p(0x1234567887654321)))

    def test_open_preserves_pointer_handle_and_sets_overlapped_flag(self) -> None:
        calls: list[tuple[object, ...]] = []
        expected = ctypes.c_void_p(0x1234567887654321)
        kernel = SimpleNamespace(
            CreateFileW=lambda *args: calls.append(args) or expected,
        )

        actual = win32._open("redacted-device", kernel, overlapped=True)

        self.assertIs(actual, expected)
        self.assertEqual(calls[0][5], win32.FILE_FLAG_OVERLAPPED)


class _PendingReadKernel:
    def __init__(self, *wait_results: int) -> None:
        self.wait_results = list(wait_results)
        self.wait_timeouts: list[int] = []
        self.cancelled = False
        self.drained = False
        self.closed: list[int] = []
        self.events: list[str] = []

    def CreateEventW(self, *_args):
        return 0x4444

    def ReadFile(self, _handle, buffer, _size, count, _overlapped):
        assert count is None
        buffer[0] = 0
        buffer[1] = 0x01
        buffer[2] = 0x5A
        return 0

    def WaitForSingleObject(self, _event, timeout_ms):
        self.wait_timeouts.append(timeout_ms)
        return self.wait_results.pop(0)

    def CancelIoEx(self, _handle, _overlapped):
        self.cancelled = True
        self.events.append("cancel")
        return 1

    def GetOverlappedResult(self, _handle, _overlapped, count, wait):
        assert not wait
        if self.cancelled:
            self.drained = True
            self.events.append("drain")
            return 0
        self.events.append("result")
        _set_dword(count, win32.REPORT_LEN + 1)
        return 1

    def CloseHandle(self, handle):
        self.closed.append(handle)
        self.events.append("close")
        return 1


class _ImmediateWriteKernel:
    def __init__(self, transferred: int) -> None:
        self.transferred = transferred
        self.result_calls = 0
        self.closed: list[int] = []

    def CreateEventW(self, *_args):
        return 0x5555

    def WriteFile(self, _handle, _buffer, _size, count, _overlapped):
        assert count is None
        return 1

    def GetOverlappedResult(self, _handle, _overlapped, count, wait):
        self.result_calls += 1
        assert not wait
        _set_dword(count, self.transferred)
        return 1

    def CloseHandle(self, handle):
        self.closed.append(handle)
        return 1


class Win32OverlappedTests(unittest.TestCase):
    def test_pending_ack_uses_requested_timeout_and_returns_payload(self) -> None:
        kernel = _PendingReadKernel(win32.WAIT_OBJECT_0)
        transport = _transport(kernel)

        with patch.object(win32, "_last_error", return_value=win32.ERROR_IO_PENDING):
            ack = transport.read_lcd_ack(437)

        self.assertEqual(kernel.wait_timeouts, [437])
        self.assertEqual(ack[:2], b"\x01\x5a")
        self.assertEqual(len(ack), win32.REPORT_LEN)
        self.assertFalse(kernel.cancelled)
        self.assertEqual(kernel.closed, [0x4444])

    def test_ack_timeout_cancels_and_drains_before_returning(self) -> None:
        kernel = _PendingReadKernel(win32.WAIT_TIMEOUT, win32.WAIT_OBJECT_0)
        transport = _transport(kernel)

        with patch.object(
            win32,
            "_last_error",
            side_effect=[win32.ERROR_IO_PENDING, win32.ERROR_OPERATION_ABORTED],
        ):
            with self.assertRaisesRegex(win32.Win32Error, "timeout after 300 ms"):
                transport.read_lcd_ack(300)

        self.assertTrue(kernel.cancelled)
        self.assertTrue(kernel.drained)
        self.assertEqual(
            kernel.wait_timeouts,
            [300, win32.CANCEL_DRAIN_TIMEOUT_MS],
        )
        self.assertEqual(kernel.closed, [0x4444])
        self.assertEqual(kernel.events, ["cancel", "drain", "close"])

    def test_unconfirmed_cancel_quarantines_native_storage(self) -> None:
        kernel = _PendingReadKernel(win32.WAIT_TIMEOUT, win32.WAIT_TIMEOUT)
        transport = _transport(kernel)
        retained: list[win32._RetainedIo] = []

        with (
            patch.object(win32, "_last_error", return_value=win32.ERROR_IO_PENDING),
            patch.object(win32, "_retain_pending_io", side_effect=retained.append),
        ):
            with self.assertRaisesRegex(win32.Win32Error, "timeout after 300 ms"):
                transport.read_lcd_ack(300)

        self.assertIsNone(transport._lcd)
        self.assertEqual(len(retained), 1)
        self.assertIs(retained[0].kernel, kernel)
        self.assertEqual(retained[0].handle, 0x1234567887654321)
        self.assertIsInstance(retained[0].overlapped, win32.OVERLAPPED)
        self.assertEqual(kernel.closed, [])

    def test_quarantined_io_is_reaped_only_after_event_signals(self) -> None:
        kernel = _PendingReadKernel(win32.WAIT_OBJECT_0)
        item = win32._RetainedIo(
            kernel=kernel,
            handle=0x6666,
            event=0x7777,
            overlapped=win32.OVERLAPPED(),
            buffer=(ctypes.c_ubyte * 4)(),
            stage="test_io",
        )
        with win32._retained_io_lock:
            win32._retained_io.append(item)

        win32._reap_retained_io(item)

        self.assertEqual(kernel.closed, [0x7777, 0x6666])
        with win32._retained_io_lock:
            self.assertNotIn(item, win32._retained_io)

    def test_immediate_write_uses_get_overlapped_result_for_byte_count(self) -> None:
        expected = win32.LCD_PAGE_BYTES + 1
        kernel = _ImmediateWriteKernel(expected)
        transport = _transport(kernel)

        transport.write_lcd_page(bytes(win32.LCD_PAGE_BYTES))

        self.assertEqual(kernel.result_calls, 1)
        self.assertEqual(kernel.closed, [0x5555])

    def test_short_write_is_rejected(self) -> None:
        kernel = _ImmediateWriteKernel(win32.LCD_PAGE_BYTES)
        transport = _transport(kernel)

        with self.assertRaisesRegex(win32.Win32Error, "short write"):
            transport.write_lcd_page(bytes(win32.LCD_PAGE_BYTES))


class _GetFeatureKernel:
    def __init__(self, returned: int, fill: bytes) -> None:
        self.returned = returned
        self.fill = fill
        self.ioctl = 0

    def DeviceIoControl(
        self, _handle, ioctl, _inbuf, _inlen, outbuf, _outlen, count, _overlapped
    ):
        self.ioctl = ioctl
        for index, value in enumerate(self.fill):
            outbuf[index] = value
        _set_dword(count, self.returned)
        return 1


class Win32GetFeatureTests(unittest.TestCase):
    def test_sixty_five_byte_report_drops_report_id_and_keeps_ack(self) -> None:
        payload = _acked_feature_payload()
        raw = bytes([0x00]) + payload
        normalized = win32._normalize_feature_payload(_feature_buffer(raw), 65)

        self.assertEqual(len(normalized), win32.REPORT_LEN)
        self.assertEqual(normalized[:4], b"\x04\x18\xaa\x01")
        self.assertEqual(normalized[3], 0x01)

    def test_sixty_four_byte_report_does_not_shift_ack_status_byte(self) -> None:
        payload = _acked_feature_payload()
        normalized = win32._normalize_feature_payload(_feature_buffer(payload), 64)

        self.assertEqual(len(normalized), win32.REPORT_LEN)
        self.assertEqual(normalized[:4], b"\x04\x18\xaa\x01")
        self.assertEqual(normalized[3], 0x01)
        self.assertNotEqual(normalized[2], 0x01)

    def test_shorter_feature_reads_are_rejected(self) -> None:
        payload = _acked_feature_payload()
        buf = _feature_buffer(bytes([0x00]) + payload)
        for returned in (0, 1, 3, 63):
            with self.subTest(returned=returned):
                with self.assertRaisesRegex(win32.Win32Error, r"short read"):
                    win32._normalize_feature_payload(buf, returned)

    def test_get_feature_ioctl_accepts_both_return_conventions(self) -> None:
        payload = _acked_feature_payload()
        cases = (
            (win32.REPORT_LEN + 1, bytes([0x00]) + payload),
            (win32.REPORT_LEN, payload),
        )
        for returned, fill in cases:
            with self.subTest(returned=returned):
                kernel = _GetFeatureKernel(returned, fill)
                transport = _transport(kernel, feature=0x1111)

                response = transport.get_feature()

                self.assertEqual(kernel.ioctl, win32.IOCTL_HID_GET_FEATURE)
                self.assertEqual(response, payload)
                self.assertEqual(response[3], 0x01)

    def test_get_feature_ioctl_rejects_short_read(self) -> None:
        payload = _acked_feature_payload()
        kernel = _GetFeatureKernel(63, payload)
        transport = _transport(kernel, feature=0x1111)

        with self.assertRaisesRegex(win32.Win32Error, r"short read \(63/65 bytes\)"):
            transport.get_feature()


class Win32LifetimeTests(unittest.TestCase):
    def test_partial_init_closes_feature_handle(self) -> None:
        kernel = SimpleNamespace(CloseHandle=lambda handle: closed.append(handle) or 1)
        closed: list[int] = []
        dlls = (object(), object(), kernel)

        with (
            patch.object(win32.sys, "platform", "win32"),
            patch.object(win32, "_win_dlls", return_value=dlls),
            patch.object(win32, "_find_path", side_effect=["feature", "lcd"]),
            patch.object(
                win32,
                "_open",
                side_effect=[0x1234567887654321, win32.Win32Error("LCD open failed")],
            ),
        ):
            with self.assertRaisesRegex(win32.Win32Error, "LCD open failed"):
                win32.Win32Transport()

        self.assertEqual(closed, [0x1234567887654321])

    def test_close_is_idempotent_with_pointer_handles(self) -> None:
        closed: list[object] = []
        kernel = SimpleNamespace(CloseHandle=lambda handle: closed.append(handle) or 1)
        transport = _transport(kernel)
        transport._feature = ctypes.c_void_p(0x2222)
        transport._lcd = ctypes.c_void_p(0x3333)

        transport.close()
        transport.close()

        self.assertEqual(
            [handle.value for handle in closed],
            [0x3333, 0x2222],
        )


if __name__ == "__main__":
    unittest.main()
