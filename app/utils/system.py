"""Fonctions système : droits admin, exécution de commandes, ouverture de fichiers."""

from __future__ import annotations

import base64
import ctypes
import logging
import os
import subprocess
import sys
import webbrowser
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

# Empêche l'ouverture d'une fenêtre console noire pour les commandes lancées en arrière-plan.
CREATE_NO_WINDOW = 0x08000000
CREATE_NEW_CONSOLE = 0x00000010


@dataclass
class CommandResult:
    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0

    @property
    def output(self) -> str:
        return (self.stdout + ("\n" + self.stderr if self.stderr else "")).strip()


def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def relaunch_as_admin() -> bool:
    """Relance l'application avec élévation UAC. Renvoie True si la relance a démarré."""
    if getattr(sys, "frozen", False):
        exe = sys.executable
        params = subprocess.list2cmdline(sys.argv[1:])
    else:
        exe = sys.executable
        params = subprocess.list2cmdline([os.path.abspath(sys.argv[0]), *sys.argv[1:]])
    rc = ctypes.windll.shell32.ShellExecuteW(None, "runas", exe, params, os.getcwd(), 1)
    return rc > 32


def decode_output(data: bytes | None) -> str:
    """Décode la sortie d'un outil console (UTF-8, sinon page de code OEM type cp850)."""
    if not data:
        return ""
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        try:
            return data.decode("oem")
        except (UnicodeDecodeError, LookupError):
            return data.decode("mbcs", errors="replace")


def run_command(args: list[str] | str, timeout: float | None = None) -> CommandResult:
    """Lance une commande sans fenêtre et récupère sa sortie."""
    log.debug("run_command: %s", args)
    try:
        proc = subprocess.run(
            args,
            capture_output=True,
            timeout=timeout,
            creationflags=CREATE_NO_WINDOW,
            stdin=subprocess.DEVNULL,
        )
    except FileNotFoundError as exc:
        return CommandResult(-1, "", f"Programme introuvable : {exc}")
    except subprocess.TimeoutExpired:
        return CommandResult(-2, "", "Délai d'attente dépassé.")
    return CommandResult(proc.returncode, decode_output(proc.stdout), decode_output(proc.stderr))


def run_powershell(script: str, timeout: float | None = None) -> CommandResult:
    """Exécute un script PowerShell (Windows PowerShell 5.1) sans fenêtre, sortie UTF-8."""
    full = "[Console]::OutputEncoding = [Text.Encoding]::UTF8\n$ProgressPreference = 'SilentlyContinue'\n" + script
    encoded = base64.b64encode(full.encode("utf-16-le")).decode("ascii")
    return run_command(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-EncodedCommand", encoded],
        timeout=timeout,
    )


def launch_console(command: str, elevated: bool = False) -> bool:
    """Ouvre une console visible qui exécute `command` (ex. sfc /scannow)."""
    verb = "runas" if elevated and not is_admin() else "open"
    rc = ctypes.windll.shell32.ShellExecuteW(None, verb, "cmd.exe", f"/k {command}", None, 1)
    return rc > 32


def open_path(path: str | Path) -> None:
    os.startfile(str(path))  # noqa: S606 - ouverture volontaire par l'utilisateur


def reveal_in_explorer(path: str | Path) -> None:
    """Ouvre l'Explorateur avec le fichier sélectionné."""
    p = Path(path)
    if p.exists() and p.is_file():
        subprocess.Popen(f'explorer.exe /select,"{p}"')
    elif p.exists():
        subprocess.Popen(["explorer.exe", str(p)])
    elif p.parent.exists():
        subprocess.Popen(["explorer.exe", str(p.parent)])


def open_url(url: str) -> None:
    webbrowser.open(url)
