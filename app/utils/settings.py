"""Réglages persistants de l'utilisateur, stockés en JSON dans %APPDATA%."""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Any

from app.utils.paths import data_dir

log = logging.getLogger(__name__)

DEFAULTS: dict[str, Any] = {
    "appearance": "dark",            # dark | light | system
    "cleaner_selection": {},         # id de cible -> coché ou non
    "create_restore_point": True,    # avant l'installation de pilotes
    "drivers_third_party_only": True,
    "bigfiles_min_mb": 100,
    "backup_sources": None,          # dossiers cochés (None = choix par défaut)
    "backup_custom": [],             # dossiers ajoutés par l'utilisateur
    "backup_destination": "",
    "backup_mode": "incremental",    # full | differential | incremental
    "backup_keep_full": 0,           # 0 = tout garder
    "backup_last": {},               # dernière sauvegarde réussie (date, type, destination)
    "backup_auto": False,            # sauvegarde automatique (tâche planifiée Windows)
    "backup_auto_hours": 24,         # fréquence en heures (1, 2, 4, 6, 12, 24, 168)
    "backup_auto_mode": "incremental",
    "backup_auto_full_weeks": 0,     # une complète toutes les N semaines (0 = jamais)
}


class Settings:
    def __init__(self, path: Path | None = None):
        self.path = path or (data_dir() / "settings.json")
        self._lock = threading.Lock()
        self._data: dict[str, Any] = dict(DEFAULTS)
        self.load()

    def load(self) -> None:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                self._data.update(raw)
        except FileNotFoundError:
            pass
        except (OSError, ValueError) as exc:
            log.warning("Réglages illisibles (%s), valeurs par défaut utilisées.", exc)

    def save(self) -> None:
        with self._lock:
            try:
                tmp = self.path.with_suffix(".tmp")
                tmp.write_text(json.dumps(self._data, indent=2, ensure_ascii=False), encoding="utf-8")
                tmp.replace(self.path)
            except OSError as exc:
                log.warning("Impossible d'enregistrer les réglages : %s", exc)

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, DEFAULTS.get(key, default))

    def set(self, key: str, value: Any, save: bool = True) -> None:
        self._data[key] = value
        if save:
            self.save()
