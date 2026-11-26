"""Appels directs à l'API Windows via ctypes (corbeille, suppression vers la corbeille)."""

from __future__ import annotations

import ctypes
import logging
from ctypes import wintypes
from pathlib import Path

log = logging.getLogger(__name__)

# shellapi.h utilise un alignement sur 1 octet en 32 bits, l'alignement naturel en 64 bits.
_PACK = 1 if ctypes.sizeof(ctypes.c_void_p) == 4 else 8


class SHQUERYRBINFO(ctypes.Structure):
    _pack_ = _PACK
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("i64Size", ctypes.c_longlong),
        ("i64NumItems", ctypes.c_longlong),
    ]


class SHFILEOPSTRUCTW(ctypes.Structure):
    _pack_ = _PACK
    _fields_ = [
        ("hwnd", wintypes.HWND),
        ("wFunc", wintypes.UINT),
        ("pFrom", wintypes.LPCWSTR),
        ("pTo", wintypes.LPCWSTR),
        ("fFlags", ctypes.c_ushort),
        ("fAnyOperationsAborted", wintypes.BOOL),
        ("hNameMappings", ctypes.c_void_p),
        ("lpszProgressTitle", wintypes.LPCWSTR),
    ]


SHERB_NOCONFIRMATION = 0x1
SHERB_NOPROGRESSUI = 0x2
SHERB_NOSOUND = 0x4

FO_DELETE = 3
FOF_SILENT = 0x4
FOF_NOCONFIRMATION = 0x10
FOF_ALLOWUNDO = 0x40
FOF_NOERRORUI = 0x400


def _shell32():
    lib = ctypes.windll.shell32
    lib.SHQueryRecycleBinW.restype = ctypes.c_long
    lib.SHEmptyRecycleBinW.restype = ctypes.c_long
    lib.SHFileOperationW.restype = ctypes.c_int
    return lib


def query_recycle_bin() -> tuple[int, int]:
    """Renvoie (taille en octets, nombre d'éléments) de la corbeille, tous lecteurs confondus."""
    info = SHQUERYRBINFO()
    info.cbSize = ctypes.sizeof(SHQUERYRBINFO)
    hr = _shell32().SHQueryRecycleBinW(None, ctypes.byref(info))
    if hr != 0:
        log.warning("SHQueryRecycleBinW a échoué : 0x%08X", hr & 0xFFFFFFFF)
        return 0, 0
    return int(info.i64Size), int(info.i64NumItems)


def empty_recycle_bin() -> bool:
    """Vide la corbeille sans confirmation. Renvoie True si l'opération a réussi (ou déjà vide)."""
    hr = _shell32().SHEmptyRecycleBinW(None, None, SHERB_NOCONFIRMATION | SHERB_NOPROGRESSUI | SHERB_NOSOUND)
    # 0x8000FFFF (E_UNEXPECTED) est renvoyé quand la corbeille est déjà vide.
    return hr == 0 or (hr & 0xFFFFFFFF) == 0x8000FFFF


def send_to_recycle_bin(path: str | Path) -> bool:
    """Envoie un fichier ou dossier à la corbeille (récupérable). Renvoie True si réussi."""
    op = SHFILEOPSTRUCTW()
    op.hwnd = None
    op.wFunc = FO_DELETE
    # pFrom doit se terminer par deux caractères nuls : ctypes en ajoute un, on ajoute l'autre.
    op.pFrom = str(Path(path).resolve()) + "\0"
    op.pTo = None
    op.fFlags = FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_SILENT | FOF_NOERRORUI
    rc = _shell32().SHFileOperationW(ctypes.byref(op))
    if rc != 0 or op.fAnyOperationsAborted:
        log.warning("SHFileOperationW(%s) a échoué : %s", path, rc)
        return False
    return True


# --------------------------------------------------------------------------- lancer un programme et attendre

class SHELLEXECUTEINFOW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("fMask", ctypes.c_ulong),
        ("hwnd", wintypes.HWND),
        ("lpVerb", wintypes.LPCWSTR),
        ("lpFile", wintypes.LPCWSTR),
        ("lpParameters", wintypes.LPCWSTR),
        ("lpDirectory", wintypes.LPCWSTR),
        ("nShow", ctypes.c_int),
        ("hInstApp", wintypes.HINSTANCE),
        ("lpIDList", ctypes.c_void_p),
        ("lpClass", wintypes.LPCWSTR),
        ("hkeyClass", wintypes.HKEY),
        ("dwHotKey", wintypes.DWORD),
        ("hIconOrMonitor", wintypes.HANDLE),
        ("hProcess", wintypes.HANDLE),
    ]


SEE_MASK_NOCLOSEPROCESS = 0x00000040
SEE_MASK_NOASYNC = 0x00000100
SW_SHOWNORMAL = 1
INFINITE = 0xFFFFFFFF


def launch_and_wait(path: str | Path) -> int | None:
    """Lance un programme comme un double-clic (la demande d'élévation UAC s'affiche si besoin) et attend
    sa fermeture. Renvoie son code de sortie, ou None s'il n'a pas pu être lancé (refus UAC…).

    Bloquant : à appeler depuis un thread de travail, jamais depuis l'interface.
    """
    info = SHELLEXECUTEINFOW()
    info.cbSize = ctypes.sizeof(SHELLEXECUTEINFOW)
    info.fMask = SEE_MASK_NOCLOSEPROCESS | SEE_MASK_NOASYNC
    info.lpVerb = "open"
    info.lpFile = str(path)
    info.lpDirectory = str(Path(path).parent)
    info.nShow = SW_SHOWNORMAL
    shell32 = ctypes.windll.shell32
    kernel32 = ctypes.windll.kernel32
    shell32.ShellExecuteExW.argtypes = [ctypes.POINTER(SHELLEXECUTEINFOW)]
    shell32.ShellExecuteExW.restype = wintypes.BOOL
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    if not shell32.ShellExecuteExW(ctypes.byref(info)):
        log.warning("Lancement impossible de %s (erreur %d)", path, ctypes.GetLastError())
        return None
    if not info.hProcess:
        return 0       # programme déjà ouvert par un autre processus : rien à attendre
    try:
        kernel32.WaitForSingleObject(info.hProcess, INFINITE)
        code = wintypes.DWORD(0)
        kernel32.GetExitCodeProcess(info.hProcess, ctypes.byref(code))
        return int(code.value)
    finally:
        kernel32.CloseHandle(info.hProcess)
