"""Emplacements des fichiers de l'application (données, logs, ressources)."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from app.config import APP_ID


def project_root() -> Path:
    """Racine du projet (ou dossier d'extraction PyInstaller une fois compilé)."""
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return Path(meipass)
    return Path(__file__).resolve().parents[2]


def resource_path(relative: str) -> Path:
    """Chemin d'une ressource embarquée (icône, etc.), en dev comme en .exe."""
    return project_root() / relative


def data_dir() -> Path:
    """%APPDATA%\\NPone29Cleaner : réglages de l'utilisateur."""
    base = os.environ.get("APPDATA") or str(Path.home())
    path = Path(base) / APP_ID
    path.mkdir(parents=True, exist_ok=True)
    return path


def log_dir() -> Path:
    """%LOCALAPPDATA%\\NPone29Cleaner\\logs : journaux de l'application."""
    base = os.environ.get("LOCALAPPDATA") or str(Path.home())
    path = Path(base) / APP_ID / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path
