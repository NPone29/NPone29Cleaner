"""Catalogue Microsoft Update : la base officielle des pilotes publiés pour Windows.

Les fabricants de puces et de PC (Intel, Realtek, Killer, Acer…) y publient leurs pilotes, signés par
Microsoft, avec la liste exacte des identifiants matériels pris en charge. Windows Update n'en propose
qu'une partie à chaque PC ; le catalogue, lui, se consulte librement :
    https://www.catalog.update.microsoft.com/Search.aspx?q=<identifiant de la puce>
Seul l'identifiant de la puce est envoyé (ex. PCI\\VEN_8086&DEV_06F0) : aucune donnée personnelle.

Pour ne jamais proposer un pilote que Windows refuserait (sinon l'application le redemanderait sans fin
après l'installation), on applique les règles de classement de Windows :
  1. le pilote doit déclarer l'un des identifiants du périphérique ;
  2. l'identifiant le plus précis l'emporte (…&SUBSYS_143F1025, propre au modèle du PC, avant VEN&DEV) ;
  3. à précision égale, la date du pilote la plus récente, puis le numéro de version.
Un pilote plus récent mais moins précis que le pilote installé (version « générique » du fabricant de la
puce, alors que le fabricant du PC fournit la sienne) n'est pas installé : il est seulement signalé.
"""

from __future__ import annotations

import base64
import hashlib
import html
import json
import logging
import os
import platform
import re
import shutil
import tempfile
import threading
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

from app.core.driver_sources.base import (EXACT, DeviceRef, DownloadLink, DriverSource, SourceRun, SystemContext,
                                          VendorCheck, cache_dir, compare_versions, version_parts)
from app.utils.download import is_allowed_url

log = logging.getLogger(__name__)

SOURCE_LABEL = "Catalogue Microsoft Update"
CATALOG = "https://www.catalog.update.microsoft.com"
SEARCH_URL = CATALOG + "/Search.aspx?q={}"
DETAILS_URL = CATALOG + "/ScopedViewInline.aspx?updateid={}"
DOWNLOAD_DIALOG_URL = CATALOG + "/DownloadDialog.aspx"
CATALOG_HOSTS = ("www.catalog.update.microsoft.com",)
DOWNLOAD_HOSTS = (".download.windowsupdate.com", ".windowsupdate.com")
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) NPone29Cleaner"

SEARCH_MAX_AGE = 12 * 3600          # une recherche est réutilisée pendant 12 h
DETAILS_MAX_AGE = 60 * 24 * 3600    # la fiche d'un pilote publié ne change pas
MAX_DETAILS_PER_DEVICE = 15         # fiches consultées au plus par périphérique
MAX_SEARCH_TERMS = 3
WORKERS = 8
CACHE_VERSION = 2
_UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
_VERSION_RE = re.compile(r"\d+(?:\.\d+){1,3}")

# Version de Windows 11 (« 24H2 ») ou de Windows 10 (« 1809 ») -> numéro de build minimal.
WIN11_RELEASES = {"21H2": 22000, "22H2": 22621, "23H2": 22631, "24H2": 26100, "25H2": 26200, "26H1": 28000}
WIN10_RELEASES = {"1507": 10240, "1511": 10586, "1607": 14393, "1703": 15063, "1709": 16299, "1803": 17134,
                  "1809": 17763, "1903": 18362, "1909": 18363, "2004": 19041, "20H2": 19042, "21H1": 19043,
                  "21H2": 19044, "22H2": 19045}
UNKNOWN_RELEASE = 10 ** 6


# --------------------------------------------------------------------------- modèles

@dataclass
class CatalogEntry:
    """Une ligne des résultats de recherche."""
    update_id: str
    title: str
    products: str
    classification: str
    last_updated: str     # « 4/12/2026 » (format américain du site)
    version: str
    size: int = 0


@dataclass
class CatalogDriver:
    """La fiche détaillée d'un pilote du catalogue."""
    update_id: str
    title: str
    version: str
    date: str               # date du pilote (DriverVer), « 12/24/2025 »
    architecture: str
    products: str
    hardware_ids: list[str] = field(default_factory=list)
    manufacturer: str = ""
    provider: str = ""
    model: str = ""
    driver_class: str = ""
    size: int = 0
    company: str = ""

    @property
    def driver_date(self) -> datetime | None:
        return us_date(self.date)

    @property
    def page_url(self) -> str:
        return DETAILS_URL.format(self.update_id)

    @property
    def size_text(self) -> str:
        from app.utils.fmt import human_size

        return human_size(self.size) if self.size else "taille inconnue"


@dataclass
class Candidate:
    driver: CatalogDriver
    index: int      # position de l'identifiant correspondant dans la liste du périphérique (0 = le plus précis)


# --------------------------------------------------------------------------- lecture des pages

def _text(fragment: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", fragment))).strip()


def us_date(text: str) -> datetime | None:
    try:
        return datetime.strptime((text or "").strip(), "%m/%d/%Y")
    except ValueError:
        return None


def entry_version(entry: CatalogEntry) -> str:
    """Version d'une ligne : colonne « Version », sinon dernier numéro du titre (« … - 10.26.0.4 »)."""
    if version_parts(entry.version) is not None:
        return entry.version
    found = _VERSION_RE.findall(entry.title)
    return found[-1] if found else ""


def parse_search(page: str) -> list[CatalogEntry]:
    entries = []
    for m in re.finditer(rf'<tr id="({_UUID})_R\d+"(.*?)</tr>', page, re.S | re.I):
        uid, body = m.group(1).lower(), m.group(2)
        cells = {int(c): _text(v) for c, v in re.findall(rf'id="{_UUID}_C(\d)_R\d+">(.*?)</td>', body, re.S | re.I)}
        size = re.search(r'_originalSize">\s*(\d+)\s*<', body)
        version = cells.get(5, "")
        entries.append(CatalogEntry(uid, cells.get(1, ""), cells.get(2, ""), cells.get(3, ""), cells.get(4, ""),
                                    "" if version.lower() == "n/a" else version,
                                    int(size.group(1)) if size else 0))
    return entries


def _span(page: str, name: str) -> str:
    m = re.search(rf'<span id="ScopedViewHandler_{name}"[^>]*>(.*?)</span>', page, re.S)
    return _text(m.group(1)) if m else ""


def _div_value(page: str, div_id: str) -> str:
    """Contenu d'un bloc « Libellé : valeur » de la fiche, sans le libellé."""
    m = re.search(rf'<div id="{div_id}"[^>]*>(.*?)</div>', page, re.S)
    if not m:
        return ""
    body = re.sub(r'<span[^>]*class="labelTitle"[^>]*>.*?</span>', " ", m.group(1), flags=re.S)
    return _text(body)


def parse_details(page: str, update_id: str) -> CatalogDriver:
    hwids: list[str] = []
    start = page.find('id="driverhwIDs"')
    if start >= 0:
        end = page.find('id="driverupdateIDs"', start)
        block = page[start:end if end > 0 else len(page)]
        hwids = [_text(h) for h in re.findall(r'<div style="padding-bottom:[^"]*">(.*?)</div>', block, re.S)]
    size = 0
    raw_size = re.search(r'id="ScopedViewHandler_size"[^>]*>\s*([\d.,]+)\s*(KB|MB|GB)', page)
    if raw_size:
        factor = {"KB": 1024, "MB": 1024 ** 2, "GB": 1024 ** 3}[raw_size.group(2)]
        size = int(float(raw_size.group(1).replace(",", "")) * factor)
    return CatalogDriver(
        update_id=update_id.lower(), title=_span(page, "titleText"), version=_span(page, "version"),
        date=_span(page, "versionDate"), architecture=_div_value(page, "archDiv"),
        products=_div_value(page, "productsDiv"), hardware_ids=[h for h in hwids if h],
        manufacturer=_span(page, "manufacturer"), provider=_span(page, "driverProvider"),
        model=_span(page, "driverModel"), driver_class=_span(page, "driverClass"), size=size,
        company=_span(page, "company"))


def parse_download_dialog(page: str) -> list[tuple[str, str]]:
    """[(adresse du fichier, empreinte SHA-256 en base64)] du dialogue de téléchargement."""
    urls = dict(re.findall(r"files\[(\d+)\]\.url\s*=\s*'([^']+)'", page))
    sums = dict(re.findall(r"files\[(\d+)\]\.sha256\s*=\s*'([^']*)'", page))
    return [(urls[i], sums.get(i, "")) for i in sorted(urls, key=int)]


# --------------------------------------------------------------------------- règles

def search_terms(dev: DeviceRef) -> list[str]:
    """Termes de recherche : l'identifiant exact du matériel (avec le SUBSYS du fabricant du PC), puis la
    puce seule (sans SUBSYS / REV / MI).

    Le catalogue cherche par début d'identifiant : « PCI\\VEN_10EC&DEV_2600 » trouve un pilote déclarant
    « pci\\ven_10ec&dev_2600&rev_21 », alors qu'une recherche avec le SUBSYS ne le trouverait pas. À l'inverse,
    pour une puce très répandue (Intel UHD), la recherche sans SUBSYS ne montre que les pilotes d'autres
    marques de PC : la recherche avec le SUBSYS trouve ceux prévus pour ce PC.
    """
    hwids = [h for h in (*dev.hardware_ids, dev.hardware_id) if h and not h.startswith("*") and "\\" in h]
    terms: list[str] = []
    if hwids:
        terms.append(re.sub(r"&REV_[0-9A-Z]+", "", hwids[0], flags=re.I))
    for hwid in hwids:
        terms.append(re.split(r"&(?:SUBSYS|REV|MI|CC|COL|PROT|CLASS)_", hwid, maxsplit=1, flags=re.I)[0])
    unique: list[str] = []
    for term in terms:
        if term.upper() not in (t.upper() for t in unique):
            unique.append(term)
    return unique[:MAX_SEARCH_TERMS]


_FAMILY_ALIASES = {"rivet": "killer", "hewlett": "hp", "hewlett-packard": "hp", "advanced": "amd", "ati": "amd"}


def family(name: str) -> str:
    """« Intel(R) Corporation » -> « intel », « Rivet Networks » -> « killer » (même fabricant)."""
    text = re.sub(r"\(r\)|\(tm\)|[®™]", " ", (name or "").lower())
    words = [w for w in re.split(r"[^a-z0-9-]+", text) if w]
    return _FAMILY_ALIASES.get(words[0], words[0]) if words else ""


def same_maker(drv: CatalogDriver, dev: DeviceRef) -> bool:
    """Le pilote vient-il du fabricant de la puce ou de l'éditeur du pilote installé ?

    Écarte par exemple le pilote « ELAN SMBus », qui déclare le contrôleur SMBus d'Intel mais ne concerne que
    les pavés tactiles ELAN de certains PC.
    """
    mine = {family(dev.provider), family(dev.vendor_name)} - {"", "inconnu"}
    theirs = {family(drv.provider), family(drv.manufacturer), family(drv.company)} - {""}
    return not mine or not theirs or bool(mine & theirs)


def is_coupled(dev: DeviceRef) -> bool:
    """Composants Intel Smart Sound : ils forment un ensemble avec le pilote audio du PC."""
    hwid = (dev.hardware_id or dev.device_id).upper()
    return "smart sound" in dev.name.lower() or hwid.startswith(("INTELAUDIO\\CTLR", "INTELAUDIO\\DSP_CTLR"))


def device_ids(dev: DeviceRef) -> list[str]:
    ids = list(dev.hardware_ids) + list(dev.compatible_ids)
    return [i.lower() for i in ids] if ids else ([dev.hardware_id.lower()] if dev.hardware_id else [])


def match_index(drv: CatalogDriver, dev: DeviceRef) -> int | None:
    """Position (0 = la plus précise) du premier identifiant du périphérique déclaré par le pilote."""
    declared = {h.lower() for h in drv.hardware_ids}
    for i, hwid in enumerate(device_ids(dev)):
        if hwid in declared:
            return i
    return None


def installed_index(dev: DeviceRef) -> int:
    """Précision du pilote installé. Inconnue : on la suppose maximale (aucune fausse mise à jour)."""
    ids = device_ids(dev)
    matching = dev.matching_id.lower()
    return ids.index(matching) if matching in ids else 0


def supports_windows(products: str, build: int) -> bool:
    """Le pilote est-il prévu pour cette version de Windows (client) ? build 0 = inconnue."""
    minimums: list[int] = []
    for release in re.findall(r"Windows (?:11 Client|- Client)(?: S)?,\s*version (\w+) and later", products, re.I):
        minimums.append(WIN11_RELEASES.get(release.upper(), UNKNOWN_RELEASE))
    for release in re.findall(r"Windows 10,?\s*version (\w+) and later", products, re.I):
        minimums.append(WIN10_RELEASES.get(release.upper(), UNKNOWN_RELEASE))
    if re.search(r"Windows 10,\s*Vibranium and later", products, re.I):
        minimums.append(19041)
    if re.search(r"Windows 10 and later", products, re.I):
        minimums.append(10240)
    if not minimums:
        return False          # Windows Server, Windows 7, Surface Hub…
    return build == 0 or min(minimums) <= build


def machine_architecture() -> str:
    machine = platform.machine().upper()
    return {"X86_64": "AMD64", "AARCH64": "ARM64"}.get(machine, machine)


def supports_architecture(text: str, machine: str) -> bool:
    words = set(re.split(r"[\s,]+", (text or "").upper())) - {""}
    if not words or words & {"N/A", "ALL"}:
        return True
    return machine in words


def rank_key(date: datetime | None, version: str) -> tuple:
    return (date or datetime.min, version_parts(version) or ())


def is_newer(drv: CatalogDriver, dev: DeviceRef) -> bool:
    """Comme Windows : date du pilote d'abord, puis numéro de version. Même version : jamais plus récent."""
    if compare_versions(drv.version, dev.version) == 0 or drv.version.strip() == dev.version.strip():
        return False
    if dev.date is None or drv.driver_date is None:
        return compare_versions(drv.version, dev.version) == 1
    return rank_key(drv.driver_date, drv.version) > rank_key(dev.date, dev.version)


def evaluate(dev: DeviceRef, candidates: list[Candidate], checked_at: datetime,
             not_applied: dict[str, dict] | None = None) -> VendorCheck | None:
    """Conclusion pour un périphérique à partir des pilotes du catalogue qui le prennent en charge."""
    if not candidates:
        return None
    not_applied = not_applied or {}
    current = installed_index(dev)
    preferred = [c for c in candidates if c.index <= current]
    generic = [c for c in candidates if c.index > current]

    def best(items: list[Candidate]) -> CatalogDriver | None:
        if not items:
            return None
        return max(items, key=lambda c: rank_key(c.driver.driver_date, c.driver.version)).driver

    def check(drv: CatalogDriver, **kw) -> VendorCheck:
        return VendorCheck(source=SOURCE_LABEL, latest_version=drv.version, checked_at=checked_at,
                           title=drv.title, release_date=drv.date, page_url=drv.page_url, **kw)

    update = best([c for c in preferred if is_newer(c.driver, dev)])
    if update:
        done = not_applied.get(update.update_id)
        if done:
            note = (f"La version {update.version} du catalogue a déjà été installée par l'application"
                    f"{' le ' + done['when'][:10] if done.get('when') else ''}, mais Windows a conservé le pilote "
                    f"actuel ({dev.version}). Un redémarrage peut être nécessaire pour terminer l'installation.")
            return check(update, update_available=False, comparable=False, note=note)
        if is_coupled(dev):
            note = (f"Le catalogue contient la version {update.version}, mais les composants Intel Smart Sound "
                    "forment un ensemble avec le pilote audio : les mettre à jour un par un peut couper le son. "
                    "L'application ne l'installe donc pas : passez par le paquet audio complet du fabricant de "
                    "votre PC.")
            return check(update, update_available=False, comparable=False, note=note)
        return check(update, update_available=True, extra=update)

    newer_generic = best([c for c in generic if is_newer(c.driver, dev)])
    reference = best(preferred) or best(generic)
    if newer_generic:
        maker = newer_generic.manufacturer or newer_generic.provider or "le fabricant de la puce"
        note = (f"Le catalogue contient une version générique plus récente ({newer_generic.version}, publiée par "
                f"{maker}). Windows ne l'installe pas d'office : votre pilote actuel est prévu pour un "
                "identifiant plus précis de votre matériel (souvent la version adaptée par le fabricant du PC). "
                "Pour passer quand même à la version générique, utilisez l'outil officiel du fabricant de la puce "
                "(bouton « Télécharger ») : vous perdriez les éventuels réglages propres à votre PC.")
        return check(newer_generic, update_available=False, comparable=True, generic_newer=True, note=note)
    notes = []
    if compare_versions(dev.version, reference.version) == 1:
        notes.append(f"Votre version ({dev.version}) est même plus récente que celle du catalogue.")
    matched = [c for c in preferred if c.driver is reference]
    if matched and "subsys_" in device_ids(dev)[matched[0].index]:
        notes.append("Cette version est publiée pour l'identifiant exact de votre matériel ; le fabricant de la "
                     "puce publie parfois une version générique plus récente sur son site (bouton « Télécharger »).")
    return check(reference, update_available=False, comparable=True, note=" ".join(notes))


# --------------------------------------------------------------------------- réseau et cache

def _cache_path(kind: str, key: str) -> Path:
    folder = cache_dir() / "mscatalog"
    folder.mkdir(parents=True, exist_ok=True)
    # CACHE_VERSION change quand la lecture des pages évolue : les anciennes fiches sont alors ignorées.
    digest = hashlib.sha1(f"{CACHE_VERSION}:{key.lower()}".encode("utf-8")).hexdigest()[:20]
    return folder / f"{kind}_{digest}.json"


def _cache_read(path: Path, max_age: float):
    try:
        if time.time() - path.stat().st_mtime < max_age:
            return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    return None


def _cache_write(path: Path, data) -> None:
    try:
        tmp = path.with_suffix(".part")
        tmp.write_text(json.dumps(data), encoding="utf-8")
        tmp.replace(path)
    except OSError:
        log.debug("Cache du catalogue non écrit : %s", path, exc_info=True)


def _fetch(url: str, data: dict | None = None, timeout: float = 30) -> str:
    if not is_allowed_url(url, CATALOG_HOSTS):
        raise ValueError(f"Adresse refusée : {url}")
    body = urllib.parse.urlencode(data).encode("ascii") if data is not None else None
    request = urllib.request.Request(url, data=body, headers={"User-Agent": USER_AGENT,
                                                              "Accept-Language": "en-US,en;q=0.5"})
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - domaine vérifié
        if not is_allowed_url(response.geturl(), CATALOG_HOSTS):
            raise ValueError(f"Redirection refusée : {response.geturl()}")
        return response.read().decode("utf-8", errors="replace")


def search(term: str) -> list[CatalogEntry]:
    path = _cache_path("search", term)
    cached = _cache_read(path, SEARCH_MAX_AGE)
    if cached is not None:
        return [CatalogEntry(**e) for e in cached]
    entries = parse_search(_fetch(SEARCH_URL.format(urllib.parse.quote(term, safe=""))))
    _cache_write(path, [asdict(e) for e in entries])
    return entries


def details(update_id: str) -> CatalogDriver:
    path = _cache_path("details", update_id)
    cached = _cache_read(path, DETAILS_MAX_AGE)
    if cached is not None:
        return CatalogDriver(**cached)
    drv = parse_details(_fetch(DETAILS_URL.format(update_id)), update_id)
    if drv.version or drv.hardware_ids:      # une page vide (erreur du site) n'est pas mise en cache
        _cache_write(path, asdict(drv))
    return drv


def download_files(update_id: str) -> list[tuple[str, str]]:
    """Adresses officielles (HTTPS, windowsupdate.com) des fichiers d'un pilote du catalogue."""
    payload = json.dumps([{"size": 0, "languages": "", "uidInfo": update_id, "updateID": update_id}])
    files = parse_download_dialog(_fetch(DOWNLOAD_DIALOG_URL, {"updateIDs": payload}))
    out = []
    for url, digest in files:
        if url.startswith("http://"):
            url = "https://" + url[len("http://"):]
        if is_allowed_url(url, DOWNLOAD_HOSTS):
            out.append((url, digest))
    return out


# --------------------------------------------------------------------------- pilotes déjà installés

def _installed_path() -> Path:
    return cache_dir() / "mscatalog_installed.json"


def installed_before() -> dict[str, dict]:
    """Pilotes du catalogue que l'application a installés mais que Windows n'a pas retenus."""
    try:
        return json.loads(_installed_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def remember_not_applied(drv: CatalogDriver) -> None:
    data = installed_before()
    data[drv.update_id] = {"version": drv.version, "when": datetime.now().isoformat(timespec="seconds")}
    _cache_write(_installed_path(), data)


# --------------------------------------------------------------------------- recherche pour un périphérique

class _Memo:
    """Évite de refaire la même requête quand plusieurs périphériques partagent une puce."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._values: dict[str, object] = {}
        self._locks: dict[str, threading.Lock] = {}

    def get(self, key: str, compute: Callable[[], object]):
        with self._lock:
            lock = self._locks.setdefault(key, threading.Lock())
        with lock:
            if key not in self._values:
                self._values[key] = compute()
            return self._values[key]


def find_candidates(dev: DeviceRef, build: int, machine: str, memo: _Memo | None = None,
                    search_fn=search, details_fn=details) -> list[Candidate]:
    """Pilotes du catalogue prévus pour ce périphérique, cette version de Windows et ce processeur."""
    memo = memo or _Memo()
    # Deux passes : d'abord les résultats de la recherche la plus précise (pilotes prévus pour ce modèle de
    # PC), puis ceux de la recherche par puce (souvent dominés par les pilotes d'autres marques de PC).
    passes: list[list[CatalogEntry]] = []
    seen: set[str] = set()
    for i, term in enumerate(search_terms(dev)):
        if i < 2:
            passes.append([])
        for entry in memo.get("s:" + term.lower(), lambda t=term: search_fn(t)):
            if entry.update_id not in seen and supports_windows(entry.products, build):
                seen.add(entry.update_id)
                passes[-1].append(entry)

    found: list[Candidate] = []
    current = installed_index(dev)
    fetched = 0
    best_preferred: CatalogDriver | None = None
    for entries in passes:
        # Une même version est publiée plusieurs fois (une par version de Windows ou par marque de PC).
        groups: dict[str, list[CatalogEntry]] = {}
        for e in entries:
            groups.setdefault(e.title.lower(), []).append(e)
        ordered = sorted(groups.values(), key=lambda g: (version_parts(entry_version(g[0])) or (), us_date(
            g[0].last_updated) or datetime.min), reverse=True)
        for group in ordered:
            if best_preferred and compare_versions(entry_version(group[0]), best_preferred.version) in (0, -1):
                break         # rien de plus récent que ce qui a déjà été trouvé
            for entry in group:
                if fetched >= MAX_DETAILS_PER_DEVICE:
                    return found
                fetched += 1
                drv = memo.get("d:" + entry.update_id, lambda u=entry.update_id: details_fn(u))
                if entry.size:
                    drv.size = entry.size      # taille exacte (la fiche l'arrondit)
                if not (supports_architecture(drv.architecture, machine)
                        and supports_windows(drv.products, build)):
                    continue      # autre variante (ARM64, autre Windows) : on essaie la suivante du groupe
                index = match_index(drv, dev)
                if index is None or not same_maker(drv, dev):
                    continue      # même version publiée pour d'autres modèles de PC : on essaie la suivante
                found.append(Candidate(drv, index))
                break
            # Les groupes sont triés du plus récent au plus ancien : le premier pilote que Windows préférerait
            # à l'actuel est la meilleure mise à jour possible de cette passe.
            if found and found[-1].index <= current:
                best_preferred = found[-1].driver
                break
    return found


# --------------------------------------------------------------------------- source

class MicrosoftCatalogSource(DriverSource):
    name = "Microsoft Update"   # « Catalogue Microsoft Update indisponible… » en cas de panne
    has_public_api = True
    allowed_hosts = CATALOG_HOSTS

    def applies_to_device(self, dev: DeviceRef) -> bool:
        if dev.is_microsoft:          # pilote fourni avec Windows : suivi par Windows Update
            return False
        if dev.vendor_name == "NVIDIA" and dev.device_class.upper() == "DISPLAY":
            return False              # vérifié directement sur nvidia.com
        return bool(search_terms(dev))

    def check(self, ctx: SystemContext, devices: list[DeviceRef], run: SourceRun, progress=None) -> None:
        targets = [d for d in devices if self.applies_to_device(d)]
        if not targets:
            return
        machine = machine_architecture()
        memo = _Memo()
        previous = installed_before()
        done = [0]
        lock = threading.Lock()
        failures: list[BaseException] = []

        def one(dev: DeviceRef):
            try:
                return dev, find_candidates(dev, ctx.windows_build, machine, memo)
            except Exception as exc:     # une page illisible ne doit pas bloquer les autres
                log.warning("Catalogue Microsoft : %s non vérifié (%s)", dev.name, exc)
                failures.append(exc)
                return dev, None
            finally:
                with lock:
                    done[0] += 1
                    if progress:
                        progress(f"Catalogue Microsoft Update : {done[0]}/{len(targets)} périphérique(s) vérifié(s)…")

        if progress:
            progress(f"Catalogue Microsoft Update : vérification de {len(targets)} périphérique(s)…")
        with ThreadPoolExecutor(max_workers=WORKERS, thread_name_prefix="mscatalog") as pool:
            results = list(pool.map(one, targets))
        if failures and len(failures) == len(targets):
            raise failures[0]
        now = datetime.now()
        for dev, candidates in results:
            if candidates is None:
                continue
            check = evaluate(dev, candidates, now, previous)
            if check:
                run.add(dev.key, check)
        if failures:
            run.notes.append(f"Catalogue Microsoft Update : {len(failures)} périphérique(s) n'ont pas pu être "
                             "vérifiés (connexion Internet ?).")
        run.verified_by.append(self.name)

    def note_for(self, ctx: SystemContext, dev: DeviceRef, consulted: list[str]) -> str:
        if self.name in consulted and self.applies_to_device(dev):
            return ("Le Catalogue Microsoft Update ne contient aucun pilote déclarant ce périphérique pour votre "
                    "version de Windows.")
        return ""

    def links(self, ctx: SystemContext, dev: DeviceRef, checks: list[VendorCheck]) -> list[DownloadLink]:
        out = [DownloadLink(f"Fiche du pilote {c.latest_version} (Catalogue Microsoft)", c.page_url, EXACT)
               for c in checks if c.source == SOURCE_LABEL and c.page_url and c.comparable]
        return [link for link in out if self.safe(link)]


# --------------------------------------------------------------------------- installation

class CatalogInstallError(Exception):
    pass


def download_and_extract(drv: CatalogDriver, progress=None, cancel=None, phase=None) -> tuple[Path, Path]:
    """Télécharge le pilote (HTTPS, windowsupdate.com), vérifie son empreinte et sa signature Microsoft,
    puis l'extrait. Renvoie (fichier .cab, dossier extrait)."""
    from app.utils.download import DownloadCancelled, download_file, verify_signature
    from app.utils.system import RC_CANCELLED, run_cancellable

    files = download_files(drv.update_id)
    if not files:
        raise CatalogInstallError("Le Catalogue Microsoft ne fournit aucun fichier pour ce pilote.")
    url, digest = files[0]
    path = download_file(url, DOWNLOAD_HOSTS, progress, cancel)
    try:
        if phase:
            phase("vérification de l'empreinte et de la signature Microsoft…")
        if digest:
            from app.core.packages import file_hash     # lecture par blocs : les .cab font parfois 700 Mo

            actual = base64.b64encode(bytes.fromhex(file_hash(path, "sha256"))).decode("ascii")
            if actual != digest:
                raise CatalogInstallError("Empreinte du fichier différente de celle annoncée par Microsoft : "
                                          "installation annulée par sécurité.")
        ok, detail = verify_signature(path, "O=Microsoft Corporation")
        if not ok:
            raise CatalogInstallError(f"Signature Microsoft invalide ({detail}) : installation annulée par sécurité.")
        if phase:
            phase("extraction…")
        folder = Path(tempfile.mkdtemp(prefix="pilote_", dir=path.parent))
        expand = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "expand.exe"
        result = run_cancellable([str(expand), str(path), "-F:*", str(folder)], timeout=1800, cancel=cancel)
        if result.returncode == RC_CANCELLED:
            shutil.rmtree(folder, ignore_errors=True)
            raise DownloadCancelled()
        if not result.ok or not any(folder.rglob("*.inf")):
            shutil.rmtree(folder, ignore_errors=True)
            raise CatalogInstallError("Extraction du pilote impossible.")
        return path, folder
    except Exception:
        path.unlink(missing_ok=True)
        raise


@dataclass
class CatalogInstallOutcome:
    title: str
    success: bool            # le périphérique utilise maintenant ce pilote (ou l'utilisera au redémarrage)
    message: str
    reboot_required: bool = False


_RESTART_HINT = re.compile(r"red.marrage|reboot|restart", re.IGNORECASE)
APPLY_CHECKS = 6            # relectures de la version après pnputil…
APPLY_CHECK_DELAY = 2.0     # …espacées de 2 s


def install(drv: CatalogDriver, device_ids: list[str], progress=None, cancel=None,
            phase=None, title: str = "") -> CatalogInstallOutcome:
    """Installe un pilote du catalogue (droits administrateur requis) puis vérifie qu'il est bien utilisé.

    Windows reste seul juge : si le pilote n'est pas meilleur que l'actuel pour ce périphérique, il est
    seulement ajouté au magasin de pilotes. On le note alors pour ne pas le reproposer à chaque analyse.

    `cancel` (threading.Event) interrompt le téléchargement (DownloadCancelled) ou l'installation en cours.
    """
    from app.core import drivers
    from app.utils.system import RC_CANCELLED

    title = title or f"{drv.title} — {drv.version}"
    cab, folder = download_and_extract(drv, progress, cancel, phase)
    try:
        if phase:
            phase("installation (Windows choisit le meilleur pilote, cela peut prendre quelques minutes)…")
        result = drivers.install_inf_folder(str(folder), cancel)
    finally:
        shutil.rmtree(folder, ignore_errors=True)
        cab.unlink(missing_ok=True)
    if result.returncode == RC_CANCELLED:
        log.warning("pnputil (%s) : installation interrompue par l'utilisateur", drv.title)
        return CatalogInstallOutcome(title, False, "Passé : installation interrompue (redémarrez le PC si le "
                                                   "périphérique ne fonctionne plus correctement).")
    log.info("pnputil (%s) : code %s\n%s", drv.title, result.returncode, result.output[-2000:])
    # pnputil ne renvoie pas toujours 3010 : avec plusieurs paquets dont un sans périphérique, il renvoie 259 tout
    # en annonçant le redémarrage nécessaire (message traduit, et parfois mal décodé : « redÚmarrage »).
    reboot = result.returncode == 3010 or bool(_RESTART_HINT.search(result.output or ""))
    applied = False
    for attempt in range(APPLY_CHECKS):
        try:
            now = drivers.driver_version_of(device_ids)
        except Exception:
            log.warning("Version installée illisible après l'installation", exc_info=True)
            now = {}
        applied = any(v.strip() == drv.version.strip() or compare_versions(v, drv.version) in (0, 1)
                      for v, _date in now.values())
        if applied or attempt == APPLY_CHECKS - 1:
            break
        time.sleep(APPLY_CHECK_DELAY)       # Windows met parfois quelques secondes à associer le nouveau pilote
    if applied:
        return CatalogInstallOutcome(title, True, "Installé" + (" — redémarrage nécessaire" if reboot else ""),
                                     reboot)
    if reboot:
        return CatalogInstallOutcome(title, True, "Installé — redémarrez le PC pour l'activer", True)
    if result.returncode in (0, 259):   # 259 : aucun périphérique mis à jour
        remember_not_applied(drv)
        return CatalogInstallOutcome(title, False, "Ajouté à Windows, mais Windows a conservé le pilote actuel "
                                                   "pour ce périphérique.")
    detail = (result.output.strip().splitlines() or [f"code {result.returncode}"])[-1]
    return CatalogInstallOutcome(title, False, f"Échec de l'installation ({detail[:200]})")
