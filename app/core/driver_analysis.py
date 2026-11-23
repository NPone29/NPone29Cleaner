"""Analyse complète des pilotes : un état clair pour chaque périphérique important.

On croise quatre sources :
  1. les pilotes installés (WMI),
  2. les mises à jour proposées par Windows Update (installables automatiquement),
  3. les périphériques signalés en erreur,
  4. le site du fabricant quand il offre un service de vérification (NVIDIA).
Pour chaque périphérique, on propose ensuite la meilleure action : installer
automatiquement, ou ouvrir la page officielle du pilote.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable

from app.core import drivers, nvidia
from app.core.drivers import DriverUpdate, InstalledDriver, ProblemDevice
from app.core.vendors import (BoardMaker, Vendor, board_drivers_search_url, board_maker, microsoft_catalog_url,
                              parse_ids, vendor_for, web_search_url)

log = logging.getLogger(__name__)

# Statuts, du plus urgent au moins urgent.
PROBLEM, UPDATE, VENDOR_UPDATE, GENERIC, OLD, OK = "problem", "update", "vendor_update", "generic", "old", "ok"
STATUS_ORDER = [PROBLEM, UPDATE, VENDOR_UPDATE, GENERIC, OLD, OK]
STATUS_LABELS = {
    PROBLEM: "⚠ Problème",
    UPDATE: "⬆ Mise à jour",
    VENDOR_UPDATE: "⬆ Chez le fabricant",
    GENERIC: "ℹ Pilote générique",
    OLD: "ℹ Ancien",
    OK: "✓ À jour",
}

CATEGORY_LABELS = {
    "DISPLAY": "Carte graphique", "MEDIA": "Audio", "NET": "Réseau", "BLUETOOTH": "Bluetooth",
    "CAMERA": "Caméra", "IMAGE": "Caméra / scanner", "USB": "USB", "HDC": "Stockage", "SCSIADAPTER": "Stockage",
    "SYSTEM": "Carte mère / chipset", "BIOMETRIC": "Biométrie", "SMARTCARDREADER": "Lecteur de cartes",
    "SENSOR": "Capteurs", "FIRMWARE": "Firmware", "PRINTER": "Imprimante", "HIDCLASS": "Périphérique d'entrée",
    "MOUSE": "Souris", "KEYBOARD": "Clavier", "PORTS": "Ports série", "MONITOR": "Écran", "DISKDRIVE": "Disque",
    "OTHERHARDWARE": "Autre", "NETWORKING": "Réseau", "AUDIOPROCESSINGOBJECT": "Audio",
}
IMPORTANT_CLASSES = {"DISPLAY", "MEDIA", "NET", "BLUETOOTH", "CAMERA", "IMAGE", "BIOMETRIC"}
GROUPED_CLASSES = {"SYSTEM", "USB", "HDC", "SCSIADAPTER", "SMARTCARDREADER", "SENSOR"}
SKIP_BUSES = {"ROOT", "SWD", "SW", "COMPOSITEBUS", "UMB", "HTREE", "ACPI_HAL", "STORAGE", "SCSI", "BTHENUM",
              "BTHLEDEVICE", "MMDEVAPI", "DISPLAY", "HID", "VMBUS", "TS_USB_HUB"}
SKIP_CLASSES = {"SOFTWARECOMPONENT", "SOFTWAREDEVICE", "EXTENSION", "AUDIOENDPOINT", "VOLUME", "VOLUMESNAPSHOT",
                "PRINTQUEUE", "LEGACYDRIVER", "COMPUTER", "PROCESSOR", "WPD", "DISKDRIVE", "MONITOR"}
OLD_AFTER_YEARS = 4


@dataclass
class DeviceStatus:
    key: str
    name: str
    category: str
    vendor: Vendor
    version: str
    date: datetime | None
    provider: str
    device_id: str
    status: str
    explanation: str
    latest_version: str = ""
    wu_update_ids: list[str] = field(default_factory=list)
    nvidia_driver: nvidia.NvidiaDriver | None = None
    links: list[tuple[str, str]] = field(default_factory=list)
    count: int = 1   # nombre de composants regroupés sur cette ligne

    @property
    def status_label(self) -> str:
        return STATUS_LABELS[self.status]

    @property
    def can_auto_install(self) -> bool:
        return bool(self.wu_update_ids) or bool(self.nvidia_driver and self.nvidia_driver.download_url)


@dataclass
class AnalysisReport:
    devices: list[DeviceStatus]
    board: str = ""
    notes: list[str] = field(default_factory=list)

    def count(self, status: str) -> int:
        return sum(1 for d in self.devices if d.status == status)


# --------------------------------------------------------------------------- règles

def is_relevant(d: InstalledDriver, show_all: bool = False) -> bool:
    bus, _ven, _dev = parse_ids(d.device_id)
    cls = d.device_class.upper()
    if bus in SKIP_BUSES or cls in SKIP_CLASSES:
        return False
    if show_all:
        return True
    if cls in IMPORTANT_CLASSES:
        # Sans identifiant fabricant (VEN/VID), c'est un périphérique virtuel de Windows
        # (énumérateur Bluetooth, Wi-Fi Direct…) : rien à mettre à jour de ce côté.
        return not (d.is_microsoft and not _ven)
    # Composants tiers (chipset Intel/AMD, contrôleurs USB/SATA, lecteurs de cartes…)
    return not d.is_microsoft and bus in {"PCI", "USB", "HDAUDIO", "ACPI", "INTELAUDIO", "BTH"}


def _matches(update: DriverUpdate, d: InstalledDriver) -> bool:
    target = update.hardware_id.lower()
    if not target:
        return False
    if d.hardware_id.lower() == target or d.device_id.lower().startswith(target):
        return True
    same_bus = target.split("\\")[0] == d.device_id.lower().split("\\")[0]
    return same_bus and _same_ids(target, d.device_id.lower())


def _same_ids(a: str, b: str) -> bool:
    _, va, da = parse_ids(a)
    _, vb, db = parse_ids(b)
    return bool(va and da) and (va, da) == (vb, db)


def _is_generic(d: InstalledDriver, vendor: Vendor) -> bool:
    cls = d.device_class.upper()
    if cls not in {"DISPLAY", "MEDIA", "NET", "BLUETOOTH"}:
        return False
    if "basic display" in d.device_name.lower():
        return True
    return d.is_microsoft and vendor.name not in {"Microsoft", "Inconnu"}


def _is_old(d: InstalledDriver, now: datetime) -> bool:
    if d.date is None or d.date.year < 2000:  # certains INF (chipset Intel) portent une date fictive
        return False
    if d.is_microsoft:  # pilotes intégrés à Windows : datés volontairement de 2006, mis à jour par Windows
        return False
    if d.device_class.upper() not in IMPORTANT_CLASSES:
        return False
    return (now - d.date).days > OLD_AFTER_YEARS * 365


def _links(name: str, vendor: Vendor, device_id: str, board: BoardMaker | None, board_product: str,
           nv: nvidia.NvidiaDriver | None) -> list[tuple[str, str]]:
    links: list[tuple[str, str]] = []
    if nv:
        links.append((f"Page du pilote NVIDIA {nv.version}", nv.details_url))
    if vendor.via_oem and board and board_product:
        links.append((f"Pilotes de la carte mère {board.name}", board_drivers_search_url(board, board_product)))
    if vendor.support_url:
        links.append((f"Site {vendor.name}", vendor.support_url))
    links.append(("Recherche sur le web", web_search_url(name, vendor)))
    _bus, ven, dev = parse_ids(device_id)
    if ven and dev:
        links.append(("Catalogue Microsoft", microsoft_catalog_url(device_id)))
    return links


def _explain(status: str, d_name: str, vendor: Vendor, provider: str, version: str, latest: str,
             date: datetime | None, problem: str = "", basic_display: bool = False) -> str:
    if status == PROBLEM:
        return (f"Windows signale un problème : « {problem} ». Installer ou réinstaller le bon pilote "
                "règle souvent le souci.")
    if status == UPDATE:
        return (f"Windows Update propose une version plus récente de ce pilote (fournie par {provider or vendor.name}). "
                "L'installation est automatique : cochez-le puis cliquez sur « Installer automatiquement ».")
    if status == VENDOR_UPDATE:
        return (f"{vendor.name} a publié la version {latest} (vous avez la {version}). L'application peut la "
                "télécharger depuis le site officiel et lancer l'installateur, ou vous pouvez ouvrir la page du pilote.")
    if status == GENERIC:
        if basic_display:
            return ("Aucun vrai pilote graphique n'est installé : l'affichage et les jeux en souffrent. "
                    "Installez le pilote du fabricant de la carte graphique.")
        return ("Ce périphérique utilise le pilote générique de Windows. Il fonctionne, mais le pilote du fabricant "
                "peut apporter de meilleures performances ou des fonctions en plus.")
    if status == OLD:
        year = date.year if date else "?"
        return (f"Ce pilote date de {year}. Aucune mise à jour n'a été trouvée automatiquement : "
                "vous pouvez vérifier sur le site du fabricant avec les liens ci-dessous.")
    extra = f" ni chez {vendor.name}" if vendor.name == "NVIDIA" else ""
    return f"Aucune mise à jour trouvée sur Windows Update{extra}. Ce pilote semble à jour."


# --------------------------------------------------------------------------- analyse

def analyse(
    installed: list[InstalledDriver],
    updates: list[DriverUpdate],
    problems: list[ProblemDevice],
    nvidia_latest: dict[str, nvidia.NvidiaDriver] | None = None,
    board_info: dict[str, str] | None = None,
    show_all: bool = False,
    now: datetime | None = None,
) -> AnalysisReport:
    """Fonction pure (testable) qui construit l'état de chaque périphérique."""
    now = now or datetime.now()
    nvidia_latest = nvidia_latest or {}
    board_info = board_info or {}
    board = board_maker(board_info.get("board_maker", "")) or board_maker(board_info.get("system", ""))
    board_product = board_info.get("board_product", "") or board_info.get("model", "")
    # Un périphérique désactivé (22) ou débranché (45) n'est pas un problème de pilote.
    problem_by_id = {p.device_id.lower(): p for p in problems if p.error_code not in (22, 45)}
    used_updates: set[str] = set()
    rows: list[DeviceStatus] = []
    groups: dict[tuple, DeviceStatus] = {}

    for d in installed:
        problem = problem_by_id.get(d.device_id.lower())
        if not problem and not is_relevant(d, show_all):
            continue
        vendor = vendor_for(d.device_id, d.manufacturer or d.provider)
        cls = d.device_class.upper()
        category = CATEGORY_LABELS.get(cls, d.device_class or "Autre")
        matched = [u for u in updates if _matches(u, d)]
        used_updates.update(u.update_id for u in matched)

        nv = None
        latest = ""
        if cls == "DISPLAY" and vendor.name == "NVIDIA" and d.device_name in nvidia_latest:
            candidate = nvidia_latest[d.device_name]
            installed_nv = nvidia.windows_to_nvidia_version(d.version)
            latest = candidate.version
            if nvidia.is_newer(candidate.version, installed_nv):
                nv = candidate
        version_text = nvidia.windows_to_nvidia_version(d.version) if vendor.name == "NVIDIA" and cls == "DISPLAY" \
            else d.version

        if problem:
            status = PROBLEM
        elif matched:
            status = UPDATE
        elif nv:
            status = VENDOR_UPDATE
        elif _is_generic(d, vendor):
            status = GENERIC
        elif _is_old(d, now):
            status = OLD
        else:
            status = OK

        # Regroupe les nombreux composants identiques du chipset / des contrôleurs.
        if status == OK and cls in GROUPED_CLASSES and not show_all:
            key = (cls, vendor.name)
            if key in groups:
                group = groups[key]
                group.count += 1
                if group.version != version_text:
                    group.version = "plusieurs versions"
                continue
            name = f"{vendor.name} — {category.lower()}"
        else:
            name = d.device_name

        row = DeviceStatus(
            key=d.device_id or d.device_name,
            name=name,
            category=category,
            vendor=vendor,
            version=version_text,
            date=d.date,
            provider=d.provider,
            device_id=d.device_id,
            status=status,
            explanation=_explain(status, d.device_name, vendor, d.provider, version_text, latest, d.date,
                                 problem.error_text if problem else "",
                                 basic_display="basic display" in d.device_name.lower()),
            latest_version=matched[0].title if matched else latest,
            wu_update_ids=[u.update_id for u in matched],
            nvidia_driver=nv,
            links=_links(d.device_name, vendor, d.device_id, board, board_product, nv),
        )
        if status == OK and cls in GROUPED_CLASSES and not show_all:
            groups[(cls, vendor.name)] = row
        rows.append(row)

    # Mises à jour Windows Update qui ne correspondent à aucune ligne affichée.
    for u in updates:
        if u.update_id in used_updates:
            continue
        vendor = vendor_for(u.hardware_id, u.manufacturer)
        rows.append(DeviceStatus(
            key=f"wu:{u.update_id}", name=u.model or u.title,
            category=CATEGORY_LABELS.get(u.driver_class.upper(), u.driver_class or "Autre"),
            vendor=vendor, version="—", date=u.version_date, provider=u.manufacturer, device_id=u.hardware_id,
            status=UPDATE,
            explanation=_explain(UPDATE, u.title, vendor, u.manufacturer, "", "", u.version_date),
            latest_version=u.title, wu_update_ids=[u.update_id],
            links=_links(u.model or u.title, vendor, u.hardware_id, board, board_product, None),
        ))

    # Périphériques en erreur sans pilote du tout (absents de la liste des pilotes installés).
    known = {r.device_id.lower() for r in rows}
    for p in problems:
        if p.device_id.lower() in known or p.error_code in (22, 45):  # désactivé / débranché : pas un souci
            continue
        hwid = p.hardware_ids[0] if p.hardware_ids else p.device_id
        vendor = vendor_for(hwid, "")
        rows.append(DeviceStatus(
            key=p.device_id, name=p.name, category="À identifier", vendor=vendor, version="—", date=None,
            provider="", device_id=hwid, status=PROBLEM,
            explanation=_explain(PROBLEM, p.name, vendor, "", "", "", None, p.error_text),
            links=_links(p.name, vendor, hwid, board, board_product, None),
        ))

    for row in rows:
        if row.count > 1:
            row.name = f"{row.name} ({row.count} composants)"
    rows.sort(key=lambda r: (STATUS_ORDER.index(r.status), r.category, r.name.lower()))
    board_text = f"{board.name} {board_product}" if board and board_product else board_product
    return AnalysisReport(rows, board=board_text)


def run_full_analysis(service: drivers.WindowsUpdateDriverService,
                      progress: Callable[[str], None] | None = None,
                      show_all: bool = False,
                      reuse_search: bool = False) -> AnalysisReport:
    """Collecte toutes les données (plusieurs dizaines de secondes) puis appelle `analyse`."""
    def step(text: str) -> None:
        if progress:
            progress(text)

    notes: list[str] = []
    step("Lecture des pilotes installés…")
    installed = drivers.list_installed_drivers()
    step("Recherche des périphériques en erreur…")
    problems = drivers.list_problem_devices()
    step("Identification de la carte mère…")
    board_info = drivers.get_hardware_vendors()

    if reuse_search and service.last_results is not None:
        updates = service.last_results
    else:
        step("Recherche sur Windows Update (jusqu'à 2 minutes)…")
        try:
            updates = service.search().result()
        except Exception as exc:
            updates = []
            notes.append(f"Windows Update indisponible : {exc}")

    nvidia_latest: dict[str, nvidia.NvidiaDriver] = {}
    nvidia_gpus = {d.device_name for d in installed
                   if d.device_class.upper() == "DISPLAY" and vendor_for(d.device_id).name == "NVIDIA"}
    for name in nvidia_gpus:
        step(f"Vérification sur nvidia.com : {name}…")
        try:
            latest = nvidia.latest_driver(name)
            if latest:
                nvidia_latest[name] = latest
        except Exception as exc:
            log.warning("Vérification NVIDIA impossible : %s", exc)
            notes.append("Vérification sur nvidia.com impossible (connexion Internet ?).")

    report = analyse(installed, updates, problems, nvidia_latest, board_info, show_all=show_all)
    report.notes.extend(notes)
    return report

