"""Dell : catalogue officiel des pilotes (CatalogPC.cab, le même que Dell Command | Update).

Le catalogue est téléchargé en entier (aucun identifiant n'est envoyé à Dell) puis filtré
localement avec l'identifiant de modèle du PC (Win32_ComputerSystem.SystemSKUNumber).
"""

from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import IO
from urllib.parse import quote

from app.core.driver_sources.base import (DOWNLOAD, EXACT, PREFILLED, DeviceRef, DownloadLink, DriverSource,
                                          SourceRun, SystemContext, VendorCheck, cached_download, compare_versions,
                                          extract_cab, file_time, find_all, find_text, local_name, subsys,
                                          version_parts)
from app.core.vendors import board_maker

log = logging.getLogger(__name__)

CATALOG_URL = "https://downloads.dell.com/catalog/CatalogPC.cab"
DOWNLOAD_BASE = "https://downloads.dell.com/"
DETAILS_URL = "https://www.dell.com/support/home/fr-fr/drivers/driversdetails?driverid={}"
SEARCH_URL = "https://www.dell.com/support/search/fr-fr#q={}"
SOURCE_LABEL = "Catalogue officiel Dell"


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


def best(drivers: list[DellDriver]) -> DellDriver | None:
    comparable = [d for d in drivers if version_parts(d.version) is not None]
    if not comparable:
        return drivers[0] if drivers else None
    result = comparable[0]
    for d in comparable[1:]:
        if compare_versions(d.version, result.version) == 1:
            result = d
    return result


def make_check(dev: DeviceRef, drv: DellDriver, checked_at) -> VendorCheck:
    cmp = compare_versions(drv.version, dev.version)
    return VendorCheck(
        source=SOURCE_LABEL, latest_version=drv.version, update_available=cmp == 1, checked_at=checked_at,
        title=drv.name, release_date=drv.release_date, page_url=drv.page_url, download_url=drv.download_url,
        extra=drv, comparable=cmp is not None,
    )


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
        for dev in devices:
            drv = best(matching(drivers, dev))
            if drv:
                run.add(dev.key, make_check(dev, drv, checked_at))
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
