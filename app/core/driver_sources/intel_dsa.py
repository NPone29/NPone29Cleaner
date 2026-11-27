"""Intel : vérification automatique avec les données publiques d'Intel Driver & Support Assistant (DSA).

L'outil officiel d'Intel télécharge une liste publique de ses pilotes :
    https://dsadata.intel.com/data/en      (archive ZIP, ~150 Ko)
Le fichier ``software-configurations.json`` qu'elle contient décrit, pour chaque paquet (cartes graphiques
Intel / Arc, Wi-Fi, Bluetooth, Ethernet, Killer, NPU…) :
  * la version du pilote installé par chaque composant du paquet ;
  * les identifiants matériels concernés (ex. ``VEN_8086&DEV_2723&SUBSYS_*8086``, jokers compris) ;
  * les versions de Windows prises en charge ;
  * l'installateur, sur ``downloadmirror.intel.com``, avec son empreinte SHA-1.

On ne lit QUE ce fichier JSON : les autres fichiers de l'archive (bibliothèques .dll de l'outil Intel) ne sont
ni extraits ni exécutés. Aucune donnée n'est envoyée : la liste est téléchargée en entier et comparée sur le PC.

Limites (affichées à l'utilisateur) : Intel ne publie dans cette liste qu'une partie de ses pilotes (pas le
chipset, ni l'audio, ni le stockage RST), et ces pilotes sont « génériques » : sur certains PC portables, le
fabricant du PC recommande sa propre version.
"""

from __future__ import annotations

import io
import json
import logging
import re
import zipfile
from dataclasses import dataclass
from datetime import datetime
from fnmatch import fnmatchcase

from app.core import packages
from app.core.driver_sources.base import (DOWNLOAD, EXACT, GENERAL, SEARCH, DeviceRef, DownloadLink, DriverSource,
                                          SourceRun, SystemContext, VendorCheck, cached_download, compare_versions,
                                          version_parts)
from app.core.vendors import INTEL, vendor_for, web_search_url

log = logging.getLogger(__name__)

FEED_URL = "https://dsadata.intel.com/data/en"
FEED_HOSTS = ("dsadata.intel.com",)
DOWNLOAD_HOSTS = ("downloadmirror.intel.com",)
PAGE_HOSTS = (".intel.com", ".intel.fr")
FEED_FILE = "software-configurations.json"
MAX_FEED_BYTES = 50 * 1024 * 1024
SOURCE_LABEL = "Intel Driver & Support Assistant"
PUBLISHER = "Intel Corporation"
# Composants qui sont des pilotes de périphériques (« OtherDownloads » = logiciels détectés autrement).
DRIVER_CATEGORIES = {"Graphics", "Wireless", "Bluetooth", "LAN", "OtherDrivers"}
INTEL_VENDOR_IDS = {"8086", "8087"}       # 8087 : Bluetooth Intel (USB)
_CHIP_RE = re.compile(r"(?:VEN_[0-9A-F]{4}&DEV_[0-9A-F]{4}|VID_[0-9A-F]{4}&PID_[0-9A-F]{4})")


@dataclass(frozen=True)
class IntelFile:
    url: str
    sha1: str
    size: int
    systems: frozenset[str]


@dataclass(frozen=True)
class IntelComponent:
    category: str
    version: str
    patterns: tuple[str, ...]


@dataclass(frozen=True)
class IntelPackage:
    id: int
    name: str
    version: str
    date: str                     # « 2026-10-20 »
    page_url: str
    beta: bool
    restricted: bool              # réservé aux produits Intel (NUC…) ou à des cartes mères précises
    files: tuple[IntelFile, ...]
    components: tuple[IntelComponent, ...]


# --------------------------------------------------------------------------- lecture de la liste

def parse_feed(data: bytes) -> list[IntelPackage]:
    """Archive ZIP de dsadata.intel.com -> paquets. Seul software-configurations.json est lu."""
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        info = archive.getinfo(FEED_FILE)
        if info.file_size > MAX_FEED_BYTES:
            raise ValueError("Liste Intel anormalement volumineuse.")
        return parse_configurations(json.loads(archive.read(info).decode("utf-8-sig")))


def parse_configurations(raw: list) -> list[IntelPackage]:
    out: list[IntelPackage] = []
    for entry in raw if isinstance(raw, list) else []:
        try:
            files = tuple(IntelFile(str(f.get("Url", "")), str(f.get("Hash", "") or ""), int(f.get("Size") or 0),
                                    frozenset(s.lower() for s in f.get("OperatingSystems") or []))
                          for f in entry.get("Files") or [] if f.get("Url"))
            components = tuple(
                IntelComponent(str(c.get("Category", "")), str(c.get("Version") or ""),
                               tuple(str(v).upper() for v in c.get("DetectionValues") or []))
                for c in entry.get("Components") or [] if c.get("DetectionType", "Default") == "Default")
            out.append(IntelPackage(
                id=int(entry.get("Id") or 0), name=str(entry.get("Name", "")).strip(),
                version=str(entry.get("Version", "")).strip(), date=str(entry.get("DisplayReleaseDate", ""))[:10],
                page_url=str(entry.get("Url", "") or ""), beta=bool(entry.get("IsBeta")),
                restricted=bool(entry.get("IntelSystemOnly") or entry.get("BoardNumbers")
                                or entry.get("ModelNumbers")),
                files=files, components=components))
        except (TypeError, ValueError, AttributeError) as exc:
            log.debug("Entrée Intel ignorée : %s", exc)
    return out


def load_feed() -> list[IntelPackage]:
    path = cached_download(FEED_URL, FEED_HOSTS, "intel_dsa.zip", max_age_hours=24)
    return parse_feed(path.read_bytes())


# --------------------------------------------------------------------------- règles

def windows_id(ctx: SystemContext, machine: str) -> str:
    """Identifiant Windows au format Intel : « windows-11-24h2-64 ». Vide si non pris en charge (ARM)."""
    bits = {"AMD64": "64", "X86": "32"}.get(machine.upper())
    if not bits or not ctx.windows_build:
        return ""
    major = "11" if ctx.is_windows11 else "10"
    release = (ctx.windows_release or "inconnue").lower()
    return f"windows-{major}-{release}-{bits}"


def file_for(package: IntelPackage, system: str, known: set[str]) -> IntelFile | None:
    """Fichier du paquet prévu pour cette version de Windows.

    Une version de Windows plus récente que la liste d'Intel (inconnue de toute la liste) est traitée comme la
    même génération de Windows (même « windows-11-…-64 »).
    """
    if not system:
        return None
    for f in package.files:
        if system in f.systems:
            return f
    if system in known:
        return None
    parts = system.split("-")                     # windows / 11 / 24h2 / 64
    family, bits = "-".join(parts[:2]), parts[-1]
    for f in package.files:
        if any(s.startswith(family + "-") and s.endswith("-" + bits) for s in f.systems):
            return f
    return None


def device_ids(dev: DeviceRef) -> list[str]:
    """Identifiants du périphérique sans le bus, du plus précis au moins précis (« VEN_8086&DEV_2723&… »)."""
    ids = [*dev.hardware_ids, *dev.compatible_ids] or [dev.hardware_id or dev.device_id]
    out = []
    for value in ids:
        if value and "\\" in value:
            short = value.split("\\", 1)[1].upper()
            if short.startswith(("VEN_", "VID_")) and short not in out:
                out.append(short)
    # La forme courte (puce seule) n'est pas toujours listée par Windows : Intel s'en sert pourtant souvent.
    for value in list(out):
        chip = _CHIP_RE.match(value)
        if chip and chip.group(0) not in out:
            out.append(chip.group(0))
    return out


def matches(component: IntelComponent, ids: list[str]) -> bool:
    return any(fnmatchcase(i, pattern) for pattern in component.patterns
               if pattern.startswith(("VEN_", "VID_")) for i in ids)


def comparable(latest: str, installed: str) -> bool:
    """Les versions de composants Intel sont celles des pilotes : même nombre de nombres suffit."""
    a, b = version_parts(latest), version_parts(installed)
    return a is not None and b is not None and len(a) == len(b)


def is_pro(name: str) -> bool:
    return " pro" in f" {name.lower().replace('™', ' ').replace('®', ' ')} "


@dataclass(frozen=True)
class Candidate:
    package: IntelPackage
    component: IntelComponent
    file: IntelFile


def find_candidate(dev: DeviceRef, catalog: list[IntelPackage], system: str, known: set[str]) -> Candidate | None:
    """Le pilote Intel le plus récent prévu pour ce périphérique et cette version de Windows."""
    ids = device_ids(dev)
    if not ids:
        return None
    found: list[Candidate] = []
    for package in catalog:
        if package.beta or package.restricted:
            continue
        f = file_for(package, system, known)
        if f is None:
            continue
        for component in package.components:
            if component.category not in DRIVER_CATEGORIES or not comparable(component.version, dev.version):
                continue
            if matches(component, ids):
                found.append(Candidate(package, component, f))
                break
    if not found:
        return None
    # Carte Arc Pro / Arc grand public : un même identifiant peut figurer dans les deux familles de pilotes.
    same_family = [c for c in found if is_pro(c.package.name) == is_pro(dev.name)]
    found = same_family or found
    best = found[0]
    for c in found[1:]:
        order = compare_versions(c.component.version, best.component.version)
        # À version égale : le paquet le plus léger (pilote seul plutôt qu'une suite logicielle).
        if order == 1 or (order == 0 and 0 < c.file.size < best.file.size):
            best = c
    return best


def to_package(c: Candidate) -> packages.VendorPackage:
    return packages.VendorPackage(
        vendor="Intel", title=f"{c.package.name} — {c.component.version}", version=c.component.version,
        download_url=c.file.url, allowed_hosts=DOWNLOAD_HOSTS, publisher=PUBLISHER, size=c.file.size,
        sha1=c.file.sha1, page_url=c.package.page_url,
        advice="Installateur Intel : suivez ses étapes (« Installer », puis « Fermer » ou « Redémarrer »).")


def launchable(url: str) -> bool:
    """Fichier qu'on peut vérifier (signature Windows) puis lancer : .exe ou .msi. Pas une archive .zip."""
    return url.lower().split("?", 1)[0].endswith((".exe", ".msi"))


def make_check(dev: DeviceRef, c: Candidate, now: datetime, attempts: dict[str, dict] | None = None) -> VendorCheck:
    latest = c.component.version
    order = compare_versions(latest, dev.version)
    newer = order == 1
    note = ""
    generic = False
    if newer and packages.already_tried(attempts or {}, dev.key, latest, dev.version):
        # Installé une fois avec succès, mais Windows garde le pilote du fabricant du PC : on ne boucle pas.
        newer, generic = False, True
        note = ("Ce pilote Intel a déjà été installé une fois, mais Windows a gardé votre pilote actuel (celui du "
                "fabricant de votre PC est prioritaire).")
    elif newer and not launchable(c.file.url):
        # Ex. « Wired_driver_31.3_x64.zip » : une archive n'a pas de signature Windows et ne se lance pas ;
        # l'installation automatique échouait à chaque fois (« fichier supprimé par sécurité »).
        note = ("Intel ne fournit ce pilote que sous forme d'archive ZIP : téléchargez-le avec le lien ci-dessous, "
                "décompressez-le puis lancez l'installateur qu'il contient.")
    elif newer and c.component.category == "Graphics":
        note = ("Pilote générique d'Intel : sur certains PC portables, le fabricant du PC recommande sa propre "
                "version.")
    auto = newer and launchable(c.file.url)
    return VendorCheck(
        source=SOURCE_LABEL, latest_version=latest, update_available=newer, checked_at=now,
        title=c.package.name, release_date=c.package.date, page_url=c.package.page_url,
        download_url=c.file.url, extra=to_package(c) if auto else None, comparable=True, note=note,
        generic_newer=generic)


# --------------------------------------------------------------------------- source

def is_intel_device(dev: DeviceRef) -> bool:
    from app.core.vendors import parse_ids

    _bus, ven, _chip = parse_ids(dev.device_id)
    return ven in INTEL_VENDOR_IDS or vendor_for(dev.device_id, dev.vendor_name).name == INTEL.name


class IntelSource(DriverSource):
    name = "Intel"
    has_public_api = True
    allowed_hosts = PAGE_HOSTS + DOWNLOAD_HOSTS

    def applies_to_device(self, dev: DeviceRef) -> bool:
        return is_intel_device(dev)

    def check(self, ctx: SystemContext, devices: list[DeviceRef], run: SourceRun, progress=None) -> None:
        from app.core.driver_sources.microsoft_catalog import machine_architecture

        targets = [d for d in devices if self.applies_to_device(d)]
        if not targets:
            return
        if progress:
            progress("Intel : lecture de la liste officielle des pilotes (Intel Driver & Support Assistant)…")
        catalog = load_feed()
        system = windows_id(ctx, machine_architecture())
        known = {s for p in catalog for f in p.files for s in f.systems}
        attempts = packages.load_attempts()
        now = datetime.now()
        for dev in targets:
            candidate = find_candidate(dev, catalog, system, known)
            if candidate is not None:
                run.add(dev.key, make_check(dev, candidate, now, attempts))
        run.verified_by.append(self.name)

    def note_for(self, ctx: SystemContext, dev: DeviceRef, consulted: list[str]) -> str:
        if not self.applies_to_device(dev):
            return ""
        if self.name in consulted:
            return ("La liste officielle d'Intel (Intel Driver & Support Assistant) ne propose aucun pilote pour ce "
                    "périphérique : Intel n'y publie qu'une partie de ses pilotes (cartes graphiques, Wi-Fi, "
                    "Bluetooth, Ethernet).")
        return ("La liste officielle d'Intel n'a pas pu être consultée (connexion Internet ?) : son outil Intel "
                "Driver & Support Assistant détecte le pilote exact.")

    def links(self, ctx: SystemContext, dev: DeviceRef, checks: list[VendorCheck]) -> list[DownloadLink]:
        if not self.applies_to_device(dev):
            return []
        out = []
        for c in checks:
            if c.source == SOURCE_LABEL and c.update_available:
                if c.download_url:
                    out.append(DownloadLink(f"Télécharger le pilote Intel {c.latest_version}", c.download_url,
                                            DOWNLOAD))
                if c.page_url:
                    out.append(DownloadLink(f"Page du pilote Intel {c.latest_version}", c.page_url, EXACT))
        out += [DownloadLink("Intel Driver & Support Assistant (outil officiel)", INTEL.support_url, GENERAL),
                DownloadLink("Rechercher sur intel.com", web_search_url(dev.name, INTEL), SEARCH)]
        return [link for link in out if self.safe(link)]
