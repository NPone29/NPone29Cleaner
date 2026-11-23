"""Identification des fabricants de matériel et liens vers leurs pilotes officiels.

Chaque périphérique PCI/USB/audio porte un identifiant fabricant (VEN_xxxx ou VID_xxxx),
attribué officiellement par le PCI-SIG / l'USB-IF. On s'en sert pour savoir qui a fabriqué
la puce et où chercher son pilote.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import quote, quote_plus


@dataclass(frozen=True)
class Vendor:
    name: str
    domain: str = ""         # pour la recherche limitée au site officiel
    support_url: str = ""    # page de téléchargement des pilotes
    via_oem: bool = False    # pilotes généralement fournis par le fabricant de la carte mère / du PC


INTEL = Vendor("Intel", "intel.com", "https://www.intel.fr/content/www/fr/fr/support/detect.html")
NVIDIA = Vendor("NVIDIA", "nvidia.com", "https://www.nvidia.com/fr-fr/drivers/")
AMD = Vendor("AMD", "amd.com", "https://www.amd.com/fr/support/download/drivers.html")
REALTEK = Vendor("Realtek", "realtek.com", "https://www.realtek.com/Download/Index", via_oem=True)

# Identifiants PCI (VEN_) et USB (VID_) les plus courants.
VENDORS: dict[str, Vendor] = {
    "8086": INTEL, "8087": INTEL,
    "10DE": NVIDIA, "0955": NVIDIA,
    "1002": AMD, "1022": AMD,
    "10EC": REALTEK, "0BDA": REALTEK,
    "14E4": Vendor("Broadcom", "broadcom.com", via_oem=True),
    "0A5C": Vendor("Broadcom", "broadcom.com", via_oem=True),
    "168C": Vendor("Qualcomm Atheros", "qualcomm.com", via_oem=True),
    "17CB": Vendor("Qualcomm", "qualcomm.com", via_oem=True),
    "0CF3": Vendor("Qualcomm Atheros", "qualcomm.com", via_oem=True),
    "14C3": Vendor("MediaTek", "mediatek.com", via_oem=True),
    "0E8D": Vendor("MediaTek", "mediatek.com", via_oem=True),
    "1B21": Vendor("ASMedia", "asmedia.com.tw", via_oem=True),
    "174C": Vendor("ASMedia", "asmedia.com.tw", via_oem=True),
    "1B4B": Vendor("Marvell", "marvell.com", via_oem=True),
    "197B": Vendor("JMicron", "jmicron.com", via_oem=True),
    "152D": Vendor("JMicron", "jmicron.com", via_oem=True),
    "1106": Vendor("VIA", "via.com.tw", via_oem=True),
    "2109": Vendor("VIA Labs", "via-labs.com", via_oem=True),
    "1912": Vendor("Renesas", "renesas.com", via_oem=True),
    "104C": Vendor("Texas Instruments", "ti.com", via_oem=True),
    "1217": Vendor("O2 Micro", "o2micro.com", via_oem=True),
    "13D3": Vendor("AzureWave", "azurewave.com", via_oem=True),
    "144D": Vendor("Samsung", "samsung.com",
                   "https://semiconductor.samsung.com/consumer-storage/support/tools/"),
    "04E8": Vendor("Samsung", "samsung.com", "https://www.samsung.com/fr/support/"),
    "15B7": Vendor("Western Digital / SanDisk", "westerndigital.com", "https://www.westerndigital.com/fr-fr/support"),
    "0781": Vendor("SanDisk", "westerndigital.com", "https://www.westerndigital.com/fr-fr/support"),
    "1C5C": Vendor("SK hynix", "skhynix.com", "https://ssd.skhynix.com/"),
    "1987": Vendor("Phison", "phison.com", via_oem=True),
    "126F": Vendor("Silicon Motion", "siliconmotion.com", via_oem=True),
    "2646": Vendor("Kingston", "kingston.com", "https://www.kingston.com/fr/support/technical/downloads"),
    "0951": Vendor("Kingston", "kingston.com", "https://www.kingston.com/fr/support/technical/downloads"),
    "c0a9": Vendor("Crucial", "crucial.fr", "https://www.crucial.fr/support/ssd-support"),
    "1344": Vendor("Micron / Crucial", "crucial.fr", "https://www.crucial.fr/support/ssd-support"),
    "046D": Vendor("Logitech", "logitech.com", "https://support.logi.com/hc/fr"),
    "1532": Vendor("Razer", "razer.com", "https://www.razer.com/fr-fr/synapse-4"),
    "1B1C": Vendor("Corsair", "corsair.com", "https://www.corsair.com/fr/fr/s/downloads"),
    "1038": Vendor("SteelSeries", "steelseries.com", "https://steelseries.com/gg"),
    "1E7D": Vendor("ROCCAT / Turtle Beach", "turtlebeach.com", "https://www.turtlebeach.com/pages/support"),
    "0B05": Vendor("ASUS", "asus.com", "https://www.asus.com/fr/support/download-center/"),
    "1043": Vendor("ASUS", "asus.com", "https://www.asus.com/fr/support/download-center/"),
    "0DB0": Vendor("MSI", "msi.com", "https://fr.msi.com/support/download"),
    "1462": Vendor("MSI", "msi.com", "https://fr.msi.com/support/download"),
    "1458": Vendor("Gigabyte", "gigabyte.com", "https://www.gigabyte.com/fr/Support"),
    "1849": Vendor("ASRock", "asrock.com", "https://www.asrock.com/support/index.fr.asp"),
    "045E": Vendor("Microsoft", "microsoft.com", "https://support.microsoft.com/fr-fr/windows"),
    "054C": Vendor("Sony", "sony.com", "https://www.sony.fr/electronics/support"),
    "057E": Vendor("Nintendo", "nintendo.com"),
    "05AC": Vendor("Apple", "apple.com", "https://support.apple.com/fr-fr"),
    "04F2": Vendor("Chicony", "chicony.com.tw", via_oem=True),
    "0C45": Vendor("Microdia (webcam)", "", via_oem=True),
    "5986": Vendor("Bison (webcam)", "", via_oem=True),
    "1A86": Vendor("WCH (CH340)", "wch-ic.com", "https://www.wch-ic.com/downloads/CH341SER_EXE.html"),
    "10C4": Vendor("Silicon Labs", "silabs.com",
                   "https://www.silabs.com/developer-tools/usb-to-uart-bridge-vcp-drivers"),
    "0403": Vendor("FTDI", "ftdichip.com", "https://ftdichip.com/drivers/vcp-drivers/"),
    "067B": Vendor("Prolific", "prolific.com.tw", "https://www.prolific.com.tw/US/ShowProduct.aspx?p_id=225&pcid=41"),
    "2341": Vendor("Arduino", "arduino.cc", "https://www.arduino.cc/en/software"),
    "28DE": Vendor("Valve (Steam)", "steampowered.com"),
    "0FD9": Vendor("Elgato", "elgato.com", "https://www.elgato.com/fr/fr/s/downloads"),
    "1395": Vendor("EPOS / Sennheiser", "eposaudio.com", "https://www.eposaudio.com/fr/fr/support"),
    "0D8C": Vendor("C-Media", "cmedia.com.tw", via_oem=True),
    "8564": Vendor("Transcend", "transcend-info.com"),
    "04B8": Vendor("Epson", "epson.fr", "https://www.epson.fr/fr_FR/support"),
    "03F0": Vendor("HP", "hp.com", "https://support.hp.com/fr-fr/drivers"),
    "04A9": Vendor("Canon", "canon.fr", "https://www.canon.fr/support/"),
    "04F9": Vendor("Brother", "brother.fr", "https://support.brother.com/g/b/countrytop.aspx?c=fr&lang=fr"),
}
VENDORS = {k.upper(): v for k, v in VENDORS.items()}

# Fabricants de cartes mères / PC : (mot-clé dans le nom, nom affiché, domaine, page support, outil ventilateurs)
BOARD_MAKERS = [
    ("asus", "ASUS", "asus.com", "https://www.asus.com/fr/support/download-center/", "Armoury Crate (Fan Xpert)"),
    ("micro-star", "MSI", "msi.com", "https://fr.msi.com/support/download", "MSI Center"),
    ("msi", "MSI", "msi.com", "https://fr.msi.com/support/download", "MSI Center"),
    ("gigabyte", "Gigabyte", "gigabyte.com", "https://www.gigabyte.com/fr/Support", "Gigabyte Control Center"),
    ("asrock", "ASRock", "asrock.com", "https://www.asrock.com/support/index.fr.asp", "ASRock Motherboard Utility"),
    ("dell", "Dell", "dell.com", "https://www.dell.com/support/home/fr-fr", "Dell Power Manager"),
    ("hp", "HP", "hp.com", "https://support.hp.com/fr-fr/drivers", "HP Command Center / OMEN Gaming Hub"),
    ("hewlett", "HP", "hp.com", "https://support.hp.com/fr-fr/drivers", "HP Command Center / OMEN Gaming Hub"),
    ("lenovo", "Lenovo", "lenovo.com", "https://pcsupport.lenovo.com/fr/fr", "Lenovo Vantage"),
    ("acer", "Acer", "acer.com", "https://www.acer.com/fr-fr/support/drivers-and-manuals", "Acer NitroSense / PredatorSense"),
    ("biostar", "Biostar", "biostar.com.tw", "https://www.biostar.com.tw/app/fr/support/download.php", "Biostar Racing GT EVO"),
    ("samsung", "Samsung", "samsung.com", "https://www.samsung.com/fr/support/", "Samsung Settings"),
    ("microsoft", "Microsoft", "microsoft.com",
     "https://support.microsoft.com/fr-fr/surface/download-drivers-and-firmware-for-surface-09bb2e09-2a4b-cb69-0951-078a7739e120",
     ""),
]


@dataclass(frozen=True)
class BoardMaker:
    name: str
    domain: str
    support_url: str
    fan_tool: str


def board_maker(manufacturer: str) -> BoardMaker | None:
    text = (manufacturer or "").lower()
    for key, name, domain, url, fan_tool in BOARD_MAKERS:
        if text.startswith(key) or f" {key}" in f" {text}":
            return BoardMaker(name, domain, url, fan_tool)
    return None


_ID_RE = re.compile(r"(?:VEN|VID)_([0-9A-F]{4})", re.IGNORECASE)
_DEV_RE = re.compile(r"(?:DEV|PID)_([0-9A-F]{4})", re.IGNORECASE)


def parse_ids(device_id: str) -> tuple[str, str, str]:
    """'PCI\\VEN_8086&DEV_7A70&SUBSYS…' -> ('PCI', '8086', '7A70')."""
    bus = device_id.split("\\", 1)[0].upper() if device_id else ""
    ven = _ID_RE.search(device_id or "")
    dev = _DEV_RE.search(device_id or "")
    return bus, ven.group(1).upper() if ven else "", dev.group(1).upper() if dev else ""


def vendor_for(device_id: str, fallback_name: str = "") -> Vendor:
    _bus, ven, _dev = parse_ids(device_id)
    if ven and ven in VENDORS:
        return VENDORS[ven]
    lower = (fallback_name or "").lower()
    for vendor in {v.name: v for v in VENDORS.values()}.values():
        if vendor.name.split(" ")[0].lower() in lower:
            return vendor
    return Vendor(fallback_name.strip() or "Inconnu")


def short_hardware_id(device_id: str) -> str:
    """Identifiant court adapté à une recherche : 'PCI\\VEN_8086&DEV_7A70'."""
    bus, ven, dev = parse_ids(device_id)
    if not (bus and ven and dev):
        return "\\".join(device_id.split("\\")[:2]) if device_id else ""
    if bus == "USB":
        return f"USB\\VID_{ven}&PID_{dev}"
    if bus == "HDAUDIO":
        return f"HDAUDIO\\FUNC_01&VEN_{ven}&DEV_{dev}"
    return f"{bus}\\VEN_{ven}&DEV_{dev}"


def microsoft_catalog_url(device_id: str) -> str:
    """Recherche du pilote exact dans le Catalogue Microsoft Update (site officiel)."""
    return "https://www.catalog.update.microsoft.com/Search.aspx?q=" + quote(short_hardware_id(device_id))


def web_search_url(device_name: str, vendor: Vendor | None = None, extra: str = "pilote") -> str:
    query = f'"{device_name}" {extra}'
    if vendor and vendor.domain:
        query += f" site:{vendor.domain}"
    return "https://www.google.com/search?q=" + quote_plus(query)


def board_drivers_search_url(board: BoardMaker, product: str) -> str:
    return "https://www.google.com/search?q=" + quote_plus(f"{product} drivers support site:{board.domain}")
