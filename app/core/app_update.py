"""Mises à jour de NPone29 Cleaner lui-même, depuis les releases GitHub du projet.

Au démarrage, l'application lit la liste publique des releases (api.github.com). Si une version plus récente
existe, elle le propose ; rien n'est téléchargé ni installé sans l'accord de l'utilisateur.

Installation : l'installateur de la release (NPone29Cleaner-Setup-<version>.exe) est téléchargé en HTTPS depuis
github.com uniquement, son empreinte SHA-256 est comparée à celle calculée par GitHub, puis il est lancé en mode
silencieux ; il ferme l'application, remplace les fichiers et la rouvre. Version portable (sans installateur) :
la page de la release est ouverte pour télécharger le nouveau .exe.
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
import sys
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from app.config import APP_VERSION, UPDATE_REPO

log = logging.getLogger(__name__)

API_URL = "https://api.github.com/repos/{repo}/releases?per_page=20"
# github.com redirige le téléchargement vers le stockage de GitHub.
DOWNLOAD_HOSTS = (".github.com", ".release-assets.githubusercontent.com", ".objects.githubusercontent.com")
SETUP_RE = re.compile(r"^NPone29Cleaner-Setup-[\w.\-]+\.exe$", re.IGNORECASE)
PORTABLE_NAME = "NPone29Cleaner.exe"
INSTALLER_ARGS = ["/SILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CLOSEAPPLICATIONS", "/RELAUNCH=1"]

_VERSION_RE = re.compile(r"(\d+(?:\.\d+)*)(?:[-.]?([A-Za-z][\w.\-]*))?")


class UpdateError(Exception):
    pass


@dataclass(frozen=True)
class Asset:
    name: str
    url: str
    size: int
    sha256: str          # empreinte publiée par GitHub, "" si absente


@dataclass(frozen=True)
class Release:
    version: str
    tag: str
    title: str
    notes: str
    page_url: str
    prerelease: bool
    setup: Asset | None
    portable: Asset | None


# --------------------------------------------------------------------------- versions

def parse_version(text: str) -> tuple | None:
    """« v1.4.0 » -> ((1, 4, 0), (1,)) ; « 1.4.0-beta.2 » -> ((1, 4, 0), (0, 'beta.2')). None si illisible.

    Une pré-version (bêta, rc…) passe avant la version finale du même numéro.
    """
    match = _VERSION_RE.search(text.strip())
    if not match:
        return None
    numbers = tuple(int(n) for n in match.group(1).split("."))
    numbers = numbers + (0,) * (3 - len(numbers)) if len(numbers) < 3 else numbers
    suffix = match.group(2)
    return numbers, ((0, suffix.lower()) if suffix else (1,))


def is_newer(candidate: str, current: str = APP_VERSION) -> bool:
    new, old = parse_version(candidate), parse_version(current)
    return bool(new and old and new > old)


def is_prerelease_version(version: str) -> bool:
    parsed = parse_version(version)
    return bool(parsed and parsed[1][0] == 0)


# --------------------------------------------------------------------------- releases GitHub

def _asset(data: dict) -> Asset:
    digest = str(data.get("digest") or "")
    sha256 = digest.split(":", 1)[1] if digest.lower().startswith("sha256:") else ""
    return Asset(str(data.get("name") or ""), str(data.get("browser_download_url") or ""),
                 int(data.get("size") or 0), sha256.lower())


def parse_release(data: dict) -> Release | None:
    tag = str(data.get("tag_name") or "")
    if data.get("draft") or parse_version(tag) is None:
        return None
    assets = [_asset(a) for a in data.get("assets") or [] if isinstance(a, dict)]
    setup = next((a for a in assets if SETUP_RE.match(a.name)), None)
    portable = next((a for a in assets if a.name.lower() == PORTABLE_NAME.lower()), None)
    return Release(version=tag.lstrip("vV"), tag=tag, title=str(data.get("name") or tag),
                   notes=str(data.get("body") or "").strip(), page_url=str(data.get("html_url") or ""),
                   prerelease=bool(data.get("prerelease")), setup=setup, portable=portable)


def pick_update(releases: list[dict], current: str = APP_VERSION, include_prerelease: bool | None = None
                ) -> Release | None:
    """La release la plus récente, plus récente que `current`.

    Les pré-versions (bêta) ne sont proposées qu'à ceux qui utilisent déjà une pré-version, sauf choix explicite.
    """
    if include_prerelease is None:
        include_prerelease = is_prerelease_version(current)
    best: Release | None = None
    for data in releases:
        if not isinstance(data, dict):
            continue
        release = parse_release(data)
        if release is None or (release.prerelease and not include_prerelease):
            continue
        if not is_newer(release.version, current):
            continue
        if best is None or parse_version(release.version) > parse_version(best.version):
            best = release
    return best


def fetch_releases(repo: str = UPDATE_REPO, timeout: float = 15) -> list[dict]:
    from app.utils.download import USER_AGENT

    request = urllib.request.Request(API_URL.format(repo=repo),
                                     headers={"User-Agent": USER_AGENT, "Accept": "application/vnd.github+json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:   # noqa: S310 - adresse fixe
            data = json.loads(response.read(5 * 1024 * 1024).decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise UpdateError("Liste des versions introuvable (dépôt GitHub privé ou renommé ?).") from exc
        if exc.code in (403, 429):
            raise UpdateError("GitHub limite temporairement les vérifications : réessayez dans une heure.") from exc
        raise UpdateError(f"GitHub a répondu avec l'erreur {exc.code}.") from exc
    except (OSError, ValueError) as exc:
        raise UpdateError(f"Vérification impossible (connexion Internet ?) : {exc}") from exc
    if not isinstance(data, list):
        raise UpdateError("Réponse inattendue de GitHub.")
    return data


def check_for_update(current: str = APP_VERSION, repo: str = UPDATE_REPO) -> Release | None:
    release = pick_update(fetch_releases(repo), current)
    log.info("Mise à jour de l'application : %s", f"version {release.version} disponible" if release
             else f"{current} est la dernière version")
    return release


# --------------------------------------------------------------------------- installation

def install_kind(frozen: bool | None = None, executable: str | None = None) -> str:
    """« installed » (installateur, mise à jour automatique), « portable » (.exe seul) ou « dev » (Python)."""
    frozen = bool(getattr(sys, "frozen", False)) if frozen is None else frozen
    if not frozen:
        return "dev"
    exe = Path(executable or sys.executable)
    return "installed" if (exe.parent / "unins000.exe").exists() else "portable"


def can_install(release: Release, kind: str | None = None) -> bool:
    """Installation automatique possible : version installée et installateur avec empreinte publiée."""
    return (kind or install_kind()) == "installed" and release.setup is not None and bool(release.setup.sha256)


def _check_url(asset: Asset, repo: str) -> None:
    from urllib.parse import urlsplit

    from app.utils.download import is_allowed_url

    parts = urlsplit(asset.url)
    if not (is_allowed_url(asset.url, (".github.com",)) and parts.hostname == "github.com"
            and parts.path.lower().startswith(f"/{repo.lower()}/releases/download/")):
        raise UpdateError(f"Adresse de téléchargement inattendue : {asset.url}")


def download_installer(release: Release, progress: Callable[[int, int], None] | None = None,
                       cancel: threading.Event | None = None, repo: str = UPDATE_REPO) -> Path:
    """Télécharge l'installateur de la release et vérifie son empreinte. Le fichier est supprimé en cas de doute."""
    from app.core.packages import file_hash
    from app.utils.download import download_file

    asset = release.setup
    if asset is None or not asset.sha256:
        raise UpdateError("Cette version ne fournit pas d'installateur vérifiable.")
    _check_url(asset, repo)
    path = download_file(asset.url, DOWNLOAD_HOSTS, progress, cancel)
    try:
        if asset.size and path.stat().st_size != asset.size:
            raise UpdateError("Le fichier téléchargé n'a pas la taille annoncée par GitHub.")
        if file_hash(path, "sha256").lower() != asset.sha256:
            raise UpdateError("L'empreinte du fichier téléchargé ne correspond pas à celle publiée par GitHub.")
    except Exception:
        path.unlink(missing_ok=True)
        raise
    log.info("Installateur de la version %s vérifié : %s", release.version, path)
    return path


def own_processes() -> list[int]:
    """Processus de l'application à attendre avant de remplacer le .exe.

    Un .exe PyInstaller « un seul fichier » tourne en deux processus : le lanceur (parent) et l'application.
    """
    import os

    pids = [os.getpid()]
    try:
        import psutil

        parent = psutil.Process(os.getpid()).parent()
        if parent is not None and os.path.normcase(parent.exe()) == os.path.normcase(sys.executable):
            pids.append(parent.pid)
    except Exception:
        log.debug("Processus parent inconnu", exc_info=True)
    return pids


def installer_command(path: Path, wait_pids: list[int]) -> list[str]:
    return [str(path), *INSTALLER_ARGS, "/WAITPID=" + ",".join(str(p) for p in wait_pids)]


def launch_installer(path: Path, wait_pids: list[int] | None = None) -> None:
    """Lance l'installateur en mode silencieux.

    Il attend la fermeture de l'application (`wait_pids`), remplace ses fichiers puis la rouvre ; en cas
    d'échec, il rouvre l'ancienne version.
    """
    DETACHED_PROCESS = 0x00000008
    CREATE_NEW_PROCESS_GROUP = 0x00000200
    command = installer_command(path, own_processes() if wait_pids is None else wait_pids)
    subprocess.Popen(command, close_fds=True, creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP)
    log.info("Installateur lancé : %s", subprocess.list2cmdline(command))


def short_notes(notes: str, limit: int = 700) -> str:
    """Notes de version lisibles dans une boîte de dialogue (sans balises Markdown, tronquées)."""
    text = re.sub(r"<!--.*?-->", "", notes, flags=re.S)
    text = re.sub(r"^#+\s*", "", text, flags=re.M)
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text if len(text) <= limit else text[:limit].rsplit("\n", 1)[0].rstrip() + "\n…"
