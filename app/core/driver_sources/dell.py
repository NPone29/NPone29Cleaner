"""Dell : catalogue officiel des pilotes (CatalogPC.cab, le même que Dell Command | Update).

Le catalogue est téléchargé en entier (aucun identifiant n'est envoyé à Dell) puis filtré
localement avec l'identifiant de modèle du PC (Win32_ComputerSystem.SystemSKUNumber).

Attention : la version d'un paquet Dell (« vendorVersion ») est celle du PAQUET, pas forcément celle
du pilote qu'il installe (paquet Intel Ethernet 20.0.3.28 -> pilote 12.19.2.66, paquet Intel ME
2543.8.42.0 -> pilote 2540.8.7.0). Pour ne pas réclamer sans fin une mise à jour déjà faite, on lit
aussi les journaux que chaque paquet Dell écrit sur le PC (C:\\ProgramData\\Dell\\UpdatePackage\\Log).
"""

from __future__ import annotations

import logging
import os
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import IO
from urllib.parse import quote

from app.core.driver_sources.base import (DOWNLOAD, EXACT, PREFILLED, DeviceRef, DownloadLink, DriverSource,
                                          SourceRun, SystemContext, VendorCheck, cached_download, compare_versions,
                                          extract_cab, file_time, find_all, find_text, local_name, same_numbering,
                                          subsys, version_parts)
from app.core.vendors import board_maker

log = logging.getLogger(__name__)

CATALOG_URL = "https://downloads.dell.com/catalog/CatalogPC.cab"
DOWNLOAD_BASE = "https://downloads.dell.com/"
DETAILS_URL = "https://www.dell.com/support/home/fr-fr/drivers/driversdetails?driverid={}"
SEARCH_URL = "https://www.dell.com/support/search/fr-fr#q={}"
SOURCE_LABEL = "Catalogue officiel Dell"

# Codes de sortie des paquets Dell (DUP) qui signifient « ce paquet est en place » :
# 0 succès, 2 succès mais redémarrage nécessaire, 3 même version (ou plus récente) déjà installée,
# 6 succès, redémarrage en cours.
APPLIED_EXIT_CODES = {0, 2, 3, 6}
REBOOT_EXIT_CODES = {2, 6}

# Catégorie Dell -> classes de périphériques Windows concernées.
CATEGORY_CLASSES = {
    "video": {"DISPLAY"}, "audio": {"MEDIA", "AUDIOPROCESSINGOBJECT"}, "network": {"NET", "BLUETOOTH"},
    "chipset": {"SYSTEM"}, "serial ata": {"HDC", "SCSIADAPTER"}, "storage": {"HDC", "SCSIADAPTER"},
    "input": {"HIDCLASS", "MOUSE", "KEYBOARD"}, "communications": {"PORTS"},
}
# Mots trop courants pour distinguer deux produits.
_STOPWORDS = {"intel", "r", "tm", "dell", "driver", "drivers", "and", "the", "for", "with", "application",
              "software", "package", "technology", "controller", "device", "devices", "windows", "installer"}
_MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct",
                                       "nov", "dec"], start=1)}


@dataclass
class DellDriver:
    release_id: str
    name: str
    version: str           # vendorVersion : version du pilote INF, comparable à celle de Windows
    dell_version: str      # A00, A01…
    release_date: str
    path: str
    category: str
    pci: list[tuple[str, str, str, str]] = field(default_factory=list)   # (ven, dev, sous-ven, sous-dev)

    @property
    def download_url(self) -> str:
        return DOWNLOAD_BASE + self.path.lstrip("/") if self.path else ""

    @property
    def page_url(self) -> str:
        return DETAILS_URL.format(quote(self.release_id)) if self.release_id else ""

    @property
    def package_key(self) -> str:
        """Identifiant écrit dans les journaux Dell : « PGN38A07 » (releaseID + dellVersion)."""
        return (self.release_id + self.dell_version).upper()

    @property
    def label(self) -> str:
        return f"{self.release_id} {self.dell_version}".strip()

    @property
    def family(self) -> str:
        """Nom du produit sans version : « Intel PCIe Ethernet Controller Driver »."""
        return self.name.split(",")[0].strip()


@dataclass
class AppliedPackage:
    """Un paquet Dell exécuté sur ce PC (d'après son journal)."""
    key: str                 # « PGN38A07 »
    exit_code: int
    when: datetime | None = None

    @property
    def reboot_needed(self) -> bool:
        return self.exit_code in REBOOT_EXIT_CODES


def _hex4(value: str) -> str:
    text = (value or "").strip().upper().removeprefix("0X")
    return text.zfill(4) if text else ""


def parse_catalog(stream: IO[bytes], sku: str) -> list[DellDriver]:
    """Pilotes (DRVR) du catalogue qui concernent le modèle `sku`. Lecture progressive (fichier de ~57 Mo)."""
    wanted = sku.strip().upper()
    found: list[DellDriver] = []
    if not wanted:
        return found
    root = None
    for event, elem in ET.iterparse(stream, events=("start", "end")):
        if event == "start":
            if root is None:
                root = elem
            continue
        if local_name(elem.tag) != "SoftwareComponent":
            continue
        driver = _parse_component(elem, wanted)
        if driver:
            found.append(driver)
        elem.clear()
        if root is not None:
            root.clear()   # libère les composants déjà lus
    return found


def _parse_component(elem, sku: str) -> DellDriver | None:
    types = find_all(elem, "ComponentType")
    if not types or types[0].get("value", "").upper() != "DRVR":
        return None
    systems = {m.get("systemID", "").strip().upper() for m in find_all(elem, "Model")}
    if sku not in systems:
        return None
    pci = [(_hex4(p.get("vendorID", "")), _hex4(p.get("deviceID", "")),
            _hex4(p.get("subVendorID", "")), _hex4(p.get("subDeviceID", "")))
           for p in find_all(elem, "PCIInfo")]
    pci = [ids for ids in pci if ids[0] and ids[1]]
    if not pci:
        return None
    name_elem = next(iter(find_all(elem, "Name")), None)
    category_elem = next(iter(find_all(elem, "Category")), None)
    return DellDriver(
        release_id=elem.get("releaseID", ""),
        name=find_text(name_elem, "Display") if name_elem is not None else "",
        version=elem.get("vendorVersion", ""),
        dell_version=elem.get("dellVersion", ""),
        release_date=elem.get("releaseDate", ""),
        path=elem.get("path", ""),
        category=find_text(category_elem, "Display") if category_elem is not None else "",
        pci=pci,
    )


def matching(drivers: list[DellDriver], dev: DeviceRef) -> list[DellDriver]:
    """Pilotes Dell prévus pour ce périphérique PCI (le SUBSYS doit correspondre s'il est précisé)."""
    bus, ven, chip = dev.ids
    if bus != "PCI" or not (ven and chip):
        return []
    sub = subsys(dev.device_id) or subsys(dev.hardware_id)   # SUBSYS_<sous-périph.><sous-fabricant>
    out = []
    for drv in drivers:
        for p_ven, p_dev, p_subven, p_subdev in drv.pci:
            if (p_ven, p_dev) != (ven, chip):
                continue
            if p_subven and p_subdev and sub and sub != p_subdev + p_subven:
                continue
            out.append(drv)
            break
    return out


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", (text or "").lower()) if w not in _STOPWORDS}


def relevant(drivers: list[DellDriver], dev: DeviceRef) -> list[DellDriver]:
    """Ne garde que les paquets qui visent vraiment ce périphérique.

    Dell liste parfois une même puce dans des paquets sans rapport (l'interface Intel ME apparaît dans
    les paquets Rapid Storage, HID Event Filter, Waves MaxxAudio…). Comparer leurs versions entre elles
    n'aurait aucun sens : on garde les paquets dont le nom ressemble le plus à celui du périphérique,
    puis ceux dont la catégorie correspond à sa classe.
    """
    if len(drivers) < 2:
        return drivers
    name_words = _words(dev.name)
    cls = dev.device_class.upper()

    def score(d: DellDriver) -> tuple[int, bool]:
        return len(_words(d.family) & name_words), cls in CATEGORY_CLASSES.get(d.category.strip().lower(), ())

    top = max(score(d) for d in drivers)
    return [d for d in drivers if score(d) == top]


def best(drivers: list[DellDriver]) -> DellDriver | None:
    comparable = [d for d in drivers if version_parts(d.version) is not None]
    if not comparable:
        return drivers[0] if drivers else None
    result = comparable[0]
    for d in comparable[1:]:
        if compare_versions(d.version, result.version) == 1:
            result = d
    return result


# --------------------------------------------------------------------------- paquets déjà installés

def log_dir() -> Path:
    return Path(os.environ.get("ProgramData") or r"C:\ProgramData") / "Dell" / "UpdatePackage" / "Log"


def _decode_log(data: bytes) -> str:
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return data.decode("utf-16", errors="replace")
    if len(data) > 1 and data[1:2] == b"\x00":          # UTF-16 sans BOM
        return data.decode("utf-16-le", errors="replace")
    return data.decode("utf-8-sig", errors="replace")


def _log_time(line: str) -> datetime | None:
    """« [Wed Nov 25 14:34:40 2026] … » -> datetime (indépendant de la langue de Windows)."""
    m = re.match(r"\s*\[\w{3}\s+(\w{3})\s+(\d{1,2})\s+(\d{1,2}):(\d{2}):(\d{2})\s+(\d{4})\]", line)
    if not m or m.group(1).lower() not in _MONTHS:
        return None
    try:
        return datetime(int(m.group(6)), _MONTHS[m.group(1).lower()], int(m.group(2)),
                        int(m.group(3)), int(m.group(4)), int(m.group(5)))
    except ValueError:
        return None


def parse_dup_log(text: str) -> list[AppliedPackage]:
    """Journal d'un paquet Dell -> une entrée par exécution (un fichier peut en contenir plusieurs)."""
    runs: list[AppliedPackage] = []
    current = ""
    for line in text.splitlines():
        m = re.search(r"DUP Release:\s*([0-9A-Za-z]+)", line)
        if m:
            current = m.group(1).upper()
            continue
        m = re.search(r"Exit Code set to:\s*(-?\d+)", line)
        if m and current:
            runs.append(AppliedPackage(current, int(m.group(1)), _log_time(line)))
            current = ""
    return runs


def applied_packages(folder: Path | None = None) -> dict[str, AppliedPackage]:
    """Paquets Dell installés avec succès sur ce PC (par Dell Command | Update ou à la main)."""
    folder = folder or log_dir()
    found: dict[str, AppliedPackage] = {}
    try:
        files = list(folder.glob("*.txt"))
    except OSError:
        return found
    for path in files:
        try:
            if path.stat().st_size > 5_000_000:
                continue
            runs = parse_dup_log(_decode_log(path.read_bytes()))
            fallback = file_time(path)
        except OSError:
            continue
        for run in runs:
            if run.exit_code not in APPLIED_EXIT_CODES:
                continue
            run.when = run.when or fallback
            known = found.get(run.key)
            if known is None or (known.when or datetime.min) < run.when:
                found[run.key] = run
    return found


# --------------------------------------------------------------------------- vérification

def make_check(dev: DeviceRef, candidates: list[DellDriver], checked_at,
               applied: dict[str, AppliedPackage] | None = None) -> VendorCheck | None:
    """Compare le pilote installé au paquet Dell le plus récent prévu pour ce périphérique."""
    drv = best(candidates)
    if drv is None:
        return None
    applied = applied or {}
    common = dict(source=SOURCE_LABEL, latest_version=drv.version, checked_at=checked_at, title=drv.name,
                  release_date=drv.release_date, page_url=drv.page_url, download_url=drv.download_url, extra=drv)

    done = [(d, applied[d.package_key]) for d in candidates if d.package_key in applied]
    done.sort(key=lambda item: item[1].when or datetime.min, reverse=True)
    current = [(d, a) for d, a in done if d is drv or compare_versions(d.version, drv.version) in (0, 1)]
    if current:
        # Le paquet le plus récent a déjà été installé : le pilote est à jour, même si Windows affiche
        # un autre numéro (celui du pilote contenu dans le paquet).
        d, a = current[0]
        when = f" le {a.when:%d/%m/%Y}" if a.when else ""
        note = f"Le paquet Dell le plus récent ({d.version}, {d.label}) a déjà été installé sur ce PC{when}."
        if compare_versions(d.version, dev.version) != 0:
            note += (f" Windows affiche {dev.version} : c'est la version du pilote contenu dans le paquet "
                     "(Dell numérote ses paquets différemment). Inutile de le réinstaller.")
        if a.reboot_needed:
            note += " Redémarrez le PC pour terminer son installation."
        return VendorCheck(update_available=False, comparable=True, note=note, **common)
    if done:
        d, a = done[0]
        when = f" le {a.when:%d/%m/%Y}" if a.when else ""
        note = (f"Le paquet Dell {d.version} ({d.label}) installé{when} a été remplacé par la version "
                f"{drv.version} ({drv.label}).")
        return VendorCheck(update_available=True, comparable=True, note=note, **common)
    if same_numbering(drv.version, dev.version):
        cmp = compare_versions(drv.version, dev.version)
        return VendorCheck(update_available=cmp == 1, comparable=cmp is not None, **common)
    note = (f"Dell propose le paquet « {drv.family or drv.name} » {drv.version} ({drv.label}), mais sa "
            f"numérotation ne correspond pas à celle du pilote installé ({dev.version}) : impossible de savoir "
            "automatiquement s'il est plus récent. Une fois ce paquet installé, l'application le reconnaîtra "
            "à la prochaine analyse.")
    return VendorCheck(update_available=False, comparable=False, note=note, **common)


class DellSource(DriverSource):
    name = "Dell"
    has_public_api = True
    allowed_hosts = (".dell.com",)

    def applies_to_system(self, ctx: SystemContext) -> bool:
        maker = board_maker(ctx.system_maker)
        return bool(maker and maker.name == "Dell")

    def check(self, ctx: SystemContext, devices: list[DeviceRef], run: SourceRun, progress=None) -> None:
        if not self.applies_to_system(ctx):
            return
        if not ctx.sku:
            run.notes.append("Identifiant du modèle Dell introuvable : vérification dans le catalogue Dell impossible.")
            return
        if progress:
            progress("Téléchargement du catalogue officiel Dell…")
        cab = cached_download(CATALOG_URL, self.allowed_hosts, "dell_CatalogPC.cab", timeout=120)
        if progress:
            progress("Lecture du catalogue officiel Dell…")
        xml_path = extract_cab(cab)
        with open(xml_path, "rb") as fh:
            drivers = parse_catalog(fh, ctx.sku)
        checked_at = file_time(cab)
        try:
            applied = applied_packages()
        except Exception:
            log.warning("Journaux des paquets Dell illisibles", exc_info=True)
            applied = {}
        log.info("Dell : %d pilote(s) au catalogue pour ce modèle, %d paquet(s) déjà installé(s)",
                 len(drivers), len(applied))
        for dev in devices:
            check = make_check(dev, relevant(matching(drivers, dev), dev), checked_at, applied)
            if check:
                run.add(dev.key, check)
        run.verified_by.append(self.name)

    def note_for(self, ctx: SystemContext, dev: DeviceRef, consulted: list[str]) -> str:
        if self.name in consulted:
            return "Ce périphérique ne figure pas dans le catalogue officiel Dell de votre modèle."
        return ""

    def links(self, ctx: SystemContext, dev: DeviceRef, checks: list[VendorCheck]) -> list[DownloadLink]:
        if not self.applies_to_system(ctx):
            return []
        out = []
        for c in checks:
            if c.source != SOURCE_LABEL:
                continue
            if c.download_url:
                out.append(DownloadLink(f"Télécharger le pilote Dell {c.latest_version}", c.download_url, DOWNLOAD))
            if c.page_url:
                out.append(DownloadLink("Page du pilote chez Dell", c.page_url, EXACT))
        if ctx.model:
            out.append(DownloadLink(f"Support Dell : {ctx.model}", SEARCH_URL.format(quote(ctx.model)), PREFILLED))
        return [link for link in out if self.safe(link)]
