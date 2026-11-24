"""Lenovo : catalogue officiel par type de machine (celui de Lenovo System Update / Vantage).

Seul le type de machine (4 premiers caractères du modèle, ex. 20XW) est utilisé.
"""

from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from app.core.driver_sources.base import (DOWNLOAD, EXACT, PREFILLED, DeviceRef, DownloadLink, DriverSource,
                                          SourceRun, SystemContext, VendorCheck, cached_download, compare_versions,
                                          file_time, is_not_found, newest, pnp_matches, read_xml_text)
from app.core.vendors import board_maker
from app.utils.download import is_allowed_url

log = logging.getLogger(__name__)

CATALOG_URL = "https://download.lenovo.com/catalog/{mt}_{os}.xml"
SUPPORT_URL = "https://pcsupport.lenovo.com/fr/fr/products/{mt}/downloads/driver-list"
SOURCE_LABEL = "Catalogue officiel Lenovo"
ALLOWED_HOSTS = (".lenovo.com",)
_MT_RE = re.compile(r"^[0-9A-Z]{4}$")


@dataclass
class LenovoPackage:
    package_id: str
    title: str
    version: str
    release_date: str
    drivers: list[tuple[list[str], str]] = field(default_factory=list)   # (ID matériels, version du pilote)
    download_url: str = ""
    readme_url: str = ""


def machine_type(model: str) -> str:
    mt = (model or "").strip().upper()[:4]
    return mt if _MT_RE.match(mt) else ""


def parse_catalog(text: str) -> list[str]:
    """Adresses des descripteurs de paquets listés dans le catalogue du modèle."""
    root = ET.fromstring(text)
    out = []
    for loc in root.iter("location"):
        url = (loc.text or "").strip()
        if url and is_allowed_url(url, ALLOWED_HOSTS):
            out.append(url)
    return out


def _folder(url: str) -> str:
    return url.rsplit("/", 1)[0] + "/"


def parse_package(text: str, location: str) -> LenovoPackage | None:
    """Descripteur d'un paquet -> pilote(s) qu'il installe ; None si ce n'est pas un pilote."""
    root = ET.fromstring(text)
    detect = root.find("DetectInstall")
    if detect is None:
        return None
    drivers = []
    for drv in detect.iter("_Driver"):
        hwids = [(h.text or "").strip() for h in drv.findall("HardwareID") if (h.text or "").strip()]
        version = (drv.findtext("Version") or "").strip().rstrip("^").strip()
        if hwids:
            drivers.append((hwids, version))
    if not drivers:
        return None
    folder = _folder(location)
    installer = (root.findtext("Files/Installer/File/Name") or "").strip()
    readme = (root.findtext("Files/Readme/File/Name") or "").strip()
    return LenovoPackage(
        package_id=root.get("id", "") or root.get("name", ""),
        title=(root.findtext("Title/Desc") or "").strip(),
        version=root.get("version", ""),
        release_date=(root.findtext("ReleaseDate") or "").strip(),
        drivers=drivers,
        download_url=folder + installer if installer else "",
        readme_url=folder + readme if readme else "",
    )


def make_check(dev: DeviceRef, packages: list[LenovoPackage], checked_at) -> VendorCheck | None:
    """Le paquet le plus récent qui prend en charge ce périphérique."""
    candidates: list[tuple[str, LenovoPackage]] = []
    for pkg in packages:
        for hwids, version in pkg.drivers:
            if any(pnp_matches(h, dev) for h in hwids):
                candidates.append((version, pkg))
                break
    if not candidates:
        return None
    latest = newest([v for v, _ in candidates])
    version, pkg = next(((v, p) for v, p in candidates if v == latest), candidates[0])
    cmp = compare_versions(version, dev.version)
    return VendorCheck(
        source=SOURCE_LABEL, latest_version=version, update_available=cmp == 1, checked_at=checked_at,
        title=pkg.title, release_date=pkg.release_date, page_url=pkg.readme_url, download_url=pkg.download_url,
        extra=pkg, comparable=cmp is not None,
    )


class LenovoSource(DriverSource):
    name = "Lenovo"
    has_public_api = True
    allowed_hosts = ALLOWED_HOSTS

    def applies_to_system(self, ctx: SystemContext) -> bool:
        maker = board_maker(ctx.system_maker)
        return bool(maker and maker.name == "Lenovo")

    def _catalog(self, mt: str, ctx: SystemContext):
        order = ["Win11", "Win10"] if ctx.is_windows11 else ["Win10", "Win11"]
        for i, os_name in enumerate(order):
            try:
                return cached_download(CATALOG_URL.format(mt=mt, os=os_name), self.allowed_hosts,
                                       f"lenovo_{mt}_{os_name}.xml")
            except Exception as exc:
                if not is_not_found(exc) or i == len(order) - 1:
                    raise
        return None

    def check(self, ctx: SystemContext, devices: list[DeviceRef], run: SourceRun, progress=None) -> None:
        if not self.applies_to_system(ctx):
            return
        mt = machine_type(ctx.model)
        if not mt:
            run.notes.append("Type de machine Lenovo introuvable : vérification dans le catalogue Lenovo impossible.")
            return
        if progress:
            progress("Téléchargement du catalogue officiel Lenovo…")
        try:
            catalog = self._catalog(mt, ctx)
        except Exception as exc:
            if is_not_found(exc):
                run.notes.append(f"Lenovo ne publie pas de catalogue de pilotes pour ce modèle ({mt}).")
                return
            raise
        locations = parse_catalog(read_xml_text(catalog.read_bytes()))
        if progress:
            progress(f"Lecture des {len(locations)} paquets du catalogue Lenovo…")

        def load(url: str) -> LenovoPackage | None:
            try:
                path = cached_download(url, self.allowed_hosts, "lenovo_" + url.rsplit("/", 1)[-1])
                return parse_package(read_xml_text(path.read_bytes()), url)
            except Exception as exc:
                log.debug("Paquet Lenovo illisible %s : %s", url, exc)
                return None

        with ThreadPoolExecutor(max_workers=8, thread_name_prefix="lenovo") as pool:
            packages = [p for p in pool.map(load, locations) if p is not None]
        checked_at = file_time(catalog)
        for dev in devices:
            check = make_check(dev, packages, checked_at)
            if check:
                run.add(dev.key, check)
        run.verified_by.append(self.name)

    def note_for(self, ctx: SystemContext, dev: DeviceRef, consulted: list[str]) -> str:
        if self.name in consulted:
            return "Ce périphérique ne figure pas dans le catalogue officiel Lenovo de votre modèle."
        return ""

    def links(self, ctx: SystemContext, dev: DeviceRef, checks: list[VendorCheck]) -> list[DownloadLink]:
        if not self.applies_to_system(ctx):
            return []
        out = []
        for c in checks:
            if c.source != SOURCE_LABEL:
                continue
            if c.download_url:
                out.append(DownloadLink(f"Télécharger le pilote Lenovo {c.latest_version}", c.download_url, DOWNLOAD))
            if c.page_url:
                out.append(DownloadLink("Notes de version Lenovo", c.page_url, EXACT))
        mt = machine_type(ctx.model)
        if mt:
            out.append(DownloadLink(f"Pilotes Lenovo de votre modèle ({mt})", SUPPORT_URL.format(mt=mt), PREFILLED))
        return [link for link in out if self.safe(link)]
