"""Sauvegarde des fichiers de l'utilisateur : complète, différentielle ou incrémentale.

Objectif : si le disque du PC lâche, retrouver ses fichiers tels quels. Les fichiers sont donc copiés
**en clair** (aucune archive, aucun format propriétaire) : on peut les ouvrir directement depuis le disque
de sauvegarde, sur n'importe quel PC, même sans l'application.

Organisation sur le disque de sauvegarde :

    <destination>/NPone29 Sauvegardes/<NOM-DU-PC>/
        LISEZMOI.txt
        2026-11-25_14h30_Complete/
            _sauvegarde.json        description (écrite en dernier : sa présence = sauvegarde terminée)
            _inventaire.json.gz     état complet des dossiers sauvegardés à cet instant
            RESTAURER.bat           restauration de secours sans l'application (robocopy)
            C/Users/Nom/Documents/… les fichiers copiés, rangés selon leur chemin d'origine
        2026-11-26_09h00_Incrementale/
            …                       uniquement les fichiers nouveaux ou modifiés

L'inventaire de chaque sauvegarde liste **tous** les fichiers présents à ce moment-là et, pour chacun,
la sauvegarde qui contient sa copie. Restaurer un point dans le temps revient donc à lire un seul
inventaire : les fichiers supprimés entre-temps ne réapparaissent pas.

- Complète : copie tout.
- Différentielle : copie ce qui a changé depuis la dernière complète (restauration = complète + celle-ci).
- Incrémentale : copie ce qui a changé depuis la dernière sauvegarde (restauration = toute la chaîne).
"""

from __future__ import annotations

import ctypes
import fnmatch
import gzip
import json
import logging
import os
import shutil
import stat
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable

from app.config import APP_NAME, APP_VERSION

log = logging.getLogger(__name__)

FORMAT_VERSION = 1
REPO_DIR = "NPone29 Sauvegardes"
MANIFEST = "_sauvegarde.json"
INVENTORY = "_inventaire.json.gz"
MARKER = "_en_cours.json"
SCRIPT = "RESTAURER.bat"
README = "LISEZMOI.txt"
CONTROL_FILES = (MANIFEST, INVENTORY, MARKER, SCRIPT)

FULL, DIFFERENTIAL, INCREMENTAL = "full", "differential", "incremental"
KIND_LABELS = {FULL: "Complète", DIFFERENTIAL: "Différentielle", INCREMENTAL: "Incrémentale"}
_KIND_FOLDER = {FULL: "Complete", DIFFERENTIAL: "Differentielle", INCREMENTAL: "Incrementale"}

# Fichiers temporaires ou propres à Windows, inutiles à sauvegarder.
DEFAULT_EXCLUDES = ["~$*", "*.tmp", "*.temp", "thumbs.db", "desktop.ini", "*.crdownload", "*.partial",
                    "*.npart", ".~lock.*#"]
_SKIP_DIRS = {"$recycle.bin", "system volume information"}

# Fichiers OneDrive « disponibles en ligne uniquement » : les lire les téléchargerait. Ils sont déjà dans le cloud.
_CLOUD_ATTRIBUTES = 0x00001000 | 0x00040000 | 0x00400000  # OFFLINE | RECALL_ON_OPEN | RECALL_ON_DATA_ACCESS

CHUNK = 4 * 1024 * 1024
FAT32_LIMIT = 4 * 1024 ** 3 - 1
_PROGRESS_EVERY = 0.15  # secondes entre deux rafraîchissements de l'interface


class Cancelled(Exception):
    """L'utilisateur a arrêté l'opération."""


# --------------------------------------------------------------------------- chemins

def long_path(path: str | Path) -> str:
    """Préfixe \\\\?\\ pour dépasser la limite de 260 caractères de Windows."""
    text = str(path)
    if os.name != "nt" or text.startswith("\\\\?\\"):
        return text
    text = os.path.abspath(text)
    if text.startswith("\\\\"):
        return "\\\\?\\UNC\\" + text[2:]
    return "\\\\?\\" + text


def short_path(path: str) -> str:
    """Inverse de long_path (pour l'affichage)."""
    if path.startswith("\\\\?\\UNC\\"):
        return "\\\\" + path[8:]
    if path.startswith("\\\\?\\"):
        return path[4:]
    return path


def same_path(a: str | Path, b: str | Path) -> bool:
    return os.path.normcase(os.path.abspath(str(a))) == os.path.normcase(os.path.abspath(str(b)))


def is_inside(child: str | Path, parent: str | Path) -> bool:
    c = os.path.normcase(os.path.abspath(str(child))).rstrip("\\/")
    p = os.path.normcase(os.path.abspath(str(parent))).rstrip("\\/")
    return c == p or c.startswith(p + os.sep)


def computer_name() -> str:
    name = os.environ.get("COMPUTERNAME") or os.environ.get("HOSTNAME") or "PC"
    return "".join(ch if ch.isalnum() or ch in "-_ " else "_" for ch in name).strip() or "PC"


def repository_for(destination: str | Path, computer: str | None = None) -> Path:
    """Dossier des sauvegardes de ce PC sur le disque de destination."""
    return Path(destination) / REPO_DIR / (computer or computer_name())


def normalize_sources(paths: Iterable[str]) -> list[str]:
    """Dossiers existants, sans doublon ni dossier contenu dans un autre de la liste."""
    result: list[str] = []
    for raw in paths:
        if not raw:
            continue
        path = os.path.abspath(os.path.expandvars(raw))
        if not os.path.isdir(path):
            continue
        if any(is_inside(path, kept) for kept in result):
            continue
        result = [kept for kept in result if not is_inside(kept, path)]
        result.append(path)
    return result


def source_labels(sources: list[str], previous: list[dict] | None = None) -> dict[str, str]:
    """Nom lisible et unique de chaque dossier (« Documents », « Disque D »…).

    C'est le nom du dossier dans la sauvegarde. Un dossier garde le nom qu'il avait dans la sauvegarde
    précédente (`previous`), et un nom déjà pris par un autre dossier n'est jamais réutilisé : deux
    fichiers différents ne peuvent donc pas être confondus d'une sauvegarde à l'autre.
    """
    labels: dict[str, str] = {}
    previous_by_path = {os.path.normcase(m.get("path", "")): m.get("label", "") for m in previous or []}
    used: set[str] = set()
    for src in sources:
        old = previous_by_path.get(os.path.normcase(src))
        if old and old.lower() not in used:
            labels[src] = old
            used.add(old.lower())
    used |= {label.lower() for path, label in previous_by_path.items() if label and path not in
             {os.path.normcase(s) for s in sources}}
    for src in sources:
        if src in labels:
            continue
        base = os.path.basename(src.rstrip("\\/"))
        if not base:
            drive = os.path.splitdrive(src)[0].rstrip(":")
            base = f"Disque {drive}" if drive else "Partage"
        label, n = base, 2
        while label.lower() in used:
            label = f"{base} ({n})"
            n += 1
        used.add(label.lower())
        labels[src] = label
    return labels


# --------------------------------------------------------------------------- dossiers proposés

_KNOWN_FOLDERS = [
    ("Documents", "{FDD39AD0-238F-46AF-ADB4-6C85480369C7}", "Documents"),
    ("Bureau", "{B4BFCC3A-DB2C-424C-B029-7FE99A87C641}", "Desktop"),
    ("Images", "{33E28130-4E1E-4676-835A-98395C3BC3BB}", "Pictures"),
    ("Vidéos", "{18989B1D-99B5-455B-841C-AB7C74E4DDFC}", "Videos"),
    ("Musique", "{4BD8D571-6D19-48D3-BE97-422220080E43}", "Music"),
    ("Téléchargements", "{374DE290-123F-4565-9164-39C4925E467B}", "Downloads"),
]


def _known_folder(guid: str) -> str | None:
    if os.name != "nt":
        return None

    class GUID(ctypes.Structure):
        _fields_ = [("Data1", ctypes.c_ulong), ("Data2", ctypes.c_ushort), ("Data3", ctypes.c_ushort),
                    ("Data4", ctypes.c_ubyte * 8)]

    try:
        ole32 = ctypes.windll.ole32
        shell32 = ctypes.windll.shell32
        fid = GUID()
        if ole32.CLSIDFromString(ctypes.c_wchar_p(guid), ctypes.byref(fid)) != 0:
            return None
        out = ctypes.c_wchar_p()
        if shell32.SHGetKnownFolderPath(ctypes.byref(fid), 0, None, ctypes.byref(out)) != 0:
            return None
        try:
            return out.value
        finally:
            ole32.CoTaskMemFree(out)
    except (AttributeError, OSError):
        return None


def default_sources() -> list[tuple[str, str]]:
    """Dossiers personnels habituels (suit les redirections OneDrive) : [(libellé, chemin)]."""
    home = Path.home()
    result = []
    for label, guid, fallback in _KNOWN_FOLDERS:
        path = _known_folder(guid) or str(home / fallback)
        if os.path.isdir(path) and not any(same_path(path, p) for _l, p in result):
            result.append((label, path))
    return result


def folder_size(path: str, cancel: threading.Event | None = None,
                excludes: list[str] | None = None) -> tuple[int, int]:
    """(nombre de fichiers, octets) qui seraient sauvegardés dans `path`."""
    files = size = 0
    for _key, _full, st in _walk(path, "x", excludes or DEFAULT_EXCLUDES, None, cancel):
        files += 1
        size += st.st_size
    return files, size


# --------------------------------------------------------------------------- disque de destination

DRIVE_REMOVABLE, DRIVE_FIXED, DRIVE_REMOTE, DRIVE_CDROM = 2, 3, 4, 5


def _volume_root(path: str) -> str | None:
    if os.name != "nt":
        return None
    buf = ctypes.create_unicode_buffer(1024)
    try:
        if ctypes.windll.kernel32.GetVolumePathNameW(ctypes.c_wchar_p(os.path.abspath(path)), buf, 1024):
            return buf.value
    except (AttributeError, OSError):
        pass
    drive = os.path.splitdrive(os.path.abspath(path))[0]
    return drive + "\\" if drive else None


def physical_disks(path: str) -> set[int] | None:
    """Numéros des disques physiques qui portent le volume de `path` (None si inconnu)."""
    root = _volume_root(path)
    if not root or root.startswith("\\\\") or len(root) < 2 or root[1] != ":":
        return None

    class DISK_EXTENT(ctypes.Structure):  # noqa: N801 - nom de l'API Windows
        _fields_ = [("DiskNumber", ctypes.c_ulong), ("StartingOffset", ctypes.c_longlong),
                    ("ExtentLength", ctypes.c_longlong)]

    class VOLUME_DISK_EXTENTS(ctypes.Structure):  # noqa: N801
        _fields_ = [("NumberOfDiskExtents", ctypes.c_ulong), ("Extents", DISK_EXTENT * 8)]

    from ctypes import wintypes

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateFileW.restype = wintypes.HANDLE
    k32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                                wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    k32.DeviceIoControl.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD,
                                    ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
                                    ctypes.c_void_p]
    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = k32.CreateFileW(f"\\\\.\\{root[:2]}", 0, 3, None, 3, 0, None)  # aucun droit : simple requête
    if handle in (None, wintypes.HANDLE(-1).value):
        return None
    try:
        extents = VOLUME_DISK_EXTENTS()
        returned = wintypes.DWORD()
        ok = k32.DeviceIoControl(handle, 0x00560000, None, 0, ctypes.byref(extents), ctypes.sizeof(extents),
                                 ctypes.byref(returned), None)
        if not ok:
            return None
        count = min(extents.NumberOfDiskExtents, 8)
        return {extents.Extents[i].DiskNumber for i in range(count)}
    finally:
        k32.CloseHandle(handle)


def volume_info(root: str) -> tuple[str, str]:
    """(nom du volume, système de fichiers)."""
    if os.name != "nt":
        return "", ""
    name = ctypes.create_unicode_buffer(261)
    fs = ctypes.create_unicode_buffer(261)
    try:
        if ctypes.windll.kernel32.GetVolumeInformationW(ctypes.c_wchar_p(root), name, 261, None, None, None,
                                                        fs, 261):
            return name.value, fs.value
    except (AttributeError, OSError):
        pass
    return "", ""


def drive_type(root: str) -> int:
    if os.name != "nt":
        return DRIVE_FIXED
    try:
        return int(ctypes.windll.kernel32.GetDriveTypeW(ctypes.c_wchar_p(root)))
    except (AttributeError, OSError):
        return 0


@dataclass
class DestinationCheck:
    ok: bool                       # la sauvegarde peut être lancée
    level: str                     # success | warning | error
    summary: str                   # une ligne pour l'interface
    warnings: list[str] = field(default_factory=list)
    free: int | None = None
    filesystem: str = ""
    same_disk: bool = False


def check_destination(destination: str, sources: list[str]) -> DestinationCheck:
    """Le disque choisi protège-t-il vraiment les données en cas de panne du disque du PC ?"""
    if not destination:
        return DestinationCheck(False, "error", "Choisissez où enregistrer la sauvegarde.")
    if not os.path.isdir(destination):
        return DestinationCheck(False, "error", "Ce dossier n'existe pas (disque débranché ?).")
    root = _volume_root(destination) or destination
    label, fs = volume_info(root)
    kind = drive_type(root)
    try:
        free = shutil.disk_usage(destination).free
    except OSError:
        free = None

    where = {DRIVE_REMOVABLE: "Disque amovible (clé ou disque USB)", DRIVE_FIXED: "Disque interne ou externe",
             DRIVE_REMOTE: "Dossier réseau (NAS / autre PC)", DRIVE_CDROM: "Lecteur optique"}.get(kind, "Disque")
    name = f"{root.rstrip(chr(92))} {label}".strip() if label else root.rstrip("\\")
    from app.utils.fmt import human_size

    summary = f"{where} — {name}" + (f", {human_size(free)} libres" if free is not None else "")
    warnings: list[str] = []

    dest_disks = physical_disks(destination)
    same = False
    for src in sources:
        src_disks = physical_disks(src)
        if dest_disks and src_disks and dest_disks & src_disks:
            same = True
        elif dest_disks is None or src_disks is None:
            # Disque inconnu : on compare au moins les lettres de lecteur.
            if (_volume_root(src) or "").lower() == root.lower() and kind != DRIVE_REMOTE:
                same = True
        if same:
            break
    if same:
        warnings.append("Cette destination est sur le même disque physique que vos données : si ce disque "
                        "tombe en panne, la sauvegarde est perdue avec. Utilisez un disque externe, une clé USB "
                        "ou un dossier réseau.")
    if fs.upper() == "FAT32":
        warnings.append("Ce disque est formaté en FAT32 : les fichiers de plus de 4 Go ne pourront pas y être "
                        "copiés. Pour les gros fichiers, formatez-le en exFAT ou NTFS.")
    if kind == DRIVE_CDROM:
        return DestinationCheck(False, "error", "Un lecteur CD/DVD ne peut pas recevoir de sauvegarde.")
    return DestinationCheck(True, "warning" if warnings else "success", summary, warnings, free, fs, same)


# --------------------------------------------------------------------------- parcours

def _excluded(name: str, patterns: list[str]) -> bool:
    low = name.lower()
    return any(fnmatch.fnmatchcase(low, p.lower()) for p in patterns)


def _walk(source: str, base_key: str, excludes: list[str], skip_dir: str | None,
          cancel: threading.Event | None, counters: dict[str, int] | None = None):
    """Fichiers de `source` : (clé « Nom du dossier/sous-dossier/fichier », chemin long, stat).

    Ignore les liens, les jonctions et les fichiers OneDrive disponibles uniquement en ligne.
    """
    root = long_path(source)
    skip = os.path.normcase(long_path(skip_dir)) if skip_dir else None
    stack = [root]
    while stack:
        if cancel is not None and cancel.is_set():
            raise Cancelled
        current = stack.pop()
        try:
            with os.scandir(current) as it:
                entries = list(it)
        except OSError as exc:
            if counters is not None:
                counters["unreadable"] = counters.get("unreadable", 0) + 1
            log.debug("Dossier illisible %s : %s", current, exc)
            continue
        for entry in entries:
            try:
                if entry.is_symlink() or getattr(entry, "is_junction", lambda: False)():
                    continue
                if entry.is_dir(follow_symlinks=False):
                    if entry.name.lower() in _SKIP_DIRS:
                        continue
                    if skip and os.path.normcase(entry.path) == skip:
                        continue
                    stack.append(entry.path)
                    continue
                if _excluded(entry.name, excludes):
                    continue
                st = entry.stat(follow_symlinks=False)
                if getattr(st, "st_file_attributes", 0) & _CLOUD_ATTRIBUTES:
                    if counters is not None:
                        counters["cloud"] = counters.get("cloud", 0) + 1
                    continue
                rel = entry.path[len(root):].lstrip("\\/").replace("\\", "/")
                yield f"{base_key}/{rel}", entry.path, st
            except OSError:
                continue


# --------------------------------------------------------------------------- copie

def _clear_readonly(path: str) -> None:
    try:
        os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
    except OSError:
        pass


def copy_file(src: str, dst: str, cancel: threading.Event | None = None,
              on_bytes: Callable[[int], None] | None = None) -> tuple[int, int]:
    """Copie `src` vers `dst` (via un fichier .npart renommé à la fin). Renvoie (octets, mtime_ns de la source)."""
    src_l, dst_l = long_path(src), long_path(dst)
    tmp = dst_l + ".npart"
    os.makedirs(os.path.dirname(dst_l), exist_ok=True)
    st = os.stat(src_l)
    written = 0
    try:
        with open(src_l, "rb") as fin, open(tmp, "wb") as fout:
            while True:
                if cancel is not None and cancel.is_set():
                    raise Cancelled
                chunk = fin.read(CHUNK)
                if not chunk:
                    break
                fout.write(chunk)
                written += len(chunk)
                if on_bytes:
                    on_bytes(len(chunk))
        os.utime(tmp, ns=(st.st_atime_ns, st.st_mtime_ns))
        if os.path.exists(dst_l):
            _clear_readonly(dst_l)
        os.replace(tmp, dst_l)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise
    return written, st.st_mtime_ns


@dataclass
class Progress:
    phase: str                 # scan | copy | restore | verify
    done_files: int = 0
    total_files: int = 0
    done_bytes: int = 0
    total_bytes: int = 0
    current: str = ""
    started: float = field(default_factory=time.monotonic)

    @property
    def fraction(self) -> float:
        if self.total_bytes:
            return min(1.0, self.done_bytes / self.total_bytes)
        if self.total_files:
            return min(1.0, self.done_files / self.total_files)
        return 0.0

    @property
    def speed(self) -> float:
        """Octets par seconde."""
        elapsed = time.monotonic() - self.started
        return self.done_bytes / elapsed if elapsed > 0.5 else 0.0

    @property
    def remaining(self) -> float | None:
        """Secondes restantes estimées."""
        speed = self.speed
        if not speed or not self.total_bytes:
            return None
        return max(0.0, (self.total_bytes - self.done_bytes) / speed)


class _Reporter:
    """Limite les rafraîchissements de l'interface (une file Tkinter saturée fige la fenêtre)."""

    def __init__(self, progress: Progress, callback: Callable[[Progress], None] | None):
        self.progress = progress
        self.callback = callback
        self._last = 0.0

    def __call__(self, force: bool = False) -> None:
        if not self.callback:
            return
        now = time.monotonic()
        if force or now - self._last >= _PROGRESS_EVERY:
            self._last = now
            p = self.progress
            self.callback(Progress(p.phase, p.done_files, p.total_files, p.done_bytes, p.total_bytes,
                                   p.current, p.started))


# --------------------------------------------------------------------------- sauvegardes existantes

@dataclass
class BackupSet:
    id: str
    path: Path
    kind: str
    created: datetime
    complete: bool
    finished: datetime | None = None
    sources: list[dict] = field(default_factory=list)
    reference: str | None = None      # sauvegarde comparée (dernière complète ou dernière sauvegarde)
    requires: list[str] = field(default_factory=list)   # sauvegardes qui contiennent une partie des fichiers
    file_count: int = 0
    total_bytes: int = 0
    copied_count: int = 0
    copied_bytes: int = 0
    error_count: int = 0
    computer: str = ""

    @property
    def label(self) -> str:
        return KIND_LABELS.get(self.kind, self.kind)

    def inventory(self) -> dict[str, list]:
        """{clé: [taille, mtime_ns, id de la sauvegarde qui contient la copie]}"""
        with gzip.open(self.path / INVENTORY, "rt", encoding="utf-8") as fh:
            data = json.load(fh)
        return data.get("files", {})


def parse_time(value) -> datetime | None:
    try:
        return datetime.fromisoformat(value) if value else None
    except (TypeError, ValueError):
        return None


def load_set(path: Path) -> BackupSet | None:
    manifest, marker = path / MANIFEST, path / MARKER
    source = manifest if manifest.is_file() else marker if marker.is_file() else None
    if source is None:
        return None
    try:
        meta = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        log.warning("Description de sauvegarde illisible : %s", source)
        return None
    created = parse_time(meta.get("created"))
    if created is None:
        try:
            created = datetime.fromtimestamp(source.stat().st_mtime)
        except OSError:
            created = datetime.min
    return BackupSet(
        id=path.name, path=path, kind=meta.get("kind", FULL), created=created,
        complete=source is manifest and bool(meta.get("complete", True)) and (path / INVENTORY).is_file(),
        finished=parse_time(meta.get("finished")), sources=meta.get("sources", []),
        reference=meta.get("reference"), requires=list(meta.get("requires", [])),
        file_count=int(meta.get("file_count", 0)), total_bytes=int(meta.get("total_bytes", 0)),
        copied_count=int(meta.get("copied_count", 0)), copied_bytes=int(meta.get("copied_bytes", 0)),
        error_count=int(meta.get("error_count", 0)), computer=meta.get("computer", ""),
    )


def list_sets(repo: str | Path) -> list[BackupSet]:
    """Sauvegardes du dossier, de la plus ancienne à la plus récente (y compris les interrompues)."""
    repo = Path(repo)
    try:
        children = [c for c in repo.iterdir() if c.is_dir()]
    except OSError:
        return []
    sets = [s for s in (load_set(c) for c in children) if s is not None]
    sets.sort(key=lambda s: (s.created, s.id))
    return sets


def is_repository(path: Path) -> bool:
    try:
        return any((c / MANIFEST).is_file() or (c / MARKER).is_file() for c in path.iterdir() if c.is_dir())
    except OSError:
        return False


def find_repositories(path: str | Path) -> list[Path]:
    """Retrouve les sauvegardes à partir d'un dossier choisi par l'utilisateur (disque, dossier, sauvegarde)."""
    p = Path(path)
    if (p / MANIFEST).is_file() or (p / MARKER).is_file():
        return [p.parent]
    if is_repository(p):
        return [p]
    if (p / REPO_DIR).is_dir():
        p = p / REPO_DIR
    if p.name.lower() == REPO_DIR.lower():
        try:
            return sorted(c for c in p.iterdir() if c.is_dir() and is_repository(c))
        except OSError:
            return []
    return []


def chain_ok(sets_by_id: dict[str, BackupSet], backup: BackupSet) -> bool:
    """Toutes les sauvegardes nécessaires à la restauration de `backup` sont-elles présentes et terminées ?"""
    if not backup.complete:
        return False
    return all(r in sets_by_id and sets_by_id[r].complete for r in backup.requires)


def dependents(sets: list[BackupSet], set_id: str) -> list[BackupSet]:
    """Sauvegardes qui ont besoin de `set_id` pour être restaurées."""
    return [s for s in sets if set_id in s.requires and s.id != set_id]


def delete_set(repo: str | Path, set_id: str) -> None:
    """Supprime une sauvegarde (uniquement un dossier de sauvegarde reconnu de ce dossier)."""
    target = Path(repo) / set_id
    if not ((target / MANIFEST).is_file() or (target / MARKER).is_file()):
        raise ValueError(f"« {target} » n'est pas une sauvegarde NPone29.")

    def retry(func, path, _exc):
        _clear_readonly(path)
        func(path)

    if sys.version_info >= (3, 12):
        shutil.rmtree(long_path(target), onexc=retry)
    else:
        shutil.rmtree(long_path(target), onerror=retry)


def prune(repo: str | Path, keep_full: int) -> list[str]:
    """Garde les `keep_full` dernières sauvegardes complètes (et ce qui en dépend). Renvoie les ids supprimés."""
    if keep_full <= 0:
        return []
    sets = list_sets(repo)
    fulls = [s for s in sets if s.kind == FULL and s.complete]
    if len(fulls) <= keep_full:
        return []
    cutoff = fulls[-keep_full].created
    removed: list[str] = []
    old = [s for s in sets if s.created < cutoff]
    keep = [s for s in sets if s.created >= cutoff]
    needed = {r for s in keep for r in s.requires}
    for s in reversed(old):
        if s.id in needed:
            continue
        try:
            delete_set(repo, s.id)
            removed.append(s.id)
        except (OSError, ValueError) as exc:
            log.warning("Ancienne sauvegarde %s non supprimée : %s", s.id, exc)
    return removed


# --------------------------------------------------------------------------- sauvegarde

@dataclass
class BackupPlan:
    sources: list[str]
    destination: str
    repo: Path
    kind: str                          # type réellement effectué
    requested: str                     # type demandé
    reference: BackupSet | None
    inventory: dict[str, list]         # état complet ; holder None = à copier
    to_copy: dict[str, str]            # clé -> chemin source (long)
    copy_bytes: int
    total_bytes: int
    notes: list[str] = field(default_factory=list)
    cloud_skipped: int = 0
    unreadable_dirs: int = 0
    too_big_for_fat32: list[str] = field(default_factory=list)
    excludes: list[str] = field(default_factory=list)
    labels: dict[str, str] = field(default_factory=dict)

    @property
    def file_count(self) -> int:
        return len(self.inventory)


@dataclass
class BackupResult:
    set_id: str | None
    kind: str
    copied: int = 0
    copied_bytes: int = 0
    file_count: int = 0
    total_bytes: int = 0
    errors: list[tuple[str, str]] = field(default_factory=list)
    cancelled: bool = False
    disk_full: bool = False
    duration: float = 0.0
    pruned: list[str] = field(default_factory=list)
    path: Path | None = None


def plan_backup(sources: list[str], destination: str, mode: str = FULL, excludes: list[str] | None = None,
                cancel: threading.Event | None = None,
                on_progress: Callable[[Progress], None] | None = None) -> BackupPlan:
    """Analyse les dossiers et détermine ce qui doit être copié (rien n'est écrit)."""
    excludes = list(DEFAULT_EXCLUDES if excludes is None else excludes)
    sources = normalize_sources(sources)
    if not sources:
        raise ValueError("Aucun dossier à sauvegarder (dossiers introuvables ?).")
    repo = repository_for(destination)
    sets = list_sets(repo)
    by_id = {s.id: s for s in sets}
    usable = [s for s in sets if chain_ok(by_id, s)]
    notes: list[str] = []

    reference: BackupSet | None = None
    if mode == DIFFERENTIAL:
        reference = next((s for s in reversed(usable) if s.kind == FULL), None)
    elif mode == INCREMENTAL:
        reference = usable[-1] if usable else None
    kind = mode
    if mode != FULL and reference is None:
        kind = FULL
        notes.append("Aucune sauvegarde complète n'existe encore sur ce disque : celle-ci sera complète. "
                     f"Les suivantes pourront être {KIND_LABELS[mode].lower()}s.")
    try:
        ref_files = reference.inventory() if reference else {}
    except (OSError, ValueError, EOFError) as exc:
        log.warning("Inventaire illisible (%s) : sauvegarde complète.", exc)
        ref_files, reference, kind = {}, None, FULL
        notes.append("L'inventaire de la sauvegarde précédente est illisible : celle-ci sera complète.")

    progress = Progress("scan")
    report = _Reporter(progress, on_progress)
    counters: dict[str, int] = {}
    inventory: dict[str, list] = {}
    to_copy: dict[str, str] = {}
    copy_bytes = total = 0
    fat32 = volume_info(_volume_root(destination) or destination)[1].upper() == "FAT32" \
        if os.path.isdir(destination) else False
    too_big: list[str] = []
    skip_dir = str(Path(destination) / REPO_DIR)
    previous = reference.sources if reference else (usable[-1].sources if usable else [])
    labels = source_labels(sources, previous)
    for src in sources:
        for key, full_path, st in _walk(src, labels[src], excludes, skip_dir, cancel, counters):
            size, mtime = st.st_size, st.st_mtime_ns
            total += size
            old = ref_files.get(key)
            if old and old[0] == size and old[1] == mtime and old[2]:
                inventory[key] = [size, mtime, old[2]]
            else:
                if fat32 and size > FAT32_LIMIT:
                    too_big.append(short_path(full_path))
                    continue
                inventory[key] = [size, mtime, None]
                to_copy[key] = full_path
                copy_bytes += size
            progress.done_files += 1
            progress.current = short_path(full_path)
            report()
    report(force=True)
    return BackupPlan(sources, destination, repo, kind, mode, reference, inventory, to_copy, copy_bytes, total,
                      notes, counters.get("cloud", 0), counters.get("unreadable", 0), too_big, excludes, labels)


def _new_set_id(repo: Path, kind: str, when: datetime) -> str:
    base = f"{when:%Y-%m-%d_%Hh%M}_{_KIND_FOLDER[kind]}"
    candidate, n = base, 2
    while (repo / candidate).exists():
        candidate = f"{base}_{n}"
        n += 1
    return candidate


def _write_json_atomic(path: Path, data: dict) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _write_readme(repo: Path) -> None:
    text = f"""SAUVEGARDES DE {repo.name} — créées par {APP_NAME}
{'=' * 60}

Vos fichiers sont copiés tels quels : vous pouvez les ouvrir directement depuis ce disque,
sur n'importe quel PC, sans logiciel particulier.

Chaque dossier est une sauvegarde, nommée par date et par type :
  • _Complete        : contient TOUS vos fichiers à cette date.
  • _Differentielle  : contient les fichiers modifiés depuis la dernière complète.
  • _Incrementale    : contient les fichiers modifiés depuis la sauvegarde précédente.
À l'intérieur, un sous-dossier par dossier sauvegardé (Documents, Images, Bureau...),
avec la même organisation que sur le PC.

RESTAURER
  1. Le plus simple : {APP_NAME} > page « Sauvegarde » > onglet « Restaurer » >
     « Ouvrir une sauvegarde… » puis choisissez ce dossier. La restauration est exacte
     (version de chaque fichier à la date choisie).
  2. Sans l'application : double-cliquez sur RESTAURER.bat dans la sauvegarde voulue.
     Il recopie dans le dossier de votre choix la sauvegarde complète puis les suivantes.
     (Les fichiers supprimés entre deux sauvegardes peuvent alors réapparaître.)
  3. À la main : copiez la sauvegarde complète, puis par-dessus, dans l'ordre, les
     sauvegardes suivantes jusqu'à la date voulue (pour une différentielle : uniquement
     la complète puis cette différentielle).

Ne renommez pas et ne modifiez pas les fichiers _sauvegarde.json et _inventaire.json.gz :
ils permettent à l'application de savoir quelle version de chaque fichier se trouve où.
"""
    try:
        (repo / README).write_text(text, encoding="utf-8-sig")
    except OSError as exc:
        log.debug("LISEZMOI non écrit : %s", exc)


def _write_restore_script(set_dir: Path, chain: list[str], when: datetime, kind: str) -> None:
    lines = [
        "@echo off",
        "chcp 65001 >nul",
        f"echo Restauration de secours de la sauvegarde du {when:%d/%m/%Y à %H:%M} ({KIND_LABELS[kind]}).",
        "echo Les fichiers seront recopiés dans le dossier de votre choix (un sous-dossier par dossier "
        "sauvegardé : Documents, Images...).",
        "echo.",
        'set "CIBLE="',
        'set /p "CIBLE=Dossier où restaurer (ex. D:\\Restauration) : "',
        'if "%CIBLE%"=="" goto :eof',
        'if not exist "%CIBLE%" mkdir "%CIBLE%"',
    ]
    exclude = " ".join(f'"{name}"' for name in CONTROL_FILES)
    for set_id in chain:
        lines.append(f'echo Copie de {set_id}...')
        lines.append(f'robocopy "%~dp0..\\{set_id}" "%CIBLE%" /E /COPY:DT /DCOPY:T /R:1 /W:1 /NP /NFL /NDL '
                     f'/XF {exclude} *.npart')
    lines += ["echo.", "echo Terminé : vos fichiers sont dans %CIBLE%", "pause"]
    try:
        (set_dir / SCRIPT).write_text("\r\n".join(lines) + "\r\n", encoding="utf-8")
    except OSError as exc:
        log.debug("RESTAURER.bat non écrit : %s", exc)


def _copy_app(repo: Path) -> None:
    """Dépose une copie de l'application à côté des sauvegardes (utile si le PC entier est perdu)."""
    if not getattr(sys, "frozen", False):
        return
    exe = Path(sys.executable)
    target = repo.parent / exe.name
    try:
        if target.exists() and target.stat().st_size == exe.stat().st_size:
            return
        shutil.copy2(exe, target)
    except OSError as exc:
        log.debug("Copie de l'application non faite : %s", exc)


def run_backup(plan: BackupPlan, cancel: threading.Event | None = None,
               on_progress: Callable[[Progress], None] | None = None, keep_full: int = 0) -> BackupResult:
    """Copie les fichiers prévus par `plan` dans une nouvelle sauvegarde."""
    started = time.monotonic()
    when = datetime.now()
    repo = plan.repo
    repo.mkdir(parents=True, exist_ok=True)
    set_id = _new_set_id(repo, plan.kind, when)
    set_dir = repo / set_id
    set_dir.mkdir()
    labels = plan.labels or source_labels(plan.sources)
    sources_meta = [{"path": s, "label": labels[s]} for s in plan.sources]
    meta = {
        "format": FORMAT_VERSION, "app": f"{APP_NAME} {APP_VERSION}", "computer": repo.name,
        "kind": plan.kind, "requested": plan.requested, "created": when.isoformat(timespec="seconds"),
        "sources": sources_meta, "reference": plan.reference.id if plan.reference else None,
        "excludes": plan.excludes,
    }
    _write_json_atomic(set_dir / MARKER, {**meta, "complete": False})
    _write_readme(repo)

    result = BackupResult(set_id, plan.kind, path=set_dir)
    progress = Progress("copy", total_files=len(plan.to_copy), total_bytes=plan.copy_bytes)
    report = _Reporter(progress, on_progress)
    inventory = plan.inventory
    ref_files: dict[str, list] | None = None

    def add_bytes(n: int) -> None:
        progress.done_bytes += n
        report()

    try:
        for key in sorted(plan.to_copy):
            src = plan.to_copy[key]
            progress.current = short_path(src)
            report()
            before = progress.done_bytes
            try:
                written, mtime = copy_file(src, str(set_dir / key), cancel, add_bytes)
                inventory[key] = [written, mtime, set_id]
                result.copied += 1
                result.copied_bytes += written
            except Cancelled:
                raise
            except OSError as exc:
                if _is_disk_full(exc):
                    result.disk_full = True
                    raise Cancelled from exc
                progress.done_bytes = before + inventory[key][0]
                result.errors.append((short_path(src), _error_text(exc)))
                # L'ancienne copie (si elle existe) reste valable : mieux qu'aucune.
                if ref_files is None:
                    try:
                        ref_files = plan.reference.inventory() if plan.reference else {}
                    except (OSError, ValueError, EOFError):
                        ref_files = {}
                old = ref_files.get(key)
                if old and old[2]:
                    inventory[key] = old
                else:
                    inventory.pop(key, None)
            progress.done_files += 1
    except Cancelled:
        result.cancelled = True
        result.duration = time.monotonic() - started
        report(force=True)
        return result

    requires = sorted({v[2] for v in inventory.values() if v[2] and v[2] != set_id})
    with gzip.open(set_dir / INVENTORY, "wt", encoding="utf-8") as fh:
        json.dump({"format": FORMAT_VERSION, "files": inventory}, fh, ensure_ascii=False, separators=(",", ":"))
    all_sets = {s.id: s for s in list_sets(repo)}
    chain = sorted(requires, key=lambda i: (all_sets[i].created, i) if i in all_sets else (when, i)) + [set_id]
    _write_restore_script(set_dir, chain, when, plan.kind)
    result.file_count = len(inventory)
    result.total_bytes = sum(v[0] for v in inventory.values())
    finished = datetime.now()
    _write_json_atomic(set_dir / MANIFEST, {
        **meta, "complete": True, "finished": finished.isoformat(timespec="seconds"), "requires": requires,
        "file_count": result.file_count, "total_bytes": result.total_bytes,
        "copied_count": result.copied, "copied_bytes": result.copied_bytes,
        "error_count": len(result.errors), "errors": [list(e) for e in result.errors[:500]],
    })
    try:
        (set_dir / MARKER).unlink()
    except OSError:
        pass
    _copy_app(repo)
    if keep_full:
        result.pruned = prune(repo, keep_full)
    result.duration = time.monotonic() - started
    progress.current = ""
    report(force=True)
    return result


def _is_disk_full(exc: OSError) -> bool:
    return getattr(exc, "winerror", None) in (39, 112) or exc.errno == 28


def _error_text(exc: OSError) -> str:
    if isinstance(exc, PermissionError) or getattr(exc, "winerror", None) in (5, 32, 33):
        return "fichier ouvert par un programme ou accès refusé"
    if _is_disk_full(exc):
        return "disque plein"
    if isinstance(exc, FileNotFoundError):
        return "fichier supprimé pendant la sauvegarde"
    return exc.strerror or str(exc)


# --------------------------------------------------------------------------- restauration

ORIGINAL = "original"
FOLDER = "folder"


@dataclass
class RestoreResult:
    restored: int = 0
    restored_bytes: int = 0
    identical: int = 0                 # déjà présents et identiques
    kept: int = 0                      # présents mais différents, conservés (pas d'écrasement)
    missing: list[str] = field(default_factory=list)   # copie introuvable dans la sauvegarde
    errors: list[tuple[str, str]] = field(default_factory=list)
    cancelled: bool = False
    target: str = ""


def restore_target(key: str, sources: list[dict], mode: str, folder: str = "") -> str:
    """Où restaurer le fichier `key` : à son emplacement d'origine ou dans `folder`/<nom du dossier>/…"""
    label, _sep, rest = key.partition("/")
    if mode == ORIGINAL:
        match = next((s for s in sources if s.get("label") == label and s.get("path")), None)
        if match is None:
            raise ValueError(f"Emplacement d'origine inconnu pour « {label} ».")
        return os.path.join(match["path"], *rest.split("/"))
    return os.path.join(folder, label, *rest.split("/"))


def restore(repo: str | Path, set_id: str, mode: str = FOLDER, folder: str = "", overwrite: bool = False,
            keys: Iterable[str] | None = None, cancel: threading.Event | None = None,
            on_progress: Callable[[Progress], None] | None = None) -> RestoreResult:
    """Restaure les fichiers tels qu'ils étaient au moment de la sauvegarde `set_id`."""
    repo = Path(repo)
    backup = load_set(repo / set_id)
    if backup is None or not backup.complete:
        raise ValueError("Cette sauvegarde est incomplète (interrompue) : elle ne peut pas être restaurée.")
    if mode == FOLDER and not folder:
        raise ValueError("Choisissez le dossier où restaurer les fichiers.")
    inventory = backup.inventory()
    wanted = inventory if keys is None else {k: inventory[k] for k in keys if k in inventory}
    result = RestoreResult(target=folder if mode == FOLDER else "emplacement d'origine")
    progress = Progress("restore", total_files=len(wanted), total_bytes=sum(v[0] for v in wanted.values()))
    report = _Reporter(progress, on_progress)

    def add_bytes(n: int) -> None:
        progress.done_bytes += n
        report()

    try:
        for key in sorted(wanted):
            size, mtime, holder = wanted[key]
            src = repo / holder / key
            try:
                dst = restore_target(key, backup.sources, mode, folder)
            except ValueError as exc:
                result.errors.append((key.replace("/", os.sep), str(exc)))
                progress.done_files += 1
                continue
            progress.current = dst
            report()
            before = progress.done_bytes
            try:
                dst_l = long_path(dst)
                if os.path.exists(dst_l):
                    st = os.stat(dst_l)
                    if st.st_size == size and st.st_mtime_ns == mtime:
                        result.identical += 1
                        progress.done_bytes += size
                        progress.done_files += 1
                        continue
                    if not overwrite:
                        result.kept += 1
                        progress.done_bytes += size
                        progress.done_files += 1
                        continue
                if not os.path.exists(long_path(src)):
                    result.missing.append(key.replace("/", os.sep))
                    progress.done_bytes += size
                    progress.done_files += 1
                    continue
                written, _m = copy_file(str(src), dst, cancel, add_bytes)
                result.restored += 1
                result.restored_bytes += written
            except Cancelled:
                raise
            except OSError as exc:
                if _is_disk_full(exc):
                    result.errors.append((dst, "disque plein : restauration arrêtée"))
                    raise Cancelled from exc
                progress.done_bytes = before + size
                result.errors.append((dst, _error_text(exc)))
            progress.done_files += 1
    except Cancelled:
        result.cancelled = True
    report(force=True)
    return result


# --------------------------------------------------------------------------- vérification

@dataclass
class VerifyResult:
    checked: int = 0
    missing: list[str] = field(default_factory=list)
    damaged: list[str] = field(default_factory=list)
    cancelled: bool = False

    @property
    def ok(self) -> bool:
        return not self.missing and not self.damaged and not self.cancelled


def verify(repo: str | Path, set_id: str, cancel: threading.Event | None = None,
           on_progress: Callable[[Progress], None] | None = None) -> VerifyResult:
    """Vérifie que chaque fichier de la sauvegarde (et des précédentes nécessaires) est présent et complet."""
    repo = Path(repo)
    backup = load_set(repo / set_id)
    if backup is None or not backup.complete:
        raise ValueError("Cette sauvegarde est incomplète (interrompue).")
    inventory = backup.inventory()
    result = VerifyResult()
    progress = Progress("verify", total_files=len(inventory))
    report = _Reporter(progress, on_progress)
    for key, (size, _mtime, holder) in inventory.items():
        if cancel is not None and cancel.is_set():
            result.cancelled = True
            break
        try:
            st = os.stat(long_path(repo / holder / key))
            if st.st_size != size:
                result.damaged.append(key.replace("/", os.sep))
        except OSError:
            result.missing.append(key.replace("/", os.sep))
        result.checked += 1
        progress.done_files += 1
        progress.current = key
        report()
    report(force=True)
    return result
