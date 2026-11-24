"""HP : fichiers de référence officiels de HP Image Assistant (hpia.hpcloud.hp.com).

Seul l'identifiant de plateforme (4 caractères hexadécimaux de la carte mère, ex. 8DE2) est
utilisé pour choisir le bon fichier : ni numéro de série, ni autre donnée personnelle.
"""

from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass

from app.core.driver_sources.base import (DOWNLOAD, EXACT, DeviceRef, DownloadLink, DriverSource, SourceRun,
                                          SystemContext, VendorCheck, cached_download, compare_versions,
                                          extract_cab, file_time, is_not_found, newest, pnp_matches, read_xml_text,
                                          same_chip)
from app.core.vendors import board_maker

log = logging.getLogger(__name__)

PLATFORM_LIST_URL = "https://hpia.hpcloud.hp.com/ref/platformList.cab"
REFERENCE_URL = "https://hpia.hpcloud.hp.com/ref/{pid}/{pid}_64_{os}.{release}.cab"
SOURCE_LABEL = "Catalogue officiel HP"
_PLATFORM_RE = re.compile(r"^[0-9A-Fa-f]{4}$")


@dataclass(frozen=True)
class HpOs:
    os_version: str     # « 10.0 »
    release: str        # « 24H2 »
    windows11: bool


@dataclass
class HpEntry:
    device_id: str      # motif PnP : PCI\VEN_…&DEV_…&SUBSYS_…
    driver_version: str
    softpaq: str
    name: str = ""
    category: str = ""
    softpaq_version: str = ""
    url: str = ""
    notes_url: str = ""
    date: str = ""


def _https(url: str) -> str:
    url = (url or "").strip()
    if not url:
        return ""
    if url.startswith("ftp://"):
        url = url[len("ftp://"):]
    if url.startswith("http://"):
        url = url[len("http://"):]
    return url if url.startswith("https://") else "https://" + url.lstrip("/")


def parse_platform_list(text: str, platform_id: str) -> list[HpOs]:
    """Versions de Windows pour lesquelles HP publie un fichier de référence pour cette plateforme."""
    wanted = platform_id.strip().upper()
    root = ET.fromstring(text)
    out: list[HpOs] = []
    for platform in root.iter("Platform"):
        ids = {(e.text or "").strip().upper() for e in platform.findall("SystemID")}
        if wanted not in ids:
            continue
        for os_elem in platform.findall("OS"):
            release = (os_elem.findtext("OSReleaseIdFilename") or os_elem.findtext("OSReleaseId") or "").strip()
            if not release:
                continue
            out.append(HpOs((os_elem.findtext("OSVersion") or "").strip(), release,
                            (os_elem.findtext("IsWindows11") or "").strip().lower() == "true"))
    return out


def release_key(release: str) -> tuple[int, int]:
    """« 24H2 » -> (24, 2) ; ancien format « 2009 » -> (20, 2), « 1909 » -> (19, 2)."""
    text = release.strip().upper()
    m = re.fullmatch(r"(\d{2})H(\d)", text)
    if m:
        return int(m.group(1)), int(m.group(2))
    m = re.fullmatch(r"(\d{2})(\d{2})", text)
    if m:
        return int(m.group(1)), 1 if int(m.group(2)) <= 6 else 2
    return 0, 0


def choose_os(oses: list[HpOs], windows11: bool, release: str) -> HpOs | None:
    """La version de Windows du PC si HP la propose, sinon la plus récente de la même génération."""
    same = [o for o in oses if o.windows11 == windows11]
    if not same:
        return None
    for o in same:
        if o.release.upper() == release.strip().upper():
            return o
    return max(same, key=lambda o: release_key(o.release))


def reference_url(platform_id: str, chosen: HpOs) -> str:
    pid = platform_id.strip().lower()
    return REFERENCE_URL.format(pid=pid, os="11.0" if chosen.windows11 else "10.0", release=chosen.release.lower())


def parse_reference(text: str) -> list[HpEntry]:
    """Fichier de référence HP -> une entrée par périphérique pris en charge."""
    root = ET.fromstring(text)
    solutions: dict[str, ET.Element] = {}
    block = root.find("Solutions")   # enfant direct (SystemInfo/System/Solutions est autre chose)
    if block is not None:
        for info in block.findall("UpdateInfo"):
            sid = (info.findtext("Id") or "").strip()
            if sid:
                solutions[sid] = info
    entries: list[HpEntry] = []
    devices = root.find("Devices")
    for device in (devices.findall("Device") if devices is not None else []):
        pattern = (device.findtext("DeviceId") or "").strip()
        version = (device.findtext("DriverVersion") or "").strip()
        refs = [u.get("IdRef", "") for u in device.findall("Solutions/UpdateInfo")]
        if not pattern or not refs:
            continue
        info = solutions.get(refs[0])
        if info is None:
            continue
        entries.append(HpEntry(
            device_id=pattern, driver_version=version, softpaq=refs[0],
            name=(info.findtext("Name") or "").strip(), category=(info.findtext("Category") or "").strip(),
            softpaq_version=(info.findtext("Version") or "").strip(), url=_https(info.findtext("Url") or ""),
            notes_url=_https(info.findtext("ReleaseNotesUrl") or ""),
            date=(info.findtext("DateReleased") or "").strip(),
        ))
    return entries


def matching(entries: list[HpEntry], dev: DeviceRef) -> list[HpEntry]:
    """Même SUBSYS en priorité, sinon même puce (VEN/DEV)."""
    exact = [e for e in entries if pnp_matches(e.device_id, dev)]
    return exact or [e for e in entries if same_chip(e.device_id, dev)]


def make_check(dev: DeviceRef, entries: list[HpEntry], checked_at) -> VendorCheck | None:
    if not entries:
        return None
    latest = newest([e.driver_version for e in entries])
    entry = next((e for e in entries if e.driver_version == latest), entries[0])
    cmp = compare_versions(entry.driver_version, dev.version)
    return VendorCheck(
        source=SOURCE_LABEL, latest_version=entry.driver_version, update_available=cmp == 1,
        checked_at=checked_at, title=entry.name, release_date=entry.date, page_url=entry.notes_url,
        download_url=entry.url, extra=entry, comparable=cmp is not None,
    )


class HpSource(DriverSource):
    name = "HP"
    has_public_api = True
    allowed_hosts = (".hp.com", ".hpcloud.hp.com")

    def applies_to_system(self, ctx: SystemContext) -> bool:
        maker = board_maker(ctx.system_maker)
        return bool(maker and maker.name == "HP")

    def check(self, ctx: SystemContext, devices: list[DeviceRef], run: SourceRun, progress=None) -> None:
        if not self.applies_to_system(ctx):
            return
        platform_id = ctx.board_product.strip()
        if not _PLATFORM_RE.match(platform_id):
            run.notes.append("Identifiant de plateforme HP introuvable : vérification dans le catalogue HP impossible.")
            return
        if progress:
            progress("Recherche du catalogue officiel HP de votre modèle…")
        cab = cached_download(PLATFORM_LIST_URL, self.allowed_hosts, "hp_platformList.cab")
        oses = parse_platform_list(read_xml_text(extract_cab(cab).read_bytes()), platform_id)
        chosen = choose_os(oses, ctx.is_windows11, ctx.windows_release)
        if chosen is None:
            run.notes.append(f"HP ne publie pas de catalogue de pilotes pour ce modèle ({platform_id}) "
                             "et cette version de Windows.")
            return
        url = reference_url(platform_id, chosen)
        try:
            ref = cached_download(url, self.allowed_hosts, url.rsplit("/", 1)[-1])
        except Exception as exc:
            if is_not_found(exc):
                run.notes.append(f"Catalogue HP introuvable pour ce modèle ({platform_id}).")
                return
            raise
        if progress:
            progress("Lecture du catalogue officiel HP…")
        entries = parse_reference(read_xml_text(extract_cab(ref).read_bytes()))
        checked_at = file_time(ref)
        for dev in devices:
            check = make_check(dev, matching(entries, dev), checked_at)
            if check:
                run.add(dev.key, check)
        run.verified_by.append(self.name)

    def note_for(self, ctx: SystemContext, dev: DeviceRef, consulted: list[str]) -> str:
        if self.name in consulted:
            return "Ce périphérique ne figure pas dans le catalogue officiel HP de votre modèle."
        return ""

    def links(self, ctx: SystemContext, dev: DeviceRef, checks: list[VendorCheck]) -> list[DownloadLink]:
        if not self.applies_to_system(ctx):
            return []
        out = []
        for c in checks:
            if c.source != SOURCE_LABEL:
                continue
            softpaq = getattr(c.extra, "softpaq", "")
            if c.download_url:
                out.append(DownloadLink(f"Télécharger le SoftPaq HP {softpaq}".rstrip(), c.download_url, DOWNLOAD))
            if c.page_url:
                out.append(DownloadLink("Notes de version HP", c.page_url, EXACT))
        return [link for link in out if self.safe(link)]
