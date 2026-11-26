"""Contrat commun des sources de pilotes (une classe par fabricant).

Une source peut :
  * vérifier en ligne la dernière version publiée (``has_public_api = True``) via un service
    ou un catalogue officiel du fabricant ;
  * et/ou fournir les liens de téléchargement les plus précis possible pour un périphérique.

Pour ajouter un fabricant : créer une sous-classe de ``DriverSource`` dans ce paquet puis
l'ajouter à ``SOURCES`` dans ``__init__.py``. La logique d'analyse n'a pas à changer.
"""

from __future__ import annotations

import logging
import os
import shutil
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from app.config import APP_ID
from app.core.vendors import parse_ids
from app.utils.download import is_allowed_url

log = logging.getLogger(__name__)

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) NPone29Cleaner"

# Nature d'un lien, du plus précis au moins précis.
EXACT, DOWNLOAD, PREFILLED, GENERAL, SEARCH = "exact", "download", "prefilled", "general", "search"
LINK_ORDER = [DOWNLOAD, EXACT, PREFILLED, GENERAL, SEARCH]
# Une recherche « limitée au site officiel » (kind SEARCH) passe par ce moteur ; elle ne fait qu'ouvrir
# une page de résultats dans le navigateur, rien n'est téléchargé.
SEARCH_HOSTS = ("www.google.com",)


@dataclass(frozen=True)
class SystemContext:
    """Ce que l'on sait du PC (aucune donnée personnelle : ni numéro de série, ni nom d'utilisateur)."""
    system_maker: str = ""
    model: str = ""
    sku: str = ""
    board_maker: str = ""
    board_product: str = ""
    windows_build: int = 0
    windows_release: str = ""      # « 24H2 », « 22H2 »…

    @property
    def is_windows11(self) -> bool:
        return self.windows_build >= 22000

    @classmethod
    def from_board_info(cls, info: dict[str, str]) -> "SystemContext":
        build, release = current_windows()
        return cls(system_maker=info.get("system", ""), model=info.get("model", ""), sku=info.get("sku", ""),
                   board_maker=info.get("board_maker", ""), board_product=info.get("board_product", ""),
                   windows_build=build, windows_release=release)


@dataclass(frozen=True)
class DeviceRef:
    """Un pilote installé, vu par les sources."""
    key: str
    name: str
    device_id: str
    hardware_id: str
    version: str
    device_class: str
    vendor_name: str
    date: datetime | None = None          # date du pilote installé (DriverVer)
    provider: str = ""                    # éditeur du pilote installé (« Microsoft » = pilote de Windows)
    # Identifiants déclarés par le périphérique, du plus précis au moins précis, et celui qui a servi à
    # choisir le pilote installé : c'est ainsi que Windows classe les pilotes (voir microsoft_catalog.py).
    hardware_ids: tuple[str, ...] = ()
    compatible_ids: tuple[str, ...] = ()
    matching_id: str = ""

    @property
    def ids(self) -> tuple[str, str, str]:
        return parse_ids(self.device_id)

    @property
    def is_microsoft(self) -> bool:
        return self.provider.strip().lower() in {"microsoft", "microsoft corporation"}


@dataclass
class DownloadLink:
    label: str
    url: str
    kind: str = GENERAL


@dataclass
class VendorCheck:
    """Résultat d'une vérification auprès d'une source officielle."""
    source: str                    # « nvidia.com », « Catalogue officiel Dell »…
    latest_version: str
    update_available: bool
    checked_at: datetime
    title: str = ""
    release_date: str = ""
    page_url: str = ""             # page officielle du pilote (notes de version, détails)
    download_url: str = ""         # fichier officiel à télécharger
    extra: object = None           # données propres à la source (ex. pilote NVIDIA installable)
    comparable: bool = True        # False si les versions n'ont pas pu être comparées
    note: str = ""                 # précision affichée à l'utilisateur (ex. paquet déjà installé)
    # Pilote à jour pour ce PC, mais le fabricant de la puce publie une version générique plus récente
    # que Windows n'installe pas d'office (celle du fabricant du PC reste prioritaire).
    generic_newer: bool = False


@dataclass
class SourceRun:
    """Résultat de toutes les vérifications d'une analyse."""
    checks: dict[str, list[VendorCheck]] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    verified_by: list[str] = field(default_factory=list)   # noms (DriverSource.name) des sources consultées

    def add(self, key: str, check: VendorCheck) -> None:
        self.checks.setdefault(key, []).append(check)


class DriverSource:
    name = ""
    has_public_api = False
    allowed_hosts: tuple[str, ...] = ()
    # Phrase affichée quand ce fabricant ne permet pas la vérification automatique.
    no_api_note = ""

    # -- quand la source s'applique
    def applies_to_system(self, ctx: SystemContext) -> bool:
        """Source « fabricant du PC » : concerne tous les périphériques de ce PC."""
        return False

    def applies_to_device(self, dev: DeviceRef) -> bool:
        """Source « fabricant de la puce » : concerne ce périphérique."""
        return False

    def concerns(self, ctx: SystemContext, dev: DeviceRef) -> bool:
        return self.applies_to_system(ctx) or self.applies_to_device(dev)

    # -- vérification en ligne (réseau)
    def check(self, ctx: SystemContext, devices: list[DeviceRef], run: SourceRun,
              progress=None) -> None:
        """Ajoute à ``run`` un ``VendorCheck`` par périphérique reconnu. Par défaut : rien."""

    # -- liens
    def links(self, ctx: SystemContext, dev: DeviceRef, checks: list[VendorCheck]) -> list[DownloadLink]:
        return []

    def safe(self, link: DownloadLink) -> bool:
        if link.kind == SEARCH and is_allowed_url(link.url, SEARCH_HOSTS):
            return True
        return bool(self.allowed_hosts) and is_allowed_url(link.url, self.allowed_hosts)

    # -- explication quand le pilote n'a pas pu être vérifié
    def note_for(self, ctx: SystemContext, dev: DeviceRef, consulted: list[str]) -> str:
        """Phrase expliquant pourquoi cette source n'a pas vérifié le pilote (vide si rien à dire)."""
        return "" if self.has_public_api else self.no_api_note


# --------------------------------------------------------------------------- versions

def version_parts(version: str) -> tuple[int, ...] | None:
    """'31.0.101.5590' -> (31, 0, 101, 5590) ; None si la version n'est pas purement numérique."""
    text = (version or "").strip().rstrip("^").strip()
    if not text:
        return None
    parts = text.split(".")
    if len(parts) < 2 or not all(p.isdigit() for p in parts):
        return None
    return tuple(int(p) for p in parts)


def compare_versions(a: str, b: str) -> int | None:
    """-1 / 0 / 1 comme a <, =, > b ; None si l'une des deux n'est pas comparable."""
    pa, pb = version_parts(a), version_parts(b)
    if pa is None or pb is None:
        return None
    width = max(len(pa), len(pb))
    pa, pb = pa + (0,) * (width - len(pa)), pb + (0,) * (width - len(pb))
    return (pa > pb) - (pa < pb)


def same_numbering(a: str, b: str) -> bool:
    """True si deux versions suivent visiblement la même numérotation (donc comparables entre elles).

    Un paquet de fabricant porte souvent sa propre version, différente de celle du pilote qu'il installe
    (paquet Intel Ethernet 20.0.3.28 -> pilote 12.19.2.66). On exige le même nombre de nombres et le
    même premier nombre (10.1.50.1 / 10.1.46.3), ou le même deuxième (32.0.101.7085 / 30.0.101.2079).
    """
    pa, pb = version_parts(a), version_parts(b)
    if pa is None or pb is None or len(pa) != len(pb):
        return False
    return pa[0] == pb[0] or (len(pa) >= 3 and pa[1] == pb[1])


def newest(versions: list[str]) -> str:
    best = ""
    for v in versions:
        if version_parts(v) is not None and (not best or compare_versions(v, best) == 1):
            best = v
    return best


# --------------------------------------------------------------------------- correspondance matériel

def pnp_matches(pattern: str, dev: DeviceRef) -> bool:
    """'PCI\\VEN_8086&DEV_A0E8' correspond à 'PCI\\VEN_8086&DEV_A0E8&SUBSYS_…\\3&1'."""
    p = (pattern or "").strip().upper()
    if not p:
        return False
    return dev.device_id.upper().startswith(p) or dev.hardware_id.upper().startswith(p)


def same_chip(pattern: str, dev: DeviceRef) -> bool:
    """Même bus, même fabricant et même puce (VEN/DEV), sans tenir compte du SUBSYS."""
    bus, ven, chip = parse_ids(pattern)
    return bool(ven and chip) and (bus, ven, chip) == dev.ids


def subsys(device_id: str) -> str:
    """'…&SUBSYS_87D71043…' -> '87D71043'."""
    upper = (device_id or "").upper()
    i = upper.find("SUBSYS_")
    return upper[i + 7:i + 15] if i >= 0 else ""


# --------------------------------------------------------------------------- réseau, cache

def cache_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home())
    path = Path(base) / APP_ID / "cache" / "drivers"
    path.mkdir(parents=True, exist_ok=True)
    return path


def http_get_bytes(url: str, allowed_hosts: tuple[str, ...], timeout: float = 30) -> bytes:
    if not is_allowed_url(url, allowed_hosts):
        raise ValueError(f"Adresse refusée : {url}")
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - domaine vérifié
        if not is_allowed_url(response.geturl(), allowed_hosts):
            raise ValueError(f"Redirection refusée : {response.geturl()}")
        return response.read()


def cached_download(url: str, allowed_hosts: tuple[str, ...], name: str, max_age_hours: float = 24,
                    timeout: float = 60) -> Path:
    """Télécharge `url` dans le cache (réutilisé pendant `max_age_hours`)."""
    path = cache_dir() / name
    if path.exists() and time.time() - path.stat().st_mtime < max_age_hours * 3600:
        return path
    data = http_get_bytes(url, allowed_hosts, timeout)
    tmp = path.with_suffix(path.suffix + ".part")
    tmp.write_bytes(data)
    tmp.replace(path)
    return path


def is_not_found(exc: BaseException) -> bool:
    return isinstance(exc, urllib.error.HTTPError) and exc.code == 404


def extract_cab(cab: Path) -> Path:
    """Extrait l'unique fichier d'une archive .cab avec l'outil intégré à Windows (expand.exe)."""
    from app.utils.system import run_command

    expand = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "expand.exe"
    target = cab.with_suffix(".xml")
    if target.exists() and target.stat().st_mtime >= cab.stat().st_mtime:
        return target
    workdir = Path(tempfile.mkdtemp(prefix="cab_", dir=cache_dir()))
    try:
        result = run_command([str(expand), str(cab), "-F:*", str(workdir)], timeout=180)
        files = [p for p in workdir.iterdir() if p.is_file()]
        if not result.ok or len(files) != 1:
            raise IOError(f"Extraction impossible de {cab.name}")
        files[0].replace(target)
        # expand.exe conserve la date interne de l'archive : sans cela le fichier paraîtrait toujours plus
        # ancien que le .cab et serait ré-extrait (57 Mo pour Dell) à chaque analyse.
        os.utime(target)
        return target
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def read_xml_text(data: bytes) -> str:
    """Décode un XML Dell/HP/Lenovo (UTF-16 ou UTF-8, avec ou sans BOM)."""
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        text = data.decode("utf-16")
    else:
        text = data.decode("utf-8-sig", errors="replace")
    # ElementTree refuse une déclaration d'encodage quand on lui donne déjà du texte décodé.
    if text.startswith("<?xml"):
        end = text.find("?>")
        text = text[end + 2:]
    return text


def local_name(tag: str) -> str:
    """'{espace de noms}Nom' -> 'Nom' (les catalogues n'utilisent pas tous un espace de noms)."""
    return tag.rsplit("}", 1)[-1]


def find_all(elem, name: str) -> list:
    """Descendants (à toute profondeur) portant ce nom, espace de noms ignoré."""
    return [e for e in elem.iter() if local_name(e.tag) == name and e is not elem]


def find_text(elem, name: str) -> str:
    for e in find_all(elem, name):
        if e.text and e.text.strip():
            return e.text.strip()
    return ""


def file_time(path: Path) -> datetime:
    """Date à laquelle un catalogue a été téléchargé (= date de la vérification)."""
    return datetime.fromtimestamp(path.stat().st_mtime)


def current_windows() -> tuple[int, str]:
    """(numéro de build, version affichée comme « 24H2 ») de Windows."""
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows NT\CurrentVersion") as key:
            build = int(winreg.QueryValueEx(key, "CurrentBuildNumber")[0])
            try:
                release = str(winreg.QueryValueEx(key, "DisplayVersion")[0])
            except OSError:
                release = str(winreg.QueryValueEx(key, "ReleaseId")[0])
            return build, release
    except Exception:  # hors Windows ou registre illisible
        log.debug("Version de Windows inconnue", exc_info=True)
        return 0, ""
