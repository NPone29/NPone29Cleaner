"""Liste des programmes installés (comme « Applications installées ») et désinstallation."""

from __future__ import annotations

import ctypes
import logging
import subprocess
import winreg
from dataclasses import dataclass
from datetime import datetime

log = logging.getLogger(__name__)

_UNINSTALL = r"Software\Microsoft\Windows\CurrentVersion\Uninstall"


@dataclass
class InstalledProgram:
    key: str
    name: str
    publisher: str
    version: str
    install_date: datetime | None
    size_bytes: int
    uninstall_string: str
    quiet_uninstall_string: str
    install_location: str
    scope: str  # "Utilisateur" ou "Système"

    @property
    def needs_admin(self) -> bool:
        return self.scope == "Système"


def _value(key, name: str, default=None):
    try:
        return winreg.QueryValueEx(key, name)[0]
    except OSError:
        return default


def parse_install_date(value) -> datetime | None:
    """InstallDate est généralement au format AAAAMMJJ."""
    if not value:
        return None
    text = str(value).strip()
    for fmt in ("%Y%m%d", "%d/%m/%Y", "%m/%d/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def _is_visible(key) -> bool:
    """Reprend les règles de Windows pour masquer composants système et correctifs."""
    if not _value(key, "DisplayName"):
        return False
    if _value(key, "SystemComponent", 0) == 1:
        return False
    if _value(key, "ParentKeyName") or _value(key, "ParentDisplayName"):
        return False
    if str(_value(key, "ReleaseType", "")).lower() in {"update", "hotfix", "security update"}:
        return False
    return bool(_value(key, "UninstallString") or _value(key, "QuietUninstallString"))


def list_installed_programs() -> list[InstalledProgram]:
    sources = [
        (winreg.HKEY_LOCAL_MACHINE, winreg.KEY_WOW64_64KEY, "Système"),
        (winreg.HKEY_LOCAL_MACHINE, winreg.KEY_WOW64_32KEY, "Système"),
        (winreg.HKEY_CURRENT_USER, 0, "Utilisateur"),
    ]
    programs: dict[tuple[str, str], InstalledProgram] = {}
    for hive, view, scope in sources:
        try:
            root = winreg.OpenKey(hive, _UNINSTALL, 0, winreg.KEY_READ | view)
        except OSError:
            continue
        with root:
            i = 0
            while True:
                try:
                    sub = winreg.EnumKey(root, i)
                except OSError:
                    break
                i += 1
                try:
                    with winreg.OpenKey(root, sub) as key:
                        if not _is_visible(key):
                            continue
                        name = str(_value(key, "DisplayName")).strip()
                        version = str(_value(key, "DisplayVersion", "") or "").strip()
                        size_kb = _value(key, "EstimatedSize", 0)
                        prog = InstalledProgram(
                            key=sub,
                            name=name,
                            publisher=str(_value(key, "Publisher", "") or "").strip(),
                            version=version,
                            install_date=parse_install_date(_value(key, "InstallDate")),
                            size_bytes=int(size_kb) * 1024 if isinstance(size_kb, int) else 0,
                            uninstall_string=str(_value(key, "UninstallString", "") or ""),
                            quiet_uninstall_string=str(_value(key, "QuietUninstallString", "") or ""),
                            install_location=str(_value(key, "InstallLocation", "") or ""),
                            scope=scope,
                        )
                        programs.setdefault((name.lower(), version), prog)
                except OSError:
                    continue
    return sorted(programs.values(), key=lambda p: p.name.lower())


def uninstall(program: InstalledProgram) -> None:
    """Lance le désinstalleur officiel du programme (avec son interface habituelle)."""
    command = program.uninstall_string or program.quiet_uninstall_string
    if not command:
        raise RuntimeError("Ce programme ne fournit pas de commande de désinstallation.")
    # Les désinstalleurs MSI utilisent parfois /I (réparer) : on force /X (désinstaller).
    if command.lower().startswith("msiexec") and " /i" in command.lower():
        idx = command.lower().index(" /i")
        command = command[:idx] + " /X" + command[idx + 3:]
    log.info("Désinstallation de %s : %s", program.name, command)
    try:
        # CreateProcess sait interpréter les chemins non entre guillemets de UninstallString.
        subprocess.Popen(command)
    except OSError as exc:
        if getattr(exc, "winerror", None) != 740:  # ERROR_ELEVATION_REQUIRED
            raise
        exe, params = split_command(command)
        rc = ctypes.windll.shell32.ShellExecuteW(None, "runas", exe, params, None, 1)
        if rc <= 32:
            raise RuntimeError("La désinstallation nécessite les droits administrateur.") from exc


def split_command(command: str) -> tuple[str, str]:
    """'"C:\\a b\\u.exe" /S' -> ('C:\\a b\\u.exe', '/S')."""
    from app.core.startup import extract_executable

    exe = extract_executable(command)
    rest = command.strip()
    if rest.startswith('"'):
        rest = rest[len(exe) + 2:]
    else:
        rest = rest[len(exe):]
    return exe, rest.strip()
