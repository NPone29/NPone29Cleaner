"""Analyse complète des pilotes : un état clair et honnête pour chaque périphérique.

On croise :
  1. les pilotes installés (WMI),
  2. les mises à jour proposées par Windows Update (installables automatiquement),
  3. les périphériques signalés en erreur,
  4. les sources officielles (app/core/driver_sources) : NVIDIA, Intel (liste de son outil officiel),
     Dell, HP, Lenovo et le Catalogue Microsoft Update (pilotes de tous les fabricants, classés selon les
     règles de Windows).
Un pilote n'est affiché « à jour » que si une source fiable l'a vérifié. Sinon il est
« non vérifiable » et l'application propose le lien officiel le plus précis.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable

from app.core import driver_sources, drivers, nvidia
from app.core.driver_sources import DeviceRef, DownloadLink, SystemContext, VendorCheck
from app.core.driver_sources.base import newest
from app.core.driver_sources.boards import pc_target
from app.core.driver_sources.microsoft_catalog import CatalogDriver
from app.core.drivers import DeviceIds, DriverUpdate, InstalledDriver, ProblemDevice
from app.core.packages import VendorPackage
from app.core.vendors import Vendor, parse_ids, vendor_for

log = logging.getLogger(__name__)

# Statuts, du plus urgent au moins urgent.
PROBLEM, UPDATE, VENDOR_UPDATE, GENERIC = "problem", "update", "vendor_update", "generic"
OPTIONAL, OLD, UNVERIFIED, VERIFIED = "optional", "old", "unverified", "verified"
STATUS_ORDER = [PROBLEM, UPDATE, VENDOR_UPDATE, GENERIC, OPTIONAL, OLD, UNVERIFIED, VERIFIED]
STATUS_LABELS = {
    PROBLEM: "⚠ Problème",
    UPDATE: "⬆ Mise à jour disponible",
    VENDOR_UPDATE: "⬆ Mise à jour disponible",
    GENERIC: "ℹ Pilote générique",
    OPTIONAL: "ℹ Version générique dispo.",
    OLD: "? Non vérifiable (ancien)",
    UNVERIFIED: "? Non vérifiable",
    VERIFIED: "✓ À jour (vérifié)",
}
WINDOWS_UPDATE = "Windows Update"

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
    links: list[DownloadLink] = field(default_factory=list)   # du plus précis au moins précis
    checks: list[VendorCheck] = field(default_factory=list)
    checked_source: str = ""        # « Catalogue officiel Dell », « Windows Update »… (vide : non vérifié)
    checked_at: datetime | None = None
    no_api_note: str = ""           # pourquoi la version n'a pas pu être vérifiée
    count: int = 1   # nombre de composants regroupés sur cette ligne
    catalog_driver: CatalogDriver | None = None    # pilote du Catalogue Microsoft installable automatiquement
    device_ids: list[str] = field(default_factory=list)   # tous les périphériques regroupés sur cette ligne
    package: VendorPackage | None = None   # installateur officiel (NVIDIA, Intel) téléchargeable et vérifiable

    @property
    def status_label(self) -> str:
        return STATUS_LABELS[self.status]

    @property
    def can_auto_install(self) -> bool:
        return bool(self.wu_update_ids) or self.package is not None or self.catalog_driver is not None

    @property
    def best_link(self) -> DownloadLink | None:
        return self.links[0] if self.links else None


@dataclass
class AnalysisReport:
    devices: list[DeviceStatus]
    board: str = ""
    notes: list[str] = field(default_factory=list)

    def count(self, *statuses: str) -> int:
        return sum(1 for d in self.devices if d.status in statuses)


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


def to_ref(d: InstalledDriver, ids: DeviceIds | None = None) -> DeviceRef:
    """Le pilote installé tel que le voient les sources officielles (même clé que dans `analyse`)."""
    vendor = vendor_for(d.device_id, d.manufacturer or d.provider)
    ids = ids or DeviceIds()
    return DeviceRef(key=d.device_id or d.device_name, name=d.device_name, device_id=d.device_id,
                     hardware_id=d.hardware_id, version=d.version, device_class=d.device_class,
                     vendor_name=vendor.name, date=d.date, provider=d.provider, hardware_ids=ids.hardware_ids,
                     compatible_ids=ids.compatible_ids, matching_id=ids.matching_id)


def _explain(status: str, vendor: Vendor, provider: str, version: str, latest: str, date: datetime | None,
             source: str = "", problem: str = "", basic_display: bool = False, nvidia_auto: bool = False,
             note: str = "", catalog: CatalogDriver | None = None, package: VendorPackage | None = None) -> str:
    text = _explain_status(status, vendor, provider, version, latest, date, source, problem, basic_display,
                           nvidia_auto, catalog, package)
    return f"{text} {note}" if note and status in (VENDOR_UPDATE, VERIFIED, OPTIONAL) else text


def _explain_status(status: str, vendor: Vendor, provider: str, version: str, latest: str, date: datetime | None,
                    source: str, problem: str, basic_display: bool, nvidia_auto: bool,
                    catalog: CatalogDriver | None = None, package: VendorPackage | None = None) -> str:
    if status == PROBLEM:
        return (f"Windows signale un problème : « {problem} ». Installer ou réinstaller le bon pilote "
                "règle souvent le souci.")
    if status == UPDATE:
        return (f"Windows Update propose une version plus récente de ce pilote (fournie par {provider or vendor.name}). "
                "L'installation est automatique : cochez-le puis cliquez sur « Installer automatiquement ».")
    if status == VENDOR_UPDATE:
        if nvidia_auto:
            action = ("L'application peut la télécharger depuis nvidia.com, vérifier sa signature et lancer "
                      "l'installateur.")
        elif catalog is not None:
            action = (f"Elle est prévue pour votre matériel et Windows la choisira à la place de l'actuelle. "
                      f"L'application peut la télécharger depuis Microsoft ({catalog.size_text}), vérifier son "
                      "empreinte et sa signature, puis l'installer.")
        elif package is not None:
            action = (f"L'application peut la télécharger depuis {package.host} ({package.size_text}), vérifier son "
                      f"empreinte et sa signature {package.publisher}, puis lancer l'installateur officiel.")
        else:
            action = "Cliquez sur « Télécharger » : le fichier ou la page officielle s'ouvre dans votre navigateur."
        return f"{source} propose la version {latest} (vous avez la {version}). {action}"
    if status == OPTIONAL:
        return (f"Votre pilote ({version}) est le plus récent prévu pour votre matériel, il fonctionne et reste "
                "le choix recommandé.")
    if status == GENERIC:
        if basic_display:
            return ("Aucun vrai pilote graphique n'est installé : l'affichage et les jeux en souffrent. "
                    "Installez le pilote du fabricant de la carte graphique.")
        return ("Ce périphérique utilise le pilote générique de Windows. Il fonctionne, mais le pilote du fabricant "
                "peut apporter de meilleures performances ou des fonctions en plus.")
    if status == VERIFIED:
        if source == WINDOWS_UPDATE:
            return "Pilote fourni avec Windows : Windows Update ne propose aucune version plus récente."
        return (f"Vérifié auprès de la source officielle ({source}) : la dernière version publiée est la {latest}, "
                "votre pilote est à jour.")
    if status == OLD:
        year = date.year if date else "?"
        return (f"Ce pilote date de {year} et aucune source officielle ne permet de vérifier automatiquement s'il "
                "existe plus récent. Cliquez sur « Télécharger » pour comparer sur le site officiel.")
    return ("Aucune source officielle ne permet de vérifier automatiquement ce pilote : il n'est donc pas affiché "
            "comme à jour. Cliquez sur « Télécharger » pour comparer avec la version du site officiel.")


# --------------------------------------------------------------------------- analyse

def analyse(
    installed: list[InstalledDriver],
    updates: list[DriverUpdate],
    problems: list[ProblemDevice],
    checks: dict[str, list[VendorCheck]] | None = None,
    ctx: SystemContext | None = None,
    wu_ok: bool = True,
    show_all: bool = False,
    now: datetime | None = None,
    consulted: list[str] | None = None,
) -> AnalysisReport:
    """Fonction pure (testable) qui construit l'état de chaque périphérique.

    `checks` : vérifications des sources officielles, par clé de périphérique (voir `to_ref`).
    `wu_ok` : False si la recherche Windows Update a échoué (rien n'est alors « vérifié » par Windows Update).
    `consulted` : noms des sources réellement consultées (pour expliquer les pilotes non vérifiés).
    """
    now = now or datetime.now()
    checks = checks or {}
    ctx = ctx or SystemContext()
    consulted = consulted or []
    # Un périphérique désactivé (22) ou débranché (45) n'est pas un problème de pilote.
    problem_by_id = {p.device_id.lower(): p for p in problems if p.error_code not in (22, 45)}
    used_updates: set[str] = set()
    rows: list[DeviceStatus] = []
    groups: dict[tuple, DeviceStatus] = {}
    single_names: dict[int, str] = {}

    for d in installed:
        problem = problem_by_id.get(d.device_id.lower())
        if not problem and not is_relevant(d, show_all):
            continue
        ref = to_ref(d)
        vendor = vendor_for(d.device_id, d.manufacturer or d.provider)
        cls = d.device_class.upper()
        category = CATEGORY_LABELS.get(cls, d.device_class or "Autre")
        matched = [u for u in updates if _matches(u, d)]
        used_updates.update(u.update_id for u in matched)

        own = checks.get(ref.key, [])
        comparable = [c for c in own if c.comparable]
        newer = [c for c in comparable if c.update_available]
        best = None
        if newer:
            top = newest([c.latest_version for c in newer])
            best = next((c for c in newer if c.latest_version == top), newer[0])
        nv = best.extra if best and isinstance(best.extra, nvidia.NvidiaDriver) else None
        catalog = best.extra if best and isinstance(best.extra, CatalogDriver) else None
        package = best.extra if best and isinstance(best.extra, VendorPackage) else None
        if package is None and nv and nv.download_url:
            package = nv.package()
        is_nvidia_gpu = vendor.name == "NVIDIA" and cls == "DISPLAY"
        version_text = nvidia.windows_to_nvidia_version(d.version) if is_nvidia_gpu else d.version

        source, checked_at, latest = "", None, ""
        if comparable:
            source = ", ".join(dict.fromkeys(c.source for c in comparable))
            checked_at = max(c.checked_at for c in comparable)
            latest = newest([c.latest_version for c in comparable]) or comparable[0].latest_version

        if problem:
            status = PROBLEM
        elif matched:
            status = UPDATE
            source, checked_at = WINDOWS_UPDATE, now
        elif best:
            status = VENDOR_UPDATE
            source, checked_at, latest = best.source, best.checked_at, best.latest_version
        elif _is_generic(d, vendor):
            status = GENERIC
        elif comparable:
            status = OPTIONAL if any(c.generic_newer for c in comparable) else VERIFIED
        elif d.is_microsoft and wu_ok:
            status = VERIFIED
            source, checked_at = WINDOWS_UPDATE, now
        elif _is_old(d, now):
            status = OLD
        else:
            status = UNVERIFIED

        note = ""
        if status in (OLD, UNVERIFIED):
            # Une source qui a trouvé le pilote sans pouvoir conclure explique pourquoi ; sinon, on dit
            # pourquoi aucune source n'a pu le vérifier.
            note = (" ".join(dict.fromkeys(c.note for c in own if c.note))
                    or " ".join(driver_sources.no_api_notes(ctx, ref, consulted)))
        if status == VENDOR_UPDATE:
            check_note = best.note
        elif status in (VERIFIED, OPTIONAL):
            check_note = " ".join(dict.fromkeys(c.note for c in comparable if c.note))
        else:
            check_note = ""
        if status == OPTIONAL:
            generic = [c for c in comparable if c.generic_newer]
            latest = newest([c.latest_version for c in generic]) or generic[0].latest_version
            source = ", ".join(dict.fromkeys(c.source for c in generic))

        # Regroupe les nombreux composants identiques du chipset / des contrôleurs. Une même mise à jour du
        # catalogue (un seul paquet pour tout le chipset) tient aussi sur une seule ligne.
        group_key = (cls, vendor.name, status, catalog.update_id if catalog else "")
        grouped = cls in GROUPED_CLASSES and not show_all and (
            status in (VERIFIED, UNVERIFIED) or (status == VENDOR_UPDATE and catalog is not None))
        if grouped:
            if group_key in groups:
                group = groups[group_key]
                group.count += 1
                group.device_ids.append(d.device_id)
                if group.version != version_text:
                    group.version = "plusieurs versions"
                continue
            name = f"{vendor.name} — {category.lower()}"
            if catalog is not None:
                name += f" (pilote {catalog.version})"
        else:
            name = d.device_name

        row = DeviceStatus(
            key=ref.key,
            name=name,
            category=category,
            vendor=vendor,
            version=version_text,
            date=d.date,
            provider=d.provider,
            device_id=d.device_id,
            status=status,
            explanation=_explain(status, vendor, d.provider, version_text, latest, d.date, source,
                                 problem.error_text if problem else "",
                                 basic_display="basic display" in d.device_name.lower(),
                                 nvidia_auto=bool(nv and nv.download_url), note=check_note,
                                 catalog=catalog if status == VENDOR_UPDATE else None,
                                 package=package if status == VENDOR_UPDATE else None),
            latest_version=matched[0].title if matched else latest,
            wu_update_ids=[u.update_id for u in matched],
            nvidia_driver=nv,
            links=driver_sources.links_for(ctx, ref, own),
            checks=own,
            checked_source=source,
            checked_at=checked_at,
            no_api_note=note,
            catalog_driver=catalog if status == VENDOR_UPDATE else None,
            device_ids=[d.device_id],
            package=package,
        )
        if grouped:
            groups[group_key] = row
            single_names[id(row)] = d.device_name
        rows.append(row)

    # Mises à jour Windows Update qui ne correspondent à aucune ligne affichée.
    for u in updates:
        if u.update_id in used_updates:
            continue
        vendor = vendor_for(u.hardware_id, u.manufacturer)
        ref = DeviceRef(key=f"wu:{u.update_id}", name=u.model or u.title, device_id=u.hardware_id,
                        hardware_id=u.hardware_id, version="", device_class=u.driver_class, vendor_name=vendor.name)
        rows.append(DeviceStatus(
            key=ref.key, name=u.model or u.title,
            category=CATEGORY_LABELS.get(u.driver_class.upper(), u.driver_class or "Autre"),
            vendor=vendor, version="—", date=u.version_date, provider=u.manufacturer, device_id=u.hardware_id,
            status=UPDATE,
            explanation=_explain(UPDATE, vendor, u.manufacturer, "", "", u.version_date),
            latest_version=u.title, wu_update_ids=[u.update_id],
            links=driver_sources.links_for(ctx, ref, []),
            checked_source=WINDOWS_UPDATE, checked_at=now,
        ))

    # Périphériques en erreur sans pilote du tout (absents de la liste des pilotes installés).
    known = {r.device_id.lower() for r in rows}
    for p in problems:
        if p.device_id.lower() in known or p.error_code in (22, 45):  # désactivé / débranché : pas un souci
            continue
        hwid = p.hardware_ids[0] if p.hardware_ids else p.device_id
        vendor = vendor_for(hwid, "")
        ref = DeviceRef(key=p.device_id, name=p.name, device_id=hwid, hardware_id=hwid, version="",
                        device_class="", vendor_name=vendor.name)
        rows.append(DeviceStatus(
            key=p.device_id, name=p.name, category="À identifier", vendor=vendor, version="—", date=None,
            provider="", device_id=hwid, status=PROBLEM,
            explanation=_explain(PROBLEM, vendor, "", "", "", None, problem=p.error_text),
            links=driver_sources.links_for(ctx, ref, []),
        ))

    for row in rows:
        if row.count > 1:
            row.name = f"{row.name} ({row.count} composants)"
        elif id(row) in single_names:     # un groupe d'un seul composant garde le vrai nom du périphérique
            row.name = single_names[id(row)]
    rows.sort(key=lambda r: (STATUS_ORDER.index(r.status), r.category, r.name.lower()))
    board, product, _prebuilt = pc_target(ctx)
    board_text = f"{board.name} {product}" if board and product else product
    report = AnalysisReport(rows, board=board_text)
    if not wu_ok:
        report.notes.append("Windows Update n'a pas pu être consulté : les pilotes fournis par Windows ne sont "
                            "pas comptés comme vérifiés.")
    return report


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
    step("Identification du PC et de la carte mère…")
    ctx = SystemContext.from_board_info(drivers.get_hardware_vendors())

    # La recherche Windows Update (jusqu'à 2 minutes) tourne pendant la consultation des sources officielles.
    wu_ok = True
    search = None
    if not (reuse_search and service.last_results is not None):
        search = service.search()

    problem_ids = {p.device_id.lower() for p in problems if p.error_code not in (22, 45)}
    selected = [d for d in installed if d.device_id.lower() in problem_ids or is_relevant(d, show_all)]
    try:
        ids = drivers.read_device_ids([d.device_id for d in selected])
    except Exception:
        log.warning("Identifiants matériels illisibles", exc_info=True)
        ids = {}
    refs = [to_ref(d, ids.get(d.device_id.lower())) for d in selected]
    run = driver_sources.check_all(ctx, refs, step)

    if search is None:
        updates = service.last_results or []
    else:
        step("Recherche sur Windows Update (jusqu'à 2 minutes)…")
        try:
            updates = search.result()
        except Exception as exc:
            updates, wu_ok = [], False
            notes.append(f"Windows Update indisponible : {exc}")

    report = analyse(installed, updates, problems, run.checks, ctx, wu_ok, show_all=show_all,
                     consulted=run.verified_by)
    report.notes.extend(notes + run.notes)
    return report
