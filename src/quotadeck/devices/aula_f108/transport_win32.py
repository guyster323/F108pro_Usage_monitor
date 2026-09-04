from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import sys
from ctypes import wintypes

from quotadeck.devices.aula_f108.constants import (
    LCD_PAGE_BYTES,
    PID,
    REPORT_LEN,
    USAGE_PAGE_CONFIG,
    USAGE_PAGE_LCD,
    VID,
)
from quotadeck.devices.aula_f108.transport import pad64

GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
FILE_SHARE_READ = 0x00000001
FILE_SHARE_WRITE = 0x00000002
OPEN_EXISTING = 3
DIGCF_PRESENT = 0x02
DIGCF_DEVICEINTERFACE = 0x10
IOCTL_HID_SET_FEATURE = 0x000B0191
IOCTL_HID_GET_FEATURE = 0x000B0192
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
        ("cbSize", wintypes.DWORD),
        ("InterfaceClassGuid", GUID),
        ("Flags", wintypes.DWORD),
        ("Reserved", ctypes.POINTER(ctypes.c_ulong)),
    ]


class HIDD_ATTRIBUTES(ctypes.Structure):
    _fields_ = [
        ("Size", wintypes.ULONG),
        ("VendorID", wintypes.USHORT),
        ("ProductID", wintypes.USHORT),
        ("VersionNumber", wintypes.USHORT),
    ]


class HIDP_CAPS(ctypes.Structure):
    _fields_ = [
        ("Usage", wintypes.USHORT),
        ("UsagePage", wintypes.USHORT),
        ("InputReportByteLength", wintypes.USHORT),
        ("OutputReportByteLength", wintypes.USHORT),
        ("FeatureReportByteLength", wintypes.USHORT),
        ("Reserved", wintypes.USHORT * 17),
        ("NumberLinkCollectionNodes", wintypes.USHORT),
        ("NumberInputButtonCaps", wintypes.USHORT),
        ("NumberInputValueCaps", wintypes.USHORT),
        ("NumberInputDataIndices", wintypes.USHORT),
        ("NumberOutputButtonCaps", wintypes.USHORT),
        ("NumberOutputValueCaps", wintypes.USHORT),
        ("NumberOutputDataIndices", wintypes.USHORT),
        ("NumberFeatureButtonCaps", wintypes.USHORT),
        ("NumberFeatureValueCaps", wintypes.USHORT),
        ("NumberFeatureDataIndices", wintypes.USHORT),
    ]


class Win32Error(RuntimeError):
    pass


def _win_dlls():
    hid = ctypes.WinDLL("hid.dll")
    setup = ctypes.WinDLL("setupapi.dll")
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    return hid, setup, kernel


def _find_path(usage_page: int) -> str:
    hid, setup, kernel = _win_dlls()
    guid = GUID()
    hid.HidD_GetHidGuid(ctypes.byref(guid))
    devinfo = setup.SetupDiGetClassDevsW(
        ctypes.byref(guid), None, None, DIGCF_PRESENT | DIGCF_DEVICEINTERFACE
    )
    if devinfo == INVALID_HANDLE:
        raise Win32Error("SetupDiGetClassDevsW failed")
    try:
        iface = SP_DEVICE_INTERFACE_DATA()
        iface.cbSize = ctypes.sizeof(SP_DEVICE_INTERFACE_DATA)
        index = 0
        while setup.SetupDiEnumDeviceInterfaces(
            devinfo, None, ctypes.byref(guid), index, ctypes.byref(iface)
        ):
            index += 1
            needed = wintypes.DWORD(0)
            setup.SetupDiGetDeviceInterfaceDetailW(
                devinfo, ctypes.byref(iface), None, 0, ctypes.byref(needed), None
            )
            if needed.value == 0:
                continue
            buf = (ctypes.c_byte * needed.value)()
            ctypes.cast(buf, ctypes.POINTER(wintypes.DWORD))[0] = 8 if ctypes.sizeof(ctypes.c_void_p) == 8 else 6
            if not setup.SetupDiGetDeviceInterfaceDetailW(
                devinfo, ctypes.byref(iface), buf, needed, None, None
            ):
                continue
            path = ctypes.wstring_at(ctypes.addressof(buf) + 4)
            handle = kernel.CreateFileW(
                path,
                GENERIC_READ | GENERIC_WRITE,
                FILE_SHARE_READ | FILE_SHARE_WRITE,
                None,
                OPEN_EXISTING,
                0,
                None,
            )
            if handle == INVALID_HANDLE:
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
                    hid.HidP_GetCaps(preparsed, ctypes.byref(caps))
                    if caps.UsagePage == usage_page:
                        return path
                finally:
                    hid.HidD_FreePreparsedData(preparsed)
            finally:
                kernel.CloseHandle(handle)
    finally:
        setup.SetupDiDestroyDeviceInfoList(devinfo)
    raise Win32Error(f"device not found (VID={VID:04x} PID={PID:04x} usage={usage_page:04x})")


def _open(path: str):
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    handle = kernel.CreateFileW(
        path,
        GENERIC_READ | GENERIC_WRITE,
        FILE_SHARE_READ | FILE_SHARE_WRITE,
        None,
        OPEN_EXISTING,
        0,
        None,
    )
    if handle == INVALID_HANDLE:
        raise Win32Error(f"CreateFileW failed: {ctypes.get_last_error()}")
    return handle


class Win32Transport:
    def __init__(self) -> None:
        if sys.platform != "win32":
            raise Win32Error("Win32 transport is Windows-only")
        self._kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self._hid = ctypes.WinDLL("hid.dll")
        self._feature = _open(_find_path(USAGE_PAGE_CONFIG))
        self._lcd = _open(_find_path(USAGE_PAGE_LCD))

    def set_feature(self, data: bytes) -> None:
        buf = (ctypes.c_ubyte * (REPORT_LEN + 1))(0, *pad64(data))
        returned = wt.DWORD(0)
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
            raise Win32Error(f"SET_FEATURE failed: {ctypes.get_last_error()}")

    def get_feature(self) -> bytes:
        buf = (ctypes.c_ubyte * (REPORT_LEN + 1))()
        returned = wt.DWORD(0)
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
            raise Win32Error(f"GET_FEATURE failed: {ctypes.get_last_error()}")
        return bytes(buf)[1:]

    def write_lcd_page(self, data: bytes) -> None:
        if len(data) != LCD_PAGE_BYTES:
            raise Win32Error("bad page size")
        buf = (ctypes.c_ubyte * (LCD_PAGE_BYTES + 1))(0, *data)
        written = wt.DWORD(0)
        ok = self._kernel.WriteFile(
            self._lcd, buf, LCD_PAGE_BYTES + 1, ctypes.byref(written), None
        )
        if not ok:
            raise Win32Error(f"WriteFile LCD failed: {ctypes.get_last_error()}")

    def read_lcd_ack(self, timeout_ms: int = 300) -> bytes:
        _ = timeout_ms
        buf = (ctypes.c_ubyte * (REPORT_LEN + 1))()
        read = wt.DWORD(0)
        ok = self._kernel.ReadFile(self._lcd, buf, REPORT_LEN + 1, ctypes.byref(read), None)
        if not ok:
            raise Win32Error(f"ReadFile LCD ACK failed: {ctypes.get_last_error()}")
        return bytes(buf)[1:]

    def close(self) -> None:
        for handle in (self._lcd, self._feature):
            if handle:
                self._kernel.CloseHandle(handle)
        self._lcd = None
        self._feature = None
