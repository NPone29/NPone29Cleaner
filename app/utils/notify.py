"""Notifications Windows (zone de notification), clignotement de la barre des tâches et son.

On utilise l'API Shell_NotifyIcon : sur Windows 10/11, une « bulle » de la zone de
notification s'affiche comme une notification moderne, sans dépendance externe.
"""

from __future__ import annotations

import ctypes
import logging
from ctypes import wintypes
from pathlib import Path

log = logging.getLogger(__name__)

NIM_ADD, NIM_MODIFY, NIM_DELETE = 0, 1, 2
NIF_MESSAGE, NIF_ICON, NIF_TIP, NIF_INFO = 0x1, 0x2, 0x4, 0x10
NIIF_INFO, NIIF_WARNING, NIIF_ERROR, NIIF_USER, NIIF_LARGE_ICON = 0x1, 0x2, 0x3, 0x4, 0x20
IMAGE_ICON, LR_LOADFROMFILE, LR_DEFAULTSIZE = 1, 0x10, 0x40
FLASHW_ALL, FLASHW_TIMERNOFG = 0x3, 0xC


class GUID(ctypes.Structure):
    _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD), ("Data3", wintypes.WORD),
                ("Data4", ctypes.c_ubyte * 8)]


class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("hWnd", wintypes.HWND),
        ("uID", wintypes.UINT),
        ("uFlags", wintypes.UINT),
        ("uCallbackMessage", wintypes.UINT),
        ("hIcon", wintypes.HICON),
        ("szTip", wintypes.WCHAR * 128),
        ("dwState", wintypes.DWORD),
        ("dwStateMask", wintypes.DWORD),
        ("szInfo", wintypes.WCHAR * 256),
        ("uTimeoutOrVersion", wintypes.UINT),
        ("szInfoTitle", wintypes.WCHAR * 64),
        ("dwInfoFlags", wintypes.DWORD),
        ("guidItem", GUID),
        ("hBalloonIcon", wintypes.HICON),
    ]


class FLASHWINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.UINT), ("hwnd", wintypes.HWND), ("dwFlags", wintypes.DWORD),
                ("uCount", wintypes.UINT), ("dwTimeout", wintypes.DWORD)]


class WindowsNotifier:
    """Icône dans la zone de notification, utilisée pour afficher des notifications."""

    def __init__(self, hwnd: int, icon_path: Path | None, tooltip: str):
        self.hwnd = hwnd
        self._added = False
        self._shell = ctypes.windll.shell32
        self._shell.Shell_NotifyIconW.argtypes = [wintypes.DWORD, ctypes.POINTER(NOTIFYICONDATAW)]
        self._shell.Shell_NotifyIconW.restype = wintypes.BOOL
        user32 = ctypes.windll.user32
        user32.LoadImageW.restype = wintypes.HANDLE
        user32.LoadImageW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR, wintypes.UINT,
                                      ctypes.c_int, ctypes.c_int, wintypes.UINT]
        self.hicon = None
        if icon_path and icon_path.exists():
            self.hicon = user32.LoadImageW(None, str(icon_path), IMAGE_ICON, 0, 0, LR_LOADFROMFILE | LR_DEFAULTSIZE)
        if not self.hicon:
            user32.LoadIconW.restype = wintypes.HICON
            self.hicon = user32.LoadIconW(None, ctypes.c_void_p(32516))  # IDI_INFORMATION
        self.tooltip = tooltip[:127]

    def _data(self) -> NOTIFYICONDATAW:
        data = NOTIFYICONDATAW()
        data.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        data.hWnd = self.hwnd
        data.uID = 1
        data.hIcon = self.hicon
        data.szTip = self.tooltip
        return data

    def show(self, title: str, message: str, level: str = "info") -> bool:
        try:
            data = self._data()
            if not self._added:
                data.uFlags = NIF_ICON | NIF_TIP
                self._added = bool(self._shell.Shell_NotifyIconW(NIM_ADD, ctypes.byref(data)))
            data.uFlags = NIF_ICON | NIF_TIP | NIF_INFO
            data.szInfoTitle = title[:63]
            data.szInfo = message[:255]
            flags = {"warning": NIIF_WARNING, "error": NIIF_ERROR}.get(level, NIIF_USER | NIIF_LARGE_ICON)
            data.dwInfoFlags = flags
            if flags & NIIF_USER:
                data.hBalloonIcon = self.hicon
            return bool(self._shell.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(data)))
        except Exception:
            log.debug("Notification Windows impossible", exc_info=True)
            return False

    def remove(self) -> None:
        if self._added:
            try:
                self._shell.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(self._data()))
            except Exception:
                pass
            self._added = False


def is_foreground(hwnd: int) -> bool:
    try:
        return ctypes.windll.user32.GetForegroundWindow() == hwnd
    except Exception:
        return True


def flash_taskbar(hwnd: int) -> None:
    """Fait clignoter le bouton de la barre des tâches jusqu'à ce que l'utilisateur revienne."""
    try:
        info = FLASHWINFO(ctypes.sizeof(FLASHWINFO), hwnd, FLASHW_ALL | FLASHW_TIMERNOFG, 0, 0)
        ctypes.windll.user32.FlashWindowEx(ctypes.byref(info))
    except Exception:
        pass


def play_sound(level: str = "info") -> None:
    try:
        import winsound

        winsound.MessageBeep({"warning": winsound.MB_ICONEXCLAMATION,
                              "error": winsound.MB_ICONHAND}.get(level, winsound.MB_ICONASTERISK))
    except Exception:
        pass
