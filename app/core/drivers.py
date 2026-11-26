"""Pilotes : inventaire (WMI), périphériques en erreur et mises à jour via Windows Update.

Les mises à jour de pilotes viennent du catalogue officiel Microsoft (Windows Update),
c'est-à-dire les pilotes signés publiés par les fabricants. C'est l'approche la plus sûre :
pas de site tiers douteux, pas de pilote non signé.

L'API Windows Update (COM) impose d'utiliser ses objets dans le thread qui les a créés :
toutes les opérations passent donc par un unique thread de travail dédié.
"""

from __future__ import annotations

import logging
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable

from app.utils.fmt import parse_wmi_datetime
from app.utils.system import CommandResult, run_cancellable, run_command, run_powershell

log = logging.getLogger(__name__)


# --------------------------------------------------------------------------- modèles

@dataclass
class InstalledDriver:
    device_name: str
    device_class: str
    manufacturer: str
    provider: str
    version: str
    date: datetime | None
    inf_name: str
    is_signed: bool
    device_id: str = ""
    hardware_id: str = ""

    @property
    def is_microsoft(self) -> bool:
        return self.provider.strip().lower() in {"microsoft", "microsoft corporation", ""}


@dataclass
class ProblemDevice:
    name: str
    error_code: int
    device_id: str
    hardware_ids: list[str] = field(default_factory=list)

    @property
    def error_text(self) -> str:
        return DEVICE_ERRORS.get(self.error_code, f"Erreur {self.error_code}")


@dataclass
class DriverUpdate:
    update_id: str
    title: str
    manufacturer: str
    model: str
    driver_class: str
    version_date: datetime | None
    size: int
    optional: bool
    description: str = ""
    hardware_id: str = ""


@dataclass
class InstallOutcome:
    update_id: str
    title: str
    success: bool
    message: str


@dataclass
class InstallReport:
    outcomes: list[InstallOutcome] = field(default_factory=list)
    reboot_required: bool = False

    @property
    def succeeded(self) -> int:
        return sum(1 for o in self.outcomes if o.success)

    @property
    def failed(self) -> int:
        return sum(1 for o in self.outcomes if not o.success)


# Codes d'erreur du Gestionnaire de périphériques (ConfigManagerErrorCode).
DEVICE_ERRORS = {
    1: "Périphérique mal configuré",
    3: "Pilote endommagé ou mémoire insuffisante",
    10: "Le périphérique ne peut pas démarrer",
    12: "Ressources insuffisantes",
    14: "Redémarrage nécessaire",
    18: "Pilotes à réinstaller",
    19: "Informations de registre endommagées",
    21: "Suppression en cours",
    22: "Périphérique désactivé",
    24: "Périphérique absent ou mal installé",
    28: "Aucun pilote installé",
    29: "Désactivé par le firmware",
    31: "Le pilote ne fonctionne pas correctement",
    32: "Service du pilote désactivé",
    37: "Échec d'initialisation du pilote",
    39: "Pilote endommagé ou manquant",
    43: "Arrêté par Windows (problème signalé)",
    45: "Périphérique non connecté",
    47: "Préparé pour le retrait",
    48: "Pilote bloqué (incompatible)",
    52: "Signature numérique du pilote non vérifiée",
}

# Erreurs Windows Update fréquentes -> message compréhensible.
WU_ERRORS = {
    0x80070005: "Accès refusé : relancez l'application en tant qu'administrateur.",
    0x8024402C: "Impossible de joindre Windows Update : vérifiez votre connexion Internet.",
    0x80072EE7: "Impossible de joindre Windows Update : vérifiez votre connexion Internet.",
    0x80072EFD: "Impossible de joindre Windows Update : vérifiez votre connexion Internet.",
    0x80072EFE: "La connexion à Windows Update a été interrompue.",
    0x80070422: "Le service Windows Update est désactivé.",
    0x8024A000: "Le service Windows Update n'est pas disponible.",
    0x80240016: "Une autre installation est déjà en cours. Réessayez plus tard.",
    0x8024001E: "Opération annulée (le service s'est arrêté).",
    0x80240024: "Aucune mise à jour à installer.",
    0x8024200B: "L'installation a échoué.",
    0x80240022: "Toutes les mises à jour ont échoué.",
}

# Codes de résultat (OperationResultCode) de l'API Windows Update.
_ORC_SUCCEEDED = 2
_ORC_SUCCEEDED_WITH_ERRORS = 3


def com_error_message(exc: BaseException) -> str:
    """Transforme une erreur COM en message lisible."""
    hresult = None
    try:
        import pywintypes  # type: ignore

        if isinstance(exc, pywintypes.com_error):
            hresult = exc.hresult
            excepinfo = exc.excepinfo
            if excepinfo and len(excepinfo) > 5 and excepinfo[5]:
                hresult = excepinfo[5]
    except ImportError:
        pass
    if hresult is None:
        return str(exc)
    code = hresult & 0xFFFFFFFF
    return WU_ERRORS.get(code, f"Erreur Windows Update 0x{code:08X}")


# --------------------------------------------------------------------------- WMI

def _wmi():
    import win32com.client  # type: ignore

    return win32com.client.GetObject(r"winmgmts:{impersonationLevel=impersonate}!\\.\root\cimv2")


def _com_init():
    import pythoncom  # type: ignore

    pythoncom.CoInitialize()
    return pythoncom


def _with_com(fn):
    """Exécute `fn` avec COM initialisé ; ses objets COM sont libérés avant CoUninitialize."""
    pythoncom = _com_init()
    try:
        return fn()
    finally:
        import gc

        gc.collect()
        pythoncom.CoUninitialize()


def _str(value) -> str:
    return "" if value is None else str(value).strip()


def list_installed_drivers() -> list[InstalledDriver]:
    """Inventaire des pilotes installés (Win32_PnPSignedDriver). Prend quelques secondes."""
    return _with_com(_query_installed_drivers)


def list_problem_devices() -> list[ProblemDevice]:
    """Périphériques signalés en erreur dans le Gestionnaire de périphériques."""
    return _with_com(_query_problem_devices)


def get_hardware_vendors() -> dict[str, str]:
    """Fabricant du PC et des cartes graphiques, pour proposer les outils officiels."""
    return _with_com(_query_hardware_vendors)


def _query_installed_drivers() -> list[InstalledDriver]:
    rows = _wmi().ExecQuery(
        "SELECT DeviceName, DeviceClass, Manufacturer, DriverProviderName, DriverVersion, "
        "DriverDate, InfName, IsSigned, DeviceID, HardWareID FROM Win32_PnPSignedDriver"
    )
    found: list[InstalledDriver] = []
    for row in rows:
        name = _str(row.DeviceName)
        if not name:
            continue
        found.append(InstalledDriver(
            device_name=name,
            device_class=_str(row.DeviceClass),
            manufacturer=_str(row.Manufacturer),
            provider=_str(row.DriverProviderName),
            version=_str(row.DriverVersion),
            date=parse_wmi_datetime(row.DriverDate),
            inf_name=_str(row.InfName),
            is_signed=bool(row.IsSigned),
            device_id=_str(row.DeviceID),
            hardware_id=_str(row.HardWareID),
        ))
    found.sort(key=lambda d: d.device_name.lower())
    return found


def _query_problem_devices() -> list[ProblemDevice]:
    rows = _wmi().ExecQuery(
        "SELECT Name, ConfigManagerErrorCode, PNPDeviceID, HardwareID FROM Win32_PnPEntity "
        "WHERE ConfigManagerErrorCode <> 0"
    )
    devices = []
    for row in rows:
        devices.append(ProblemDevice(
            name=_str(row.Name) or "Périphérique inconnu",
            error_code=int(row.ConfigManagerErrorCode or 0),
            device_id=_str(row.PNPDeviceID),
            hardware_ids=[_str(h) for h in (row.HardwareID or [])],
        ))
    return devices


def _query_hardware_vendors() -> dict[str, str]:
    wmi = _wmi()
    # Uniquement marque, modèle et identifiant de modèle (SKU) : jamais de numéro de série / service tag.
    result = {"system": "", "model": "", "sku": "", "gpus": "", "board_maker": "", "board_product": ""}
    for row in wmi.ExecQuery("SELECT Manufacturer, Model, SystemSKUNumber FROM Win32_ComputerSystem"):
        result["system"] = _str(row.Manufacturer)
        result["model"] = _str(row.Model)
        result["sku"] = _str(row.SystemSKUNumber)
    for row in wmi.ExecQuery("SELECT Manufacturer, Product FROM Win32_BaseBoard"):
        result["board_maker"] = _str(row.Manufacturer)
        result["board_product"] = _str(row.Product)
    gpus = [_str(r.Name) for r in wmi.ExecQuery("SELECT Name FROM Win32_VideoController")]
    result["gpus"] = " | ".join(g for g in gpus if g)
    return result


@dataclass
class DeviceIds:
    """Identifiants d'un périphérique tels que Windows les utilise pour choisir son pilote."""
    hardware_ids: tuple[str, ...] = ()
    compatible_ids: tuple[str, ...] = ()
    matching_id: str = ""      # identifiant qui a servi à choisir le pilote installé


_ENUM_KEY = "SYSTEM\\CurrentControlSet\\Enum\\"
_CLASS_KEY = "SYSTEM\\CurrentControlSet\\Control\\Class\\"


def read_device_ids(instance_ids: list[str]) -> dict[str, DeviceIds]:
    """Lit dans le registre (sans droits administrateur) les identifiants de chaque périphérique.

    Clé du dictionnaire : identifiant d'instance en minuscules. Un périphérique illisible est ignoré.
    """
    try:
        import winreg
    except ImportError:  # hors Windows
        return {}

    def read(key, name):
        try:
            return winreg.QueryValueEx(key, name)[0]
        except OSError:
            return None

    found: dict[str, DeviceIds] = {}
    for instance in instance_ids:
        if not instance or instance.lower() in found:
            continue
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, _ENUM_KEY + instance) as key:
                hardware = read(key, "HardwareID") or []
                compatible = read(key, "CompatibleIDs") or []
                driver_key = read(key, "Driver") or ""
        except OSError:
            continue
        matching = ""
        if driver_key:
            try:
                with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, _CLASS_KEY + driver_key) as key:
                    matching = str(read(key, "MatchingDeviceId") or "")
            except OSError:
                pass
        found[instance.lower()] = DeviceIds(tuple(str(h) for h in hardware if h),
                                            tuple(str(c) for c in compatible if c), matching)
    return found


def driver_version_of(device_ids: list[str]) -> dict[str, tuple[str, datetime | None]]:
    """Version et date du pilote actuellement utilisé par ces périphériques (après une installation)."""
    wanted = {d.lower() for d in device_ids if d}
    return {d.device_id.lower(): (d.version, d.date) for d in list_installed_drivers()
            if d.device_id.lower() in wanted}


@dataclass
class VendorTool:
    vendor: str
    label: str
    url: str


_GPU_TOOLS = [
    ("nvidia", VendorTool("NVIDIA", "NVIDIA App (pilotes GeForce)", "https://www.nvidia.com/fr-fr/software/nvidia-app/")),
    ("amd", VendorTool("AMD", "AMD Software: Adrenalin", "https://www.amd.com/fr/support/download/drivers.html")),
    ("radeon", VendorTool("AMD", "AMD Software: Adrenalin", "https://www.amd.com/fr/support/download/drivers.html")),
    ("intel", VendorTool("Intel", "Intel Driver & Support Assistant",
                         "https://www.intel.fr/content/www/fr/fr/support/detect.html")),
]

_OEM_TOOLS = [
    ("dell", VendorTool("Dell", "Dell SupportAssist", "https://www.dell.com/support/home/fr-fr")),
    ("hp", VendorTool("HP", "HP Support Assistant", "https://support.hp.com/fr-fr/drivers")),
    ("hewlett", VendorTool("HP", "HP Support Assistant", "https://support.hp.com/fr-fr/drivers")),
    ("lenovo", VendorTool("Lenovo", "Lenovo Vantage / pilotes", "https://pcsupport.lenovo.com/fr/fr")),
    ("asus", VendorTool("ASUS", "Assistance ASUS (MyASUS / Armoury Crate)", "https://www.asus.com/fr/support/download-center/")),
    ("acer", VendorTool("Acer", "Assistance Acer", "https://www.acer.com/fr-fr/support/drivers-and-manuals")),
    ("msi", VendorTool("MSI", "MSI Center / pilotes", "https://fr.msi.com/support/download")),
    ("micro-star", VendorTool("MSI", "MSI Center / pilotes", "https://fr.msi.com/support/download")),
    ("gigabyte", VendorTool("Gigabyte", "Gigabyte Control Center", "https://www.gigabyte.com/fr/Support")),
    ("samsung", VendorTool("Samsung", "Samsung Update", "https://www.samsung.com/fr/support/")),
    ("microsoft", VendorTool("Microsoft", "Surface : pilotes et firmware",
                             "https://support.microsoft.com/fr-fr/surface/download-drivers-and-firmware-for-surface-09bb2e09-2a4b-cb69-0951-078a7739e120")),
]


def recommended_vendor_tools(vendors: dict[str, str]) -> list[VendorTool]:
    """Outils officiels des fabricants détectés (GPU et marque du PC)."""
    tools: list[VendorTool] = []
    seen: set[str] = set()
    gpus = vendors.get("gpus", "").lower()
    for key, tool in _GPU_TOOLS:
        if key in gpus and tool.url not in seen:
            tools.append(tool)
            seen.add(tool.url)
    system = vendors.get("system", "").lower()
    for key, tool in _OEM_TOOLS:
        if system.startswith(key) or f" {key}" in f" {system}":
            if tool.url not in seen:
                tools.append(tool)
                seen.add(tool.url)
            break
    return tools


# --------------------------------------------------------------------------- Windows Update

def _get(obj, name: str, default=None):
    try:
        value = getattr(obj, name)
        return default if value is None else value
    except Exception:
        return default


def _to_datetime(value) -> datetime | None:
    if value is None:
        return None
    try:
        return datetime(value.year, value.month, value.day)
    except Exception:
        return None


class WindowsUpdateDriverService:
    """Recherche et installation de pilotes via l'agent Windows Update (COM)."""

    SEARCH_CRITERIA = "IsInstalled=0 and Type='Driver' and IsHidden=0"

    def __init__(self) -> None:
        self._generation = 0      # incrémenté quand une installation bloquée est abandonnée
        self._executor = self._new_executor()
        self._session = None
        self._updates: dict[str, object] = {}  # objets COM, utilisés uniquement dans le thread dédié
        self.last_results: list[DriverUpdate] | None = None  # dernière recherche (lecture seule)

    @staticmethod
    def _new_executor() -> ThreadPoolExecutor:
        return ThreadPoolExecutor(max_workers=1, thread_name_prefix="windows-update", initializer=_com_init)

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)

    def abandon(self) -> None:
        """Abandonne une opération bloquée (ex. installation qui ne se termine jamais).

        L'appel COM en cours ne peut pas être interrompu : on laisse l'ancien thread finir seul et les
        opérations suivantes passent par un nouveau thread, avec une nouvelle session Windows Update.
        """
        self._generation += 1
        old = self._executor
        self._executor = self._new_executor()
        self._session = None
        self._updates = {}
        old.shutdown(wait=False, cancel_futures=True)
        log.warning("Windows Update : opération en cours abandonnée à la demande de l'utilisateur")

    # Les méthodes publiques renvoient des Future ; le travail s'exécute dans le thread dédié.
    def search(self) -> Future:
        return self._executor.submit(self._search)

    def install(self, update_ids: list[str],
                progress: Callable[[int, int, str, str], None] | None = None,
                cancel=None, report: InstallReport | None = None) -> Future:
        """`cancel` : évènement qui empêche d'installer les pilotes suivants ; `report` : bilan rempli au fur
        et à mesure (lisible même si l'installation est abandonnée en cours de route)."""
        return self._executor.submit(self._install, list(update_ids), progress, cancel,
                                     report if report is not None else InstallReport(), self._generation)

    def _get_session(self):
        if self._session is None:
            import win32com.client  # type: ignore

            self._session = win32com.client.Dispatch("Microsoft.Update.Session")
            self._session.ClientApplicationID = "NPone29 Cleaner"
        return self._session

    def _search(self) -> list[DriverUpdate]:
        try:
            searcher = self._get_session().CreateUpdateSearcher()
            searcher.Online = True
            result = searcher.Search(self.SEARCH_CRITERIA)
        except Exception as exc:
            raise RuntimeError(com_error_message(exc)) from exc

        self._updates.clear()
        found: list[DriverUpdate] = []
        for i in range(result.Updates.Count):
            upd = result.Updates.Item(i)
            update_id = str(upd.Identity.UpdateID)
            self._updates[update_id] = upd
            found.append(DriverUpdate(
                update_id=update_id,
                title=_str(_get(upd, "Title", "")),
                manufacturer=_str(_get(upd, "DriverManufacturer", "")),
                model=_str(_get(upd, "DriverModel", "")),
                driver_class=_str(_get(upd, "DriverClass", "")),
                version_date=_to_datetime(_get(upd, "DriverVerDate")),
                size=int(_get(upd, "MaxDownloadSize", 0) or 0),
                optional=bool(_get(upd, "BrowseOnly", False)),
                description=_str(_get(upd, "Description", "")),
                hardware_id=_str(_get(upd, "DriverHardwareID", "")),
            ))
        log.info("Windows Update : %d pilote(s) disponible(s)", len(found))
        self.last_results = found
        return found

    def _install(self, update_ids: list[str], progress, cancel, report: InstallReport,
                 generation: int) -> InstallReport:
        import win32com.client  # type: ignore

        if not self._updates:
            self._search()
        session = self._get_session()
        updates = self._updates
        total = len(update_ids)

        for index, update_id in enumerate(update_ids, start=1):
            if cancel is not None and cancel.is_set():
                break
            upd = updates.get(update_id)
            if upd is None:
                report.outcomes.append(InstallOutcome(update_id, update_id, False,
                                                      "Mise à jour introuvable (relancez la recherche)."))
                continue
            title = _str(_get(upd, "Title", update_id))
            try:
                if not _get(upd, "EulaAccepted", True):
                    upd.AcceptEula()
                coll = win32com.client.Dispatch("Microsoft.Update.UpdateColl")
                coll.Add(upd)

                if not _get(upd, "IsDownloaded", False):
                    if progress:
                        progress(index, total, title, "Téléchargement")
                    downloader = session.CreateUpdateDownloader()
                    downloader.Updates = coll
                    dl = downloader.Download()
                    if dl.ResultCode not in (_ORC_SUCCEEDED, _ORC_SUCCEEDED_WITH_ERRORS):
                        report.outcomes.append(InstallOutcome(update_id, title, False, "Échec du téléchargement."))
                        continue

                if progress:
                    progress(index, total, title, "Installation")
                installer = session.CreateUpdateInstaller()
                installer.Updates = coll
                try:
                    installer.AllowSourcePrompts = False
                    installer.ForceQuiet = True
                except Exception:
                    pass
                res = installer.Install()
                ok = res.ResultCode in (_ORC_SUCCEEDED, _ORC_SUCCEEDED_WITH_ERRORS)
                if res.RebootRequired:
                    report.reboot_required = True
                message = "Installé" if ok else f"Échec de l'installation (code {res.ResultCode})"
                if ok and res.RebootRequired:
                    message += " — redémarrage nécessaire"
                report.outcomes.append(InstallOutcome(update_id, title, ok, message))
            except Exception as exc:
                log.exception("Installation du pilote %s impossible", title)
                report.outcomes.append(InstallOutcome(update_id, title, False, com_error_message(exc)))

        # Les pilotes installés ne doivent plus apparaître à la prochaine recherche (sauf si cette installation
        # a été abandonnée entre-temps : l'état appartient alors au nouveau thread).
        if generation == self._generation:
            self._updates.clear()
            self.last_results = None
        return report


# --------------------------------------------------------------------------- divers

def create_restore_point(description: str = "NPone29 Cleaner") -> CommandResult:
    """Crée un point de restauration système (admin requis, protection système activée).

    Windows limite par défaut la création à un point toutes les 24 h : on lève
    temporairement cette limite, puis on remet la valeur d'origine.
    """
    safe_desc = description.replace("'", "''")
    script = f"""
$ErrorActionPreference = 'Stop'
$key = 'HKLM:\\Software\\Microsoft\\Windows NT\\CurrentVersion\\SystemRestore'
$old = (Get-ItemProperty -Path $key -Name SystemRestorePointCreationFrequency -ErrorAction SilentlyContinue).SystemRestorePointCreationFrequency
try {{
    Set-ItemProperty -Path $key -Name SystemRestorePointCreationFrequency -Value 0 -Type DWord
    Checkpoint-Computer -Description '{safe_desc}' -RestorePointType 'MODIFY_SETTINGS'
    Write-Output 'OK'
}} finally {{
    if ($null -eq $old) {{
        Remove-ItemProperty -Path $key -Name SystemRestorePointCreationFrequency -ErrorAction SilentlyContinue
    }} else {{
        Set-ItemProperty -Path $key -Name SystemRestorePointCreationFrequency -Value $old -Type DWord
    }}
}}
"""
    return run_powershell(script, timeout=600)


def install_inf_folder(folder: str, cancel=None) -> CommandResult:
    """Ajoute les pilotes d'un dossier au magasin de Windows et les installe sur les périphériques concernés.

    Windows applique ses propres règles : un pilote n'est installé sur un périphérique que s'il est meilleur
    que le pilote actuel. Code 3010 : redémarrage nécessaire.
    """
    return run_cancellable(["pnputil.exe", "/add-driver", f"{folder}\\*.inf", "/subdirs", "/install"],
                           timeout=1800, cancel=cancel)


def rescan_hardware() -> CommandResult:
    """Relance la détection du matériel (équivalent « Rechercher les modifications »)."""
    return run_command(["pnputil.exe", "/scan-devices"], timeout=120)
