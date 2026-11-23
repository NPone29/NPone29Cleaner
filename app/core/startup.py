"""Programmes lancés au démarrage de Windows : liste, activation, désactivation.

On active/désactive exactement comme le Gestionnaire des tâches, via les clés
« StartupApproved » : rien n'est supprimé, tout reste réversible.
"""

from __future__ import annotations

import logging
import os
import struct
import time
import winreg
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

_RUN = r"Software\Microsoft\Windows\CurrentVersion\Run"
_RUN_WOW = r"Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Run"
_APPROVED = r"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved"

HIVE_NAMES = {winreg.HKEY_CURRENT_USER: "HKCU", winreg.HKEY_LOCAL_MACHINE: "HKLM"}


@dataclass
class StartupEntry:
    name: str
    command: str
    location: str           # texte lisible
    hive: int               # HKCU ou HKLM (où se trouve la clé StartupApproved)
    approved_subkey: str    # Run, Run32 ou StartupFolder
    enabled: bool
    file_path: str = ""     # chemin du raccourci pour le dossier Démarrage

    @property
    def needs_admin(self) -> bool:
        return self.hive == winreg.HKEY_LOCAL_MACHINE

    @property
    def executable(self) -> str:
        return extract_executable(self.command)


def extract_executable(command: str) -> str:
    """'"C:\\a b\\x.exe" --arg' -> 'C:\\a b\\x.exe'."""
    cmd = os.path.expandvars(command.strip())
    if cmd.startswith('"'):
        end = cmd.find('"', 1)
        return cmd[1:end] if end > 0 else cmd[1:]
    lower = cmd.lower()
    for ext in (".exe", ".bat", ".cmd", ".lnk", ".com"):
        idx = lower.find(ext)
        if idx > 0:
            return cmd[: idx + len(ext)]
    return cmd.split(" ")[0]


def is_enabled_value(data: bytes | None) -> bool:
    """Le premier octet de la valeur StartupApproved est pair (02) si activé, impair (03) si désactivé."""
    if not data:
        return True
    return data[0] % 2 == 0


def make_approved_value(enabled: bool) -> bytes:
    if enabled:
        return bytes([2]) + bytes(11)
    # 03 00 00 00 + FILETIME de la désactivation (comme le Gestionnaire des tâches).
    filetime = int((time.time() + 11644473600) * 10_000_000)
    return bytes([3, 0, 0, 0]) + struct.pack("<Q", filetime)


def _read_values(hive: int, path: str, wow64_32: bool = False) -> dict[str, object]:
    access = winreg.KEY_READ | (winreg.KEY_WOW64_64KEY if not wow64_32 else 0)
    values: dict[str, object] = {}
    try:
        with winreg.OpenKey(hive, path, 0, access) as key:
            i = 0
            while True:
                try:
                    name, data, _type = winreg.EnumValue(key, i)
                except OSError:
                    break
                values[name] = data
                i += 1
    except OSError:
        pass
    return values


def _approved_map(hive: int, subkey: str) -> dict[str, object]:
    return _read_values(hive, f"{_APPROVED}\\{subkey}")


def _startup_folders() -> list[tuple[Path, int, str]]:
    appdata = os.environ.get("APPDATA", "")
    progdata = os.environ.get("PROGRAMDATA", r"C:\ProgramData")
    return [
        (Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup",
         winreg.HKEY_CURRENT_USER, "Dossier Démarrage (utilisateur)"),
        (Path(progdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "StartUp",
         winreg.HKEY_LOCAL_MACHINE, "Dossier Démarrage (tous les utilisateurs)"),
    ]


def list_startup_entries() -> list[StartupEntry]:
    entries: list[StartupEntry] = []
    sources = [
        (winreg.HKEY_CURRENT_USER, _RUN, "Run", "Registre (utilisateur)"),
        (winreg.HKEY_LOCAL_MACHINE, _RUN, "Run", "Registre (tous les utilisateurs)"),
        (winreg.HKEY_LOCAL_MACHINE, _RUN_WOW, "Run32", "Registre 32 bits (tous les utilisateurs)"),
    ]
    for hive, path, approved_subkey, label in sources:
        approved = _approved_map(hive, approved_subkey)
        for name, command in _read_values(hive, path).items():
            if not name:
                continue
            entries.append(StartupEntry(
                name=name, command=str(command), location=label, hive=hive,
                approved_subkey=approved_subkey,
                enabled=is_enabled_value(approved.get(name)),  # type: ignore[arg-type]
            ))

    for folder, hive, label in _startup_folders():
        if not folder.is_dir():
            continue
        approved = _approved_map(hive, "StartupFolder")
        try:
            files = sorted(folder.iterdir())
        except OSError:
            continue
        for file in files:
            if file.name.lower() == "desktop.ini" or not file.is_file():
                continue
            entries.append(StartupEntry(
                name=file.stem, command=str(file), location=label, hive=hive,
                approved_subkey="StartupFolder",
                enabled=is_enabled_value(approved.get(file.name)),  # type: ignore[arg-type]
                file_path=str(file),
            ))
    entries.sort(key=lambda e: e.name.lower())
    return entries


def set_enabled(entry: StartupEntry, enabled: bool) -> None:
    """Active/désactive une entrée. Lève PermissionError si les droits admin manquent."""
    value_name = Path(entry.file_path).name if entry.file_path else entry.name
    key_path = f"{_APPROVED}\\{entry.approved_subkey}"
    with winreg.CreateKeyEx(entry.hive, key_path, 0, winreg.KEY_SET_VALUE | winreg.KEY_WOW64_64KEY) as key:
        winreg.SetValueEx(key, value_name, 0, winreg.REG_BINARY, make_approved_value(enabled))
    entry.enabled = enabled
    log.info("Démarrage : %s %s", entry.name, "activé" if enabled else "désactivé")
