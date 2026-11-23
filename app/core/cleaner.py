"""Nettoyage des fichiers inutiles : temporaires, caches, corbeille, journaux…

Chaque emplacement à nettoyer est décrit par une `CleanTarget`. L'analyse (`scan_target`)
ne supprime rien ; le nettoyage (`clean_target`) supprime uniquement le *contenu* des
dossiers ciblés, sans jamais suivre les liens symboliques ni les jonctions.
"""

from __future__ import annotations

import fnmatch
import logging
import os
import stat
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterator

from app.utils import winapi
from app.utils.system import run_command

log = logging.getLogger(__name__)

Locator = Callable[[], list[Path]]


@dataclass
class CleanTarget:
    id: str
    name: str
    category: str
    description: str
    locate: Locator = field(default=lambda: [])
    admin: bool = False              # nécessite les droits administrateur
    default: bool = True             # coché par défaut
    min_age_hours: float = 0.0       # ignore les fichiers modifiés plus récemment
    patterns: tuple[str, ...] = ()   # filtre sur le nom de fichier (fnmatch, minuscules)
    recursive: bool = True
    kind: str = "files"              # "files" ou "recycle_bin"
    before_clean: Callable[[], object] | None = None
    after_clean: Callable[[object], None] | None = None


@dataclass
class ScanResult:
    target_id: str
    size: int = 0
    files: int = 0


@dataclass
class CleanResult:
    target_id: str
    freed: int = 0
    deleted: int = 0
    failed: int = 0


# --------------------------------------------------------------------------- parcours

def _excluded_roots(extra: list[str] | None = None) -> list[str]:
    """Dossiers à ne jamais toucher (ex. le dossier d'extraction de notre propre .exe)."""
    roots = list(extra or [])
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        roots.append(meipass)
    return [os.path.normcase(os.path.abspath(r)) for r in roots]


def _is_excluded(path: str, excluded: list[str]) -> bool:
    if not excluded:
        return False
    p = os.path.normcase(os.path.abspath(path))
    return any(p == e or p.startswith(e + os.sep) for e in excluded)


def _is_link(entry: os.DirEntry) -> bool:
    try:
        if entry.is_symlink():
            return True
        is_junction = getattr(entry, "is_junction", None)
        return bool(is_junction and is_junction())
    except OSError:
        return True


def _matches(name: str, patterns: tuple[str, ...]) -> bool:
    if not patterns:
        return True
    lower = name.lower()
    return any(fnmatch.fnmatch(lower, p) for p in patterns)


def _walk_files(
    root: str,
    recursive: bool,
    patterns: tuple[str, ...],
    cutoff: float | None,
    excluded: list[str],
    cancel: threading.Event | None,
) -> Iterator[tuple[str, int]]:
    stack = [root]
    while stack:
        if cancel is not None and cancel.is_set():
            return
        current = stack.pop()
        if _is_excluded(current, excluded):
            continue
        try:
            with os.scandir(current) as it:
                entries = list(it)
        except OSError:
            continue
        for entry in entries:
            try:
                if _is_link(entry):
                    continue
                if entry.is_dir(follow_symlinks=False):
                    if recursive:
                        stack.append(entry.path)
                    continue
                if not entry.is_file(follow_symlinks=False) or not _matches(entry.name, patterns):
                    continue
                st = entry.stat(follow_symlinks=False)
                if cutoff is not None and st.st_mtime > cutoff:
                    continue
                yield entry.path, st.st_size
            except OSError:
                continue


def iter_target_files(
    target: CleanTarget,
    cancel: threading.Event | None = None,
    excluded: list[str] | None = None,
) -> Iterator[tuple[str, int]]:
    """Fichiers (chemin, taille) qui seraient supprimés pour cette cible."""
    excluded = _excluded_roots(excluded)
    cutoff = time.time() - target.min_age_hours * 3600 if target.min_age_hours > 0 else None
    seen: set[str] = set()
    for root in _safe_locate(target):
        root_s = str(root)
        key = os.path.normcase(os.path.abspath(root_s))
        if key in seen or _is_excluded(root_s, excluded):
            continue
        seen.add(key)
        try:
            if os.path.islink(root_s):
                continue
            if os.path.isfile(root_s):
                st = os.stat(root_s)
                if cutoff is None or st.st_mtime <= cutoff:
                    yield root_s, st.st_size
            elif os.path.isdir(root_s):
                yield from _walk_files(root_s, target.recursive, target.patterns, cutoff, excluded, cancel)
        except OSError:
            continue


def _safe_locate(target: CleanTarget) -> list[Path]:
    try:
        return list(target.locate())
    except Exception:  # un localisateur ne doit jamais faire planter l'analyse
        log.exception("Localisation impossible pour %s", target.id)
        return []


# --------------------------------------------------------------------------- suppression

def _delete_file(path: str) -> bool:
    try:
        os.remove(path)
        return True
    except FileNotFoundError:
        return True
    except PermissionError:
        # Fichier en lecture seule : on retire l'attribut puis on réessaie.
        try:
            os.chmod(path, stat.S_IWRITE)
            os.remove(path)
            return True
        except OSError:
            return False
    except OSError:
        return False


def _prune_empty_dirs(root: str, cutoff: float | None, excluded: list[str]) -> None:
    """Supprime les sous-dossiers vides (jamais `root` lui-même)."""

    def visit(path: str) -> bool:
        """Renvoie True si `path` a été supprimé."""
        if _is_excluded(path, excluded):
            return False
        try:
            with os.scandir(path) as it:
                entries = list(it)
        except OSError:
            return False
        remaining = 0
        for entry in entries:
            try:
                if not _is_link(entry) and entry.is_dir(follow_symlinks=False):
                    if visit(entry.path):
                        continue
                remaining += 1
            except OSError:
                remaining += 1
        if remaining or path == root:
            return False
        try:
            if cutoff is not None and os.stat(path).st_mtime > cutoff:
                return False
            os.rmdir(path)
            return True
        except OSError:
            return False

    visit(root)


# --------------------------------------------------------------------------- API publique

def scan_target(target: CleanTarget, cancel: threading.Event | None = None) -> ScanResult:
    result = ScanResult(target.id)
    if target.kind == "recycle_bin":
        result.size, result.files = winapi.query_recycle_bin()
        return result
    for _path, size in iter_target_files(target, cancel):
        result.size += size
        result.files += 1
    return result


def clean_target(target: CleanTarget, cancel: threading.Event | None = None) -> CleanResult:
    result = CleanResult(target.id)
    if target.kind == "recycle_bin":
        size, count = winapi.query_recycle_bin()
        if count and winapi.empty_recycle_bin():
            result.freed, result.deleted = size, count
        elif count:
            result.failed = count
        return result

    state = None
    if target.before_clean:
        try:
            state = target.before_clean()
        except Exception:
            log.exception("Préparation du nettoyage de %s impossible", target.id)
    try:
        for path, size in iter_target_files(target, cancel):
            if _delete_file(path):
                result.freed += size
                result.deleted += 1
            else:
                result.failed += 1
        if target.recursive and not target.patterns and not (cancel and cancel.is_set()):
            cutoff = time.time() - target.min_age_hours * 3600 if target.min_age_hours > 0 else None
            excluded = _excluded_roots()
            for root in _safe_locate(target):
                if root.is_dir() and not root.is_symlink():
                    _prune_empty_dirs(str(root), cutoff, excluded)
    finally:
        if target.after_clean:
            try:
                target.after_clean(state)
            except Exception:
                log.exception("Finalisation du nettoyage de %s impossible", target.id)
    log.info("Nettoyage %s : %d fichiers, %d octets libérés, %d échecs",
             target.id, result.deleted, result.freed, result.failed)
    return result


# --------------------------------------------------------------------------- emplacements

def _env_path(name: str, fallback: str = "") -> Path:
    return Path(os.environ.get(name) or fallback)


def _local() -> Path:
    return _env_path("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))


def _roaming() -> Path:
    return _env_path("APPDATA", str(Path.home() / "AppData" / "Roaming"))


def _windir() -> Path:
    return _env_path("WINDIR", r"C:\Windows")


def _programdata() -> Path:
    return _env_path("PROGRAMDATA", r"C:\ProgramData")


def _existing(*paths: Path) -> list[Path]:
    return [p for p in paths if p.exists()]


_CHROMIUM_CACHE_DIRS = ("Cache", "Code Cache", "GPUCache", "DawnGraphiteCache", "DawnWebGPUCache")
_CHROMIUM_SHARED_DIRS = ("ShaderCache", "GrShaderCache", "GraphiteDawnCache")


def chromium_cache_dirs(user_data: Path) -> list[Path]:
    """Caches de tous les profils d'un navigateur basé sur Chromium (Chrome, Edge, Brave…)."""
    if not user_data.is_dir():
        return []
    found: list[Path] = []
    try:
        candidates = [d for d in user_data.iterdir() if d.is_dir()]
    except OSError:
        return []
    for profile in candidates:
        if profile.name == "Default" or profile.name.startswith("Profile ") or profile.name == "Guest Profile":
            found.extend(_existing(*(profile / sub for sub in _CHROMIUM_CACHE_DIRS)))
    found.extend(_existing(*(user_data / sub for sub in _CHROMIUM_SHARED_DIRS)))
    return found


def _single_profile_caches(*roots: Path) -> list[Path]:
    """Applications Electron/Chromium à profil unique (Discord, VS Code, Opera…)."""
    found: list[Path] = []
    for root in roots:
        found.extend(_existing(*(root / sub for sub in _CHROMIUM_CACHE_DIRS)))
    return found


def firefox_cache_dirs() -> list[Path]:
    profiles = _local() / "Mozilla" / "Firefox" / "Profiles"
    if not profiles.is_dir():
        return []
    found: list[Path] = []
    try:
        for profile in profiles.iterdir():
            found.extend(_existing(profile / "cache2", profile / "startupCache", profile / "thumbnails"))
    except OSError:
        pass
    return found


def _steam_dirs() -> list[Path]:
    found = _existing(_local() / "Steam" / "htmlcache")
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as key:
            steam = Path(winreg.QueryValueEx(key, "SteamPath")[0])
        found.extend(_existing(steam / "appcache" / "httpcache"))
    except OSError:
        pass
    return found


def _stop_update_services() -> list[str]:
    """Arrête Windows Update/BITS pour libérer le cache ; renvoie les services à relancer."""
    import psutil

    stopped: list[str] = []
    for name in ("wuauserv", "bits"):
        try:
            if psutil.win_service_get(name).status() == "running":
                if run_command(["net", "stop", name], timeout=90).ok:
                    stopped.append(name)
        except Exception:
            continue
    return stopped


def _restart_services(stopped: object) -> None:
    for name in reversed(list(stopped or [])):
        run_command(["net", "start", name], timeout=90)


CATEGORY_ORDER = ["Windows", "Navigateurs", "Applications", "Développement"]

# Applications dont les fichiers sont verrouillés tant qu'elles sont ouvertes.
RUNNING_APPS: dict[str, tuple[str, tuple[str, ...]]] = {
    "chrome": ("Google Chrome", ("chrome.exe",)),
    "edge": ("Microsoft Edge", ("msedge.exe",)),
    "brave": ("Brave", ("brave.exe",)),
    "vivaldi": ("Vivaldi", ("vivaldi.exe",)),
    "opera": ("Opera", ("opera.exe", "opera_gx.exe")),
    "firefox": ("Firefox", ("firefox.exe",)),
    "discord": ("Discord", ("discord.exe", "discordptb.exe", "discordcanary.exe")),
    "teams_classic": ("Microsoft Teams", ("teams.exe",)),
    "steam": ("Steam", ("steam.exe", "steamwebhelper.exe")),
    "spotify_cache": ("Spotify", ("spotify.exe",)),
    "vscode": ("Visual Studio Code", ("code.exe",)),
}


def running_blockers(target_ids: list[str]) -> list[str]:
    """Noms des applications ouvertes qui empêcheront de nettoyer entièrement les cibles choisies."""
    import psutil

    wanted = {tid: RUNNING_APPS[tid] for tid in target_ids if tid in RUNNING_APPS}
    if not wanted:
        return []
    running = set()
    for proc in psutil.process_iter(["name"]):
        name = (proc.info.get("name") or "").lower()
        if name:
            running.add(name)
    return [label for label, exes in wanted.values() if any(exe in running for exe in exes)]


def build_targets() -> list[CleanTarget]:
    """Liste de toutes les cibles connues (celles absentes du PC seront simplement vides)."""
    local, roaming, windir, progdata = _local(), _roaming(), _windir(), _programdata()
    user_temp = Path(tempfile.gettempdir())

    return [
        # ---------------------------------------------------------------- Windows
        CleanTarget("recycle_bin", "Corbeille", "Windows",
                    "Vide la corbeille de tous les lecteurs.", kind="recycle_bin"),
        CleanTarget("user_temp", "Fichiers temporaires (utilisateur)", "Windows",
                    "Dossier %TEMP% — fichiers de plus d'une heure.",
                    lambda: _existing(user_temp), min_age_hours=1),
        CleanTarget("windows_temp", "Fichiers temporaires (Windows)", "Windows",
                    r"C:\Windows\Temp — fichiers de plus d'une heure.",
                    lambda: _existing(windir / "Temp"), admin=True, min_age_hours=1),
        CleanTarget("thumbnails", "Cache des miniatures", "Windows",
                    "Aperçus d'images de l'Explorateur (recréés automatiquement).",
                    lambda: _existing(local / "Microsoft" / "Windows" / "Explorer"),
                    patterns=("thumbcache_*.db",), recursive=False),
        CleanTarget("crash_dumps", "Rapports de plantage", "Windows",
                    "Fichiers .dmp et rapports d'erreurs Windows (WER).",
                    lambda: _existing(local / "CrashDumps",
                                      local / "Microsoft" / "Windows" / "WER" / "ReportArchive",
                                      local / "Microsoft" / "Windows" / "WER" / "ReportQueue")),
        CleanTarget("system_crash_dumps", "Rapports de plantage (système)", "Windows",
                    "Minidumps, MEMORY.DMP et rapports d'erreurs système.",
                    lambda: _existing(windir / "Minidump", windir / "MEMORY.DMP",
                                      progdata / "Microsoft" / "Windows" / "WER" / "ReportArchive",
                                      progdata / "Microsoft" / "Windows" / "WER" / "ReportQueue"),
                    admin=True),
        CleanTarget("windows_update", "Cache de Windows Update", "Windows",
                    "Mises à jour déjà installées et téléchargées (SoftwareDistribution).",
                    lambda: _existing(windir / "SoftwareDistribution" / "Download"), admin=True,
                    before_clean=_stop_update_services, after_clean=_restart_services),
        CleanTarget("delivery_optimization", "Optimisation de la distribution", "Windows",
                    "Cache de partage des mises à jour entre PC.",
                    lambda: _existing(windir / "ServiceProfiles" / "NetworkService" / "AppData" / "Local"
                                      / "Microsoft" / "Windows" / "DeliveryOptimization" / "Cache"),
                    admin=True),
        CleanTarget("directx_cache", "Cache des shaders DirectX", "Windows",
                    "Recréé automatiquement par les jeux et applications.",
                    lambda: _existing(local / "D3DSCache")),
        CleanTarget("gpu_cache", "Cache des shaders NVIDIA / AMD", "Windows",
                    "Les jeux peuvent saccader brièvement le temps de le reconstruire.",
                    lambda: _existing(local / "NVIDIA" / "DXCache", local / "NVIDIA" / "GLCache",
                                      local / "NVIDIA Corporation" / "NV_Cache",
                                      local / "AMD" / "DxCache", local / "AMD" / "DxcCache",
                                      local / "AMD" / "VkCache", local / "AMD" / "GLCache"),
                    default=False),
        CleanTarget("windows_logs", "Journaux Windows", "Windows",
                    r"Fichiers .log anciens de C:\Windows\Logs (plus de 7 jours).",
                    lambda: _existing(windir / "Logs"), admin=True, default=False,
                    patterns=("*.log", "*.old", "*.etl"), min_age_hours=24 * 7),
        CleanTarget("app_downloads", "Installateurs téléchargés par NPone29 Cleaner", "Windows",
                    "Pilotes téléchargés (ex. NVIDIA) déjà installés — plus d'un jour.",
                    lambda: _existing(local / "NPone29Cleaner" / "downloads"), min_age_hours=24),
        CleanTarget("recent_files", "Liste des documents récents", "Windows",
                    "Raccourcis « Récents » de l'Explorateur (confidentialité).",
                    lambda: _existing(roaming / "Microsoft" / "Windows" / "Recent"),
                    patterns=("*.lnk",), recursive=False, default=False),

        # ---------------------------------------------------------------- Navigateurs
        CleanTarget("chrome", "Google Chrome — cache", "Navigateurs",
                    "Cache Internet (n'efface ni l'historique, ni les mots de passe, ni les cookies).",
                    lambda: chromium_cache_dirs(local / "Google" / "Chrome" / "User Data")),
        CleanTarget("edge", "Microsoft Edge — cache", "Navigateurs",
                    "Cache Internet (n'efface ni l'historique, ni les mots de passe, ni les cookies).",
                    lambda: chromium_cache_dirs(local / "Microsoft" / "Edge" / "User Data")),
        CleanTarget("brave", "Brave — cache", "Navigateurs", "Cache Internet.",
                    lambda: chromium_cache_dirs(local / "BraveSoftware" / "Brave-Browser" / "User Data")),
        CleanTarget("vivaldi", "Vivaldi — cache", "Navigateurs", "Cache Internet.",
                    lambda: chromium_cache_dirs(local / "Vivaldi" / "User Data")),
        CleanTarget("opera", "Opera / Opera GX — cache", "Navigateurs", "Cache Internet.",
                    lambda: _single_profile_caches(local / "Opera Software" / "Opera Stable",
                                                   local / "Opera Software" / "Opera GX Stable",
                                                   roaming / "Opera Software" / "Opera Stable",
                                                   roaming / "Opera Software" / "Opera GX Stable")),
        CleanTarget("firefox", "Mozilla Firefox — cache", "Navigateurs",
                    "Cache Internet (n'efface ni l'historique, ni les mots de passe, ni les cookies).",
                    firefox_cache_dirs),

        # ---------------------------------------------------------------- Applications
        CleanTarget("discord", "Discord — cache", "Applications", "Images et médias mis en cache.",
                    lambda: _single_profile_caches(roaming / "discord", roaming / "discordptb",
                                                   roaming / "discordcanary")),
        CleanTarget("teams_classic", "Microsoft Teams (classique) — cache", "Applications", "Cache de Teams.",
                    lambda: _single_profile_caches(roaming / "Microsoft" / "Teams")),
        CleanTarget("steam", "Steam — cache web", "Applications", "Cache du navigateur intégré de Steam.",
                    _steam_dirs),
        CleanTarget("spotify_cache", "Spotify — cache web", "Applications",
                    "Cache de l'interface (pas la musique téléchargée).",
                    lambda: _single_profile_caches(local / "Spotify" / "Browser")),

        # ---------------------------------------------------------------- Développement
        CleanTarget("vscode", "Visual Studio Code — cache", "Développement", "Caches d'interface de VS Code.",
                    lambda: _single_profile_caches(roaming / "Code")
                    + _existing(roaming / "Code" / "CachedData", roaming / "Code" / "CachedExtensionVSIXs"),
                    default=False),
        CleanTarget("pip_cache", "Cache pip (Python)", "Développement",
                    "Paquets Python téléchargés ; ils seront retéléchargés si besoin.",
                    lambda: _existing(local / "pip" / "cache"), default=False),
        CleanTarget("npm_cache", "Cache npm (Node.js)", "Développement",
                    "Paquets npm téléchargés ; ils seront retéléchargés si besoin.",
                    lambda: _existing(local / "npm-cache"), default=False),
    ]
