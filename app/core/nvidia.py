"""Cartes graphiques NVIDIA : mesures en direct (nvidia-smi) et dernier pilote sur nvidia.com.

nvidia-smi est installé avec le pilote NVIDIA. Il donne la température, la vitesse du
ventilateur, l'utilisation et la mémoire de la carte graphique.

La vérification en ligne utilise le service public de nvidia.com qui alimente leur page
« Pilotes » : on y envoie seulement le modèle de carte, jamais d'information personnelle.
"""

from __future__ import annotations

import json
import logging
import shutil
import threading
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from urllib.parse import unquote, urlencode

from app.utils.system import run_command

log = logging.getLogger(__name__)

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) NPone29Cleaner"
LOOKUP_URL = "https://www.nvidia.com/Download/API/lookupValueSearch.aspx?TypeID=3"
DRIVER_URL = "https://gfwsl.geforce.com/services_toolkit/services/com/nvidia/services/AjaxDriverService.php"
DRIVERS_PAGE = "https://www.nvidia.com/fr-fr/drivers/"
ALLOWED_DOWNLOAD_HOSTS = (".nvidia.com",)

_lookup_cache: dict[str, tuple[str, str]] | None = None
_lock = threading.Lock()


@dataclass
class NvidiaLive:
    name: str
    driver_version: str
    temperature: float | None
    fan_percent: float | None
    utilization: float | None
    memory_total_mb: float | None
    memory_used_mb: float | None
    power_watts: float | None


@dataclass
class NvidiaDriver:
    version: str
    release_date: str
    download_url: str
    details_url: str
    size_text: str
    name: str

    def package(self):
        """L'installateur, vu comme un paquet officiel téléchargeable et vérifiable (app/core/packages.py)."""
        from app.core.packages import VendorPackage

        return VendorPackage(
            vendor="NVIDIA", title=f"Carte graphique NVIDIA {self.version}", version=self.version,
            download_url=self.download_url, allowed_hosts=ALLOWED_DOWNLOAD_HOSTS, publisher="NVIDIA Corporation",
            size_label=self.size_text, page_url=self.details_url,
            advice="Installateur NVIDIA : choisissez « Installation express ». L'écran peut clignoter quelques "
                   "secondes.")


def _num(text: str) -> float | None:
    try:
        return float(text.strip())
    except (ValueError, AttributeError):
        return None


def query_smi() -> list[NvidiaLive]:
    """Mesures instantanées de chaque carte NVIDIA (liste vide si pas de carte / pas de pilote)."""
    exe = shutil.which("nvidia-smi")
    if not exe:
        return []
    fields = "name,driver_version,temperature.gpu,fan.speed,utilization.gpu,memory.total,memory.used,power.draw"
    result = run_command([exe, f"--query-gpu={fields}", "--format=csv,noheader,nounits"], timeout=10)
    if not result.ok:
        return []
    return parse_smi_output(result.stdout)


def parse_smi_output(text: str) -> list[NvidiaLive]:
    gpus = []
    for line in text.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 8:
            continue
        gpus.append(NvidiaLive(parts[0], parts[1], _num(parts[2]), _num(parts[3]), _num(parts[4]),
                               _num(parts[5]), _num(parts[6]), _num(parts[7])))
    return gpus


def windows_to_nvidia_version(windows_version: str) -> str:
    """Version Windows du pilote -> version NVIDIA : '32.0.15.6214' -> '621.14'."""
    parts = (windows_version or "").split(".")
    if len(parts) < 4:
        return windows_version or ""
    digits = (parts[2] + parts[3])[-5:]
    if len(digits) < 5 or not digits.isdigit():
        return windows_version
    return f"{digits[:3]}.{digits[3:]}"


def version_tuple(version: str) -> tuple[int, ...]:
    out = []
    for part in (version or "").split("."):
        try:
            out.append(int(part))
        except ValueError:
            out.append(0)
    return tuple(out)


def is_newer(candidate: str, installed: str) -> bool:
    return version_tuple(candidate) > version_tuple(installed)


def _normalize(name: str) -> str:
    text = " ".join(name.lower().replace("(r)", "").replace("(tm)", "").split())
    return text.removeprefix("nvidia ").strip()


def parse_lookup(xml_text: str) -> dict[str, tuple[str, str]]:
    """XML de nvidia.com -> {nom normalisé: (psid, pfid)}."""
    products: dict[str, tuple[str, str]] = {}
    root = ET.fromstring(xml_text)
    for item in root.iter("LookupValue"):
        name, value, parent = item.findtext("Name"), item.findtext("Value"), item.get("ParentID")
        if name and value and parent:
            products.setdefault(_normalize(name), (parent, value))
    return products


def _http_get(url: str, timeout: float = 20) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - URL fixe https
        return response.read().decode("utf-8", errors="replace")


def find_product(gpu_name: str) -> tuple[str, str] | None:
    """(psid, pfid) de la carte, d'après la liste officielle des produits NVIDIA."""
    global _lookup_cache
    with _lock:
        if _lookup_cache is None:
            _lookup_cache = parse_lookup(_http_get(LOOKUP_URL))
        return _lookup_cache.get(_normalize(gpu_name))


def latest_driver(gpu_name: str) -> NvidiaDriver | None:
    """Dernier pilote Game Ready WHQL (DCH) publié pour cette carte, ou None si introuvable."""
    product = find_product(gpu_name)
    if product is None:
        log.info("Carte NVIDIA inconnue du catalogue : %s", gpu_name)
        return None
    psid, pfid = product
    params = {
        "func": "DriverManualLookup", "psid": psid, "pfid": pfid, "osID": "57", "languageCode": "1036",
        "beta": "0", "isWHQL": "1", "dltype": "-1", "dch": "1", "upCRD": "0", "qnf": "0", "sort1": "0",
        "numberOfResults": "1",
    }
    data = json.loads(_http_get(f"{DRIVER_URL}?{urlencode(params)}"))
    return parse_driver_response(data)


def parse_driver_response(data: dict) -> NvidiaDriver | None:
    try:
        info = data["IDS"][0]["downloadInfo"]
    except (KeyError, IndexError, TypeError):
        return None
    return NvidiaDriver(
        version=str(info.get("Version", "")),
        release_date=str(info.get("ReleaseDateTime", "")),
        download_url=str(info.get("DownloadURL", "")),
        details_url=str(info.get("DetailsURL", "")) or DRIVERS_PAGE,
        size_text=str(info.get("DownloadURLFileSize", "")),
        name=unquote(str(info.get("Name", "Pilote NVIDIA"))),
    )
