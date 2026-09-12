from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import logging
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass

from quotadeck.devices.aula_f108.constants import (
    LCD_PAGE_BYTES,
    PID,
    REPORT_LEN,
    USAGE_PAGE_CONFIG,
    USAGE_PAGE_LCD,
    VID,
)
from quotadeck.devices.aula_f108.transport import pad64

log = logging.getLogger("quotadeck.device.win32")

# Fixed-width aliases make the Windows ABI explicit even when this module is
# imported by dependency-light tests on a non-Windows host.
BOOL = ctypes.c_int32
BOOLEAN = ctypes.c_ubyte
DWORD = ctypes.c_uint32
LONG = ctypes.c_int32
ULONG = ctypes.c_uint32
ULONG_PTR = ctypes.c_size_t
USHORT = ctypes.c_uint16
HANDLE = wt.HANDLE
LPDWORD = ctypes.POINTER(DWORD)

GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
FILE_SHARE_READ = 0x00000001
FILE_SHARE_WRITE = 0x00000002
OPEN_EXISTING = 3
FILE_FLAG_OVERLAPPED = 0x40000000
DIGCF_PRESENT = 0x02
DIGCF_DEVICEINTERFACE = 0x10
IOCTL_HID_SET_FEATURE = 0x000B0191
IOCTL_HID_GET_FEATURE = 0x000B0192

ERROR_OPERATION_ABORTED = 995
ERROR_IO_PENDING = 997
ERROR_NOT_FOUND = 1168
WAIT_OBJECT_0 = 0
WAIT_TIMEOUT = 258
WAIT_FAILED = 0xFFFFFFFF
INFINITE = 0xFFFFFFFF
LCD_WRITE_TIMEOUT_MS = 2_000
CANCEL_DRAIN_TIMEOUT_MS = 500
INVALID_HANDLE = ctypes.c_void_p(-1).value


class GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", ctypes.c_uint32),
        ("Data2", ctypes.c_uint16),
        ("Data3", ctypes.c_uint16),
        ("Data4", ctypes.c_ubyte * 8),
    ]


class SP_DEVICE_INTERFACE_DATA(ctypes.Structure):
    _fields_ = [
        ("cbSize", DWORD),
        ("InterfaceClassGuid", GUID),
        ("Flags", DWORD),
        # ULONG_PTR, not a pointer to an ULONG. Both happen to be pointer-sized
        # on Windows, but the declared type matters to the ABI contract.
        ("Reserved", ULONG_PTR),
    ]


class HIDD_ATTRIBUTES(ctypes.Structure):
    _fields_ = [
        ("Size", ULONG),
        ("VendorID", USHORT),
        ("ProductID", USHORT),
        ("VersionNumber", USHORT),
    ]


class HIDP_CAPS(ctypes.Structure):
    _fields_ = [
        ("Usage", USHORT),
        ("UsagePage", USHORT),
        ("InputReportByteLength", USHORT),
        ("OutputReportByteLength", USHORT),
        ("FeatureReportByteLength", USHORT),
        ("Reserved", USHORT * 17),
        ("NumberLinkCollectionNodes", USHORT),
        ("NumberInputButtonCaps", USHORT),
        ("NumberInputValueCaps", USHORT),
        ("NumberInputDataIndices", USHORT),
        ("NumberOutputButtonCaps", USHORT),
        ("NumberOutputValueCaps", USHORT),
        ("NumberOutputDataIndices", USHORT),
        ("NumberFeatureButtonCaps", USHORT),
        ("NumberFeatureValueCaps", USHORT),
        ("NumberFeatureDataIndices", USHORT),
    ]


class OVERLAPPED(ctypes.Structure):
    _fields_ = [
        ("Internal", ULONG_PTR),
        ("InternalHigh", ULONG_PTR),
        ("Offset", DWORD),
        ("OffsetHigh", DWORD),
        ("hEvent", HANDLE),
    ]


LPOVERLAPPED = ctypes.POINTER(OVERLAPPED)


@dataclass(slots=True, eq=False)
class _RetainedIo:
    kernel: object
    handle: object
    event: object
    overlapped: OVERLAPPED
    buffer: object
    stage: str


_retained_io: list[_RetainedIo] = []
_retained_io_lock = threading.Lock()


class Win32Error(RuntimeError):
    pass


def _normalize_feature_payload(buffer, returned: int) -> bytes:
    """Normalize ``IOCTL_HID_GET_FEATURE`` bytes to a 64-byte protocol payload.

    Connected F108 Pro default Win32 returns count 64 with a report-ID prefix
    (``00 04 18 00 01...``), not a payload-only buffer. Both count conventions
    therefore drop ``buffer[0]`` so protocol ACK stays at payload ``byte[3]``:

    * 65 bytes: ``[report_id][64-byte payload]`` — drop the report ID.
    * 64 bytes: ``[report_id][63-byte payload]`` — drop the report ID, then
      pad the unavailable trailing payload byte.

    Counts below 64 are genuine short reads.
    """
    raw = bytes(buffer)[:returned]
    if returned == REPORT_LEN + 1:
        return raw[1:]
    if returned == REPORT_LEN:
        return raw[1:].ljust(REPORT_LEN, b"\x00")
    raise Win32Error(
        f"GET_FEATURE short read ({returned}/{REPORT_LEN + 1} bytes)"
    )


def _last_error() -> int:
    """Return ctypes' private Win32 error copy; kept replaceable for tests."""
    getter = getattr(ctypes, "get_last_error", None)
    return int(getter()) if getter is not None else 0


def _invalid_handle(handle) -> bool:
    value = handle.value if isinstance(handle, ctypes.c_void_p) else handle
    return value in (None, 0, INVALID_HANDLE)


def _reap_retained_io(item: _RetainedIo) -> None:
    """Keep native storage alive until an uncooperative driver completes."""
    wait = int(item.kernel.WaitForSingleObject(item.event, INFINITE))
    if wait != WAIT_OBJECT_0:
        # Keeping the item in the module-level list is an intentional bounded
        # leak: freeing its buffer/OVERLAPPED while the IRP may still own them
        # can corrupt the process. The OS will reclaim it at process exit.
        log.critical(
            "event=win32_io_quarantine_wait_failed stage=%s wait=0x%08x",
            item.stage,
            wait,
        )
        return
    transferred = DWORD(0)
    completed = item.kernel.GetOverlappedResult(
        item.handle,
        ctypes.byref(item.overlapped),
        ctypes.byref(transferred),
        False,
    )
    final_error = 0 if completed else _last_error()
    item.kernel.CloseHandle(item.event)
    item.kernel.CloseHandle(item.handle)
    with _retained_io_lock:
        if item in _retained_io:
            _retained_io.remove(item)
    log.info(
        "event=win32_io_quarantine_reaped stage=%s final_error=%d",
        item.stage,
        final_error,
    )


def _retain_pending_io(item: _RetainedIo) -> None:
    with _retained_io_lock:
        _retained_io.append(item)
    try:
        threading.Thread(
            target=_reap_retained_io,
            args=(item,),
            name="quotadeck-hid-reaper",
            daemon=True,
        ).start()
    except Exception:
        # The module-level list still preserves native storage safely.
        log.exception("event=win32_io_quarantine_reaper_start_failed stage=%s", item.stage)


def _set_prototype(function, argtypes: list[object], restype: object) -> None:
    function.argtypes = argtypes
    function.restype = restype


def _bind_apis(hid, setup, kernel) -> None:
    """Declare every native signature before any pointer-valued call.

    ctypes otherwise assumes a C ``int`` return value. In particular, that
    truncates HDEVINFO and HANDLE values in 64-bit builds and can terminate the
    process before Python gets a chance to log an exception.
    """
    _set_prototype(hid.HidD_GetHidGuid, [ctypes.POINTER(GUID)], None)
    _set_prototype(
        hid.HidD_GetAttributes,
        [HANDLE, ctypes.POINTER(HIDD_ATTRIBUTES)],
        BOOLEAN,
    )
    _set_prototype(
        hid.HidD_GetPreparsedData,
        [HANDLE, ctypes.POINTER(ctypes.c_void_p)],
        BOOLEAN,
    )
    _set_prototype(hid.HidD_FreePreparsedData, [ctypes.c_void_p], BOOLEAN)
    _set_prototype(
        hid.HidP_GetCaps,
        [ctypes.c_void_p, ctypes.POINTER(HIDP_CAPS)],
        LONG,
    )

    _set_prototype(
        setup.SetupDiGetClassDevsW,
        [ctypes.POINTER(GUID), wt.LPCWSTR, wt.HWND, DWORD],
        HANDLE,
    )
    _set_prototype(
        setup.SetupDiEnumDeviceInterfaces,
        [
            HANDLE,
            ctypes.c_void_p,
            ctypes.POINTER(GUID),
            DWORD,
            ctypes.POINTER(SP_DEVICE_INTERFACE_DATA),
        ],
        BOOL,
    )
    _set_prototype(
        setup.SetupDiGetDeviceInterfaceDetailW,
        [
            HANDLE,
            ctypes.POINTER(SP_DEVICE_INTERFACE_DATA),
            ctypes.c_void_p,
            DWORD,
            LPDWORD,
            ctypes.c_void_p,
        ],
        BOOL,
    )
    _set_prototype(setup.SetupDiDestroyDeviceInfoList, [HANDLE], BOOL)

    _set_prototype(
        kernel.CreateFileW,
        [wt.LPCWSTR, DWORD, DWORD, ctypes.c_void_p, DWORD, DWORD, HANDLE],
        HANDLE,
    )
    _set_prototype(
        kernel.DeviceIoControl,
        [
            HANDLE,
            DWORD,
            ctypes.c_void_p,
            DWORD,
            ctypes.c_void_p,
            DWORD,
            LPDWORD,
            LPOVERLAPPED,
        ],
        BOOL,
    )
    _set_prototype(
        kernel.WriteFile,
        [HANDLE, ctypes.c_void_p, DWORD, LPDWORD, LPOVERLAPPED],
        BOOL,
    )
    _set_prototype(
        kernel.ReadFile,
        [HANDLE, ctypes.c_void_p, DWORD, LPDWORD, LPOVERLAPPED],
        BOOL,
    )
    _set_prototype(
        kernel.CreateEventW,
        [ctypes.c_void_p, BOOL, BOOL, wt.LPCWSTR],
        HANDLE,
    )
    _set_prototype(kernel.WaitForSingleObject, [HANDLE, DWORD], DWORD)
    _set_prototype(kernel.CancelIoEx, [HANDLE, LPOVERLAPPED], BOOL)
    _set_prototype(
        kernel.GetOverlappedResult,
        [HANDLE, LPOVERLAPPED, LPDWORD, BOOL],
        BOOL,
    )
    _set_prototype(kernel.CloseHandle, [HANDLE], BOOL)


def _win_dlls():
    hid = ctypes.WinDLL("hid.dll", use_last_error=True)
    setup = ctypes.WinDLL("setupapi.dll", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    _bind_apis(hid, setup, kernel)
    return hid, setup, kernel


def _find_path(usage_page: int, dlls=None) -> str:
    hid, setup, kernel = dlls or _win_dlls()
    guid = GUID()
    hid.HidD_GetHidGuid(ctypes.byref(guid))
    devinfo = setup.SetupDiGetClassDevsW(
        ctypes.byref(guid), None, None, DIGCF_PRESENT | DIGCF_DEVICEINTERFACE
    )
    if _invalid_handle(devinfo):
        error = _last_error()
        log.error("event=win32_discovery_failed stage=get_class_devs error=%d", error)
        raise Win32Error(f"SetupDiGetClassDevsW failed: {error}")
    try:
        iface = SP_DEVICE_INTERFACE_DATA()
        iface.cbSize = ctypes.sizeof(SP_DEVICE_INTERFACE_DATA)
        index = 0
        while setup.SetupDiEnumDeviceInterfaces(
            devinfo, None, ctypes.byref(guid), index, ctypes.byref(iface)
        ):
            index += 1
            needed = DWORD(0)
            setup.SetupDiGetDeviceInterfaceDetailW(
                devinfo, ctypes.byref(iface), None, 0, ctypes.byref(needed), None
            )
            if needed.value == 0:
                continue
            buf = (ctypes.c_byte * needed.value)()
            ctypes.cast(buf, ctypes.POINTER(DWORD))[0] = (
                8 if ctypes.sizeof(ctypes.c_void_p) == 8 else 6
            )
            if not setup.SetupDiGetDeviceInterfaceDetailW(
                devinfo,
                ctypes.byref(iface),
                buf,
                needed.value,
                ctypes.byref(needed),
                None,
            ):
                continue
            path = ctypes.wstring_at(ctypes.addressof(buf) + ctypes.sizeof(DWORD))
            handle = kernel.CreateFileW(
                path,
                GENERIC_READ | GENERIC_WRITE,
                FILE_SHARE_READ | FILE_SHARE_WRITE,
                None,
                OPEN_EXISTING,
                0,
                None,
            )
            if _invalid_handle(handle):
                continue
            try:
                attrs = HIDD_ATTRIBUTES()
                attrs.Size = ctypes.sizeof(HIDD_ATTRIBUTES)
                if not hid.HidD_GetAttributes(handle, ctypes.byref(attrs)):
                    continue
                if attrs.VendorID != VID or attrs.ProductID != PID:
                    continue
                preparsed = ctypes.c_void_p()
                if not hid.HidD_GetPreparsedData(handle, ctypes.byref(preparsed)):
                    continue
                try:
                    caps = HIDP_CAPS()
                    if hid.HidP_GetCaps(preparsed, ctypes.byref(caps)) < 0:
                        continue
                    if caps.UsagePage == usage_page:
                        log.info(
                            "event=win32_interface_found usage_page=0x%04x",
                            usage_page,
                        )
                        return path
                finally:
                    hid.HidD_FreePreparsedData(preparsed)
            finally:
                kernel.CloseHandle(handle)
    finally:
        setup.SetupDiDestroyDeviceInfoList(devinfo)
    raise Win32Error(
        f"device not found (VID={VID:04x} PID={PID:04x} usage={usage_page:04x})"
    )


def _open(path: str, kernel, *, overlapped: bool = False):
    flags = FILE_FLAG_OVERLAPPED if overlapped else 0
    handle = kernel.CreateFileW(
        path,
        GENERIC_READ | GENERIC_WRITE,
        FILE_SHARE_READ | FILE_SHARE_WRITE,
        None,
        OPEN_EXISTING,
        flags,
        None,
    )
    if _invalid_handle(handle):
        error = _last_error()
        log.error(
            "event=win32_open_failed overlapped=%s error=%d",
            overlapped,
            error,
        )
        raise Win32Error(f"CreateFileW failed: {error}")
    return handle


class Win32Transport:
    def __init__(self) -> None:
        if sys.platform != "win32":
            raise Win32Error("Win32 transport is Windows-only")
        self._io_lock = threading.RLock()
        self._feature = None
        self._lcd = None
        self._hid, self._setup, self._kernel = _win_dlls()
        log.info(
            "event=win32_transport_open_start pointer_bits=%d",
            ctypes.sizeof(ctypes.c_void_p) * 8,
        )
        try:
            dlls = (self._hid, self._setup, self._kernel)
            feature_path = _find_path(USAGE_PAGE_CONFIG, dlls)
            lcd_path = _find_path(USAGE_PAGE_LCD, dlls)
            self._feature = _open(feature_path, self._kernel)
            # Both operations on this handle must use OVERLAPPED I/O.
            self._lcd = _open(lcd_path, self._kernel, overlapped=True)
        except BaseException:
            self.close()
            raise
        log.info("event=win32_transport_opened")

    def set_feature(self, data: bytes) -> None:
        with self._io_lock:
            self._set_feature(data)

    def _set_feature(self, data: bytes) -> None:
        if self._feature is None:
            raise Win32Error("transport is closed")
        buf = (ctypes.c_ubyte * (REPORT_LEN + 1))(0, *pad64(data))
        returned = DWORD(0)
        ok = self._kernel.DeviceIoControl(
            self._feature,
            IOCTL_HID_SET_FEATURE,
            buf,
            REPORT_LEN + 1,
            None,
            0,
            ctypes.byref(returned),
            None,
        )
        if not ok:
            error = _last_error()
            log.error("event=win32_io_failed stage=set_feature error=%d", error)
            raise Win32Error(f"SET_FEATURE failed: {error}")

    def get_feature(self) -> bytes:
        with self._io_lock:
            return self._get_feature()

    def _get_feature(self) -> bytes:
        if self._feature is None:
            raise Win32Error("transport is closed")
        buf = (ctypes.c_ubyte * (REPORT_LEN + 1))()
        returned = DWORD(0)
        ok = self._kernel.DeviceIoControl(
            self._feature,
            IOCTL_HID_GET_FEATURE,
            buf,
            REPORT_LEN + 1,
            buf,
            REPORT_LEN + 1,
            ctypes.byref(returned),
            None,
        )
        if not ok:
            error = _last_error()
            log.error("event=win32_io_failed stage=get_feature error=%d", error)
            raise Win32Error(f"GET_FEATURE failed: {error}")
        return _normalize_feature_payload(buf, int(returned.value))

    def _cancel_and_drain(
        self,
        handle,
        event,
        overlapped: OVERLAPPED,
        transferred: DWORD,
        stage: str,
    ) -> bool:
        cancelled = self._kernel.CancelIoEx(handle, ctypes.byref(overlapped))
        cancel_error = 0 if cancelled else _last_error()
        wait = int(
            self._kernel.WaitForSingleObject(event, CANCEL_DRAIN_TIMEOUT_MS)
        )
        if wait != WAIT_OBJECT_0:
            log.critical(
                "event=win32_cancel_unconfirmed stage=%s cancel_error=%d "
                "drain_wait=0x%08x drain_timeout_ms=%d",
                stage,
                cancel_error,
                wait,
                CANCEL_DRAIN_TIMEOUT_MS,
            )
            return False
        drained = self._kernel.GetOverlappedResult(
            handle,
            ctypes.byref(overlapped),
            ctypes.byref(transferred),
            False,
        )
        drain_error = 0 if drained else _last_error()
        if (not cancelled and cancel_error != ERROR_NOT_FOUND) or (
            not drained and drain_error != ERROR_OPERATION_ABORTED
        ):
            log.error(
                "event=win32_cancel_failed stage=%s cancel_error=%d drain_error=%d",
                stage,
                cancel_error,
                drain_error,
            )
        # The dedicated event being signaled establishes completion, including
        # failure/cancellation, so the native storage can now be released.
        return True

    def _overlapped_transfer(
        self,
        operation: Callable[..., int],
        buffer,
        size: int,
        *,
        timeout_ms: int,
        stage: str,
    ) -> int:
        with self._io_lock:
            return self._overlapped_transfer_locked(
                operation,
                buffer,
                size,
                timeout_ms=timeout_ms,
                stage=stage,
            )

    def _overlapped_transfer_locked(
        self,
        operation: Callable[..., int],
        buffer,
        size: int,
        *,
        timeout_ms: int,
        stage: str,
    ) -> int:
        if self._lcd is None:
            raise Win32Error("transport is closed")
        handle = self._lcd
        timeout_ms = max(1, min(int(timeout_ms), 0xFFFFFFFE))
        event = self._kernel.CreateEventW(None, True, False, None)
        if not event:
            error = _last_error()
            log.error(
                "event=win32_io_failed stage=%s_create_event error=%d",
                stage,
                error,
            )
            raise Win32Error(f"{stage} CreateEventW failed: {error}")
        overlapped = OVERLAPPED()
        overlapped.hEvent = event
        transferred = DWORD(0)
        release_event = True
        try:
            ok = operation(
                handle,
                buffer,
                size,
                None,
                ctypes.byref(overlapped),
            )
            if ok:
                # For an asynchronous handle the byte-count argument above is
                # intentionally NULL; GetOverlappedResult is authoritative
                # even when the operation completed before the call returned.
                if not self._kernel.GetOverlappedResult(
                    handle,
                    ctypes.byref(overlapped),
                    ctypes.byref(transferred),
                    False,
                ):
                    error = _last_error()
                    log.error(
                        "event=win32_io_failed stage=%s_immediate_result error=%d",
                        stage,
                        error,
                    )
                    raise Win32Error(f"{stage} completion failed: {error}")
                return int(transferred.value)
            error = _last_error()
            if error != ERROR_IO_PENDING:
                log.error(
                    "event=win32_io_failed stage=%s error=%d",
                    stage,
                    error,
                )
                raise Win32Error(f"{stage} failed: {error}")

            wait = int(self._kernel.WaitForSingleObject(event, timeout_ms))
            if wait == WAIT_TIMEOUT:
                drained = self._cancel_and_drain(
                    handle,
                    event,
                    overlapped,
                    transferred,
                    stage,
                )
                if not drained:
                    _retain_pending_io(
                        _RetainedIo(
                            self._kernel,
                            handle,
                            event,
                            overlapped,
                            buffer,
                            stage,
                        )
                    )
                    self._lcd = None
                    release_event = False
                log.warning(
                    "event=win32_io_timeout stage=%s timeout_ms=%d",
                    stage,
                    timeout_ms,
                )
                raise Win32Error(f"{stage} timeout after {timeout_ms} ms")
            if wait != WAIT_OBJECT_0:
                wait_error = _last_error() if wait == WAIT_FAILED else 0
                drained = self._cancel_and_drain(
                    handle,
                    event,
                    overlapped,
                    transferred,
                    stage,
                )
                if not drained:
                    _retain_pending_io(
                        _RetainedIo(
                            self._kernel,
                            handle,
                            event,
                            overlapped,
                            buffer,
                            stage,
                        )
                    )
                    self._lcd = None
                    release_event = False
                log.error(
                    "event=win32_io_wait_failed stage=%s wait=0x%08x error=%d",
                    stage,
                    wait,
                    wait_error,
                )
                raise Win32Error(
                    f"{stage} wait failed (result=0x{wait:08x}, error={wait_error})"
                )
            if not self._kernel.GetOverlappedResult(
                handle,
                ctypes.byref(overlapped),
                ctypes.byref(transferred),
                False,
            ):
                error = _last_error()
                log.error(
                    "event=win32_io_failed stage=%s_result error=%d",
                    stage,
                    error,
                )
                raise Win32Error(f"{stage} completion failed: {error}")
            return int(transferred.value)
        finally:
            if release_event and not self._kernel.CloseHandle(event):
                log.warning(
                    "event=win32_event_close_failed stage=%s error=%d",
                    stage,
                    _last_error(),
                )

    def write_lcd_page(self, data: bytes) -> None:
        if len(data) != LCD_PAGE_BYTES:
            raise Win32Error("bad page size")
        buf = (ctypes.c_ubyte * (LCD_PAGE_BYTES + 1))(0, *data)
        written = self._overlapped_transfer(
            self._kernel.WriteFile,
            buf,
            LCD_PAGE_BYTES + 1,
            timeout_ms=LCD_WRITE_TIMEOUT_MS,
            stage="write_lcd_page",
        )
        expected = LCD_PAGE_BYTES + 1
        if written != expected:
            log.error(
                "event=win32_io_short stage=write_lcd_page actual=%d expected=%d",
                written,
                expected,
            )
            raise Win32Error(f"WriteFile LCD short write ({written}/{expected} bytes)")

    def read_lcd_ack(self, timeout_ms: int = 300) -> bytes:
        buf = (ctypes.c_ubyte * (REPORT_LEN + 1))()
        count = self._overlapped_transfer(
            self._kernel.ReadFile,
            buf,
            REPORT_LEN + 1,
            timeout_ms=timeout_ms,
            stage="read_lcd_ack",
        )
        # Native HID ReadFile includes the zero report ID. At least the report
        # ID plus the two protocol ACK bytes must be present.
        if count < 3 or count > REPORT_LEN + 1:
            log.error(
                "event=win32_io_short stage=read_lcd_ack actual=%d maximum=%d",
                count,
                REPORT_LEN + 1,
            )
            raise Win32Error(f"ReadFile LCD ACK invalid length ({count} bytes)")
        return bytes(buf)[1:count].ljust(REPORT_LEN, b"\x00")

    def close(self) -> None:
        lock = getattr(self, "_io_lock", None)
        if lock is None:
            return
        with lock:
            self._close_locked()

    def _close_locked(self) -> None:
        kernel = getattr(self, "_kernel", None)
        if kernel is None:
            return
        closed = 0
        for name in ("_lcd", "_feature"):
            handle = getattr(self, name, None)
            if not _invalid_handle(handle):
                if not kernel.CloseHandle(handle):
                    log.warning(
                        "event=win32_handle_close_failed kind=%s error=%d",
                        name.removeprefix("_"),
                        _last_error(),
                    )
                else:
                    closed += 1
            setattr(self, name, None)
        if closed:
            log.info("event=win32_transport_closed handles=%d", closed)
