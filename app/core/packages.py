"""Installateurs officiels des fabricants (NVIDIA, Intel…) : téléchargement vérifié puis lancement.

Un `VendorPackage` décrit un installateur que l'application peut télécharger elle-même :
  * uniquement en HTTPS, depuis les domaines officiels du fabricant (redirections comprises) ;
  * empreinte du fichier comparée à celle publiée par le fabricant, quand il en publie une ;
  * signature numérique Authenticode valide ET au nom du fabricant attendu ;
  * l'installateur s'ouvre ensuite normalement : l'utilisateur suit ses étapes.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

from app.utils.fmt import human_size

log = logging.getLogger(__name__)

# Codes de sortie d'un installateur réussi (3010 / 1641 : redémarrage nécessaire).
SUCCESS_CODES = {0, 3010, 1641}
REBOOT_CODES = {3010, 1641}
ATTEMPTS_FILE = "installed_packages.json"


class VerificationError(Exception):
    """Le fichier téléchargé n'est pas celui publié par le fabricant : il n'est jamais lancé."""


@dataclass(frozen=True)
class VendorPackage:
    vendor: str                         # « NVIDIA », « Intel »
    title: str                          # « Intel® Wireless Wi-Fi Drivers 24.80.0 »
    version: str                        # version du pilote installé par ce paquet
    download_url: str
    allowed_hosts: tuple[str, ...]      # domaines officiels autorisés pour le téléchargement
    publisher: str                      # éditeur attendu de la signature (« Intel Corporation »)
    size: int = 0
    size_label: str = ""                # taille déjà mise en forme par la source (NVIDIA)
    sha1: str = ""
    sha256: str = ""
    page_url: str = ""
    advice: str = ""                    # conseil affiché avant le lancement de l'installateur

    @property
    def key(self) -> str:
        return self.download_url

    @property
    def host(self) -> str:
        return urlparse(self.download_url).hostname or ""

    @property
    def size_text(self) -> str:
        if self.size_label:
            return self.size_label
        return human_size(self.size) if self.size else "taille inconnue"


def version_in_place(package: VendorPackage, device_ids: list[str], checks: int = 3, delay: float = 2.0) -> str:
    """Version du pilote désormais utilisée par l'un de ces périphériques si elle atteint celle du paquet, sinon "".

    Le code de sortie d'un installateur ne suffit pas : le lanceur auto-extractible du pilote graphique Intel,
    par exemple, ne renvoie pas 0 alors que l'installation a réussi. Ce qui compte, c'est le pilote en place.
    """
    import time

    from app.core import drivers
    from app.core.driver_sources.base import compare_versions

    ids = [d for d in device_ids if d]
    if not ids or not package.version:
        return ""
    for attempt in range(checks):
        try:
            now = drivers.driver_version_of(ids)
        except Exception:
            log.warning("Version installée illisible après %s", package.title, exc_info=True)
            now = {}
        for version, _date in now.values():
            if version.strip() == package.version.strip() or compare_versions(version, package.version) in (0, 1):
                return version
        if attempt < checks - 1:
            time.sleep(delay)
    return ""


def file_hash(path: Path, algorithm: str) -> str:
    digest = hashlib.new(algorithm)
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def check_hash(path: Path, package: VendorPackage) -> None:
    """Compare l'empreinte du fichier à celle publiée par le fabricant (aucune si elle n'est pas publiée)."""
    for algorithm, expected in (("sha256", package.sha256), ("sha1", package.sha1)):
        if expected:
            actual = file_hash(path, algorithm)
            if actual.lower() != expected.strip().lower():
                raise VerificationError(f"L'empreinte du fichier téléchargé ({algorithm.upper()}) ne correspond pas à "
                                        f"celle publiée par {package.vendor}.")
            return


def download_verified(package: VendorPackage, progress: Callable[[int, int], None] | None = None,
                      cancel: threading.Event | None = None) -> Path:
    """Télécharge l'installateur puis vérifie empreinte et signature. Le fichier est supprimé en cas de doute."""
    from app.utils.download import download_file, verify_signature

    path = download_file(package.download_url, package.allowed_hosts, progress, cancel)
    try:
        check_hash(path, package)
        ok, detail = verify_signature(path, package.publisher)
        if not ok:
            raise VerificationError(f"Le fichier n'est pas signé par {package.publisher} ({detail}).")
    except Exception:
        path.unlink(missing_ok=True)
        raise
    log.info("Installateur vérifié : %s (%s)", path, package.title)
    return path


# --------------------------------------------------------------------------- installations déjà faites

def _attempts_path() -> Path:
    from app.core.driver_sources.base import cache_dir

    return cache_dir() / ATTEMPTS_FILE


def load_attempts(path: Path | None = None) -> dict[str, dict]:
    try:
        data = json.loads((path or _attempts_path()).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def remember_install(package: VendorPackage, devices: list[tuple[str, str]], path: Path | None = None,
                     now: datetime | None = None) -> None:
    """Note qu'un installateur a été exécuté avec succès pour ces périphériques (clé, version avant).

    Si Windows garde malgré tout l'ancien pilote (celui du fabricant du PC est prioritaire), la source ne
    reproposera pas indéfiniment la même version.
    """
    path = path or _attempts_path()
    data = load_attempts(path)
    when = (now or datetime.now()).isoformat(timespec="seconds")
    for key, version in devices:
        data[key] = {"vendor": package.vendor, "offered": package.version, "before": version, "when": when}
    try:
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)
    except OSError as exc:
        log.warning("Mémo des installations non enregistré : %s", exc)


def already_tried(attempts: dict[str, dict], key: str, offered: str, installed: str) -> bool:
    """Cette version a-t-elle déjà été installée sans que Windows change le pilote utilisé ?"""
    entry = attempts.get(key) or {}
    return entry.get("offered") == offered and entry.get("before") == installed
