"""Inventaire du matériel du PC, expliqué simplement, pour la page « Mon PC ».

Tout est lu localement (WMI, registre, psutil, nvidia-smi) : aucune donnée ne quitte le PC.
"""

from __future__ import annotations

import logging
import winreg
from dataclasses import dataclass, field

import psutil

from app.core import nvidia, sysinfo
from app.core.vendors import board_maker
from app.utils.fmt import format_date, human_size, parse_wmi_datetime

log = logging.getLogger(__name__)

# Explications destinées aux non-connaisseurs.
EXPLANATIONS = {
    "cpu": "Le processeur est le « cerveau » de l'ordinateur : il exécute les programmes. Plus il a de cœurs, "
           "plus il peut mener de tâches en parallèle ; plus sa fréquence (GHz) est élevée, plus chaque tâche va vite.",
    "ram": "La mémoire vive (RAM) est le « plan de travail » du PC : plus elle est grande, plus vous pouvez garder "
           "de programmes et d'onglets ouverts sans ralentissement. Elle se vide quand on éteint l'ordinateur.",
    "gpu": "La carte graphique calcule les images affichées à l'écran. Elle est essentielle pour les jeux, "
           "le montage vidéo et la 3D. Sa mémoire (VRAM) stocke les textures et les images.",
    "board": "La carte mère est la grande plaque sur laquelle tout est branché : elle relie le processeur, la mémoire, "
             "la carte graphique et les disques. Son BIOS/UEFI est le petit logiciel qui démarre le PC.",
    "storage": "Le disque conserve Windows, vos programmes et vos fichiers, même PC éteint. Un SSD (surtout NVMe) "
               "est bien plus rapide qu'un disque dur classique (HDD), qui a des pièces mécaniques.",
    "network": "Les cartes réseau connectent le PC à Internet et à votre box : par câble (Ethernet, plus stable) "
               "ou sans fil (Wi-Fi, plus pratique).",
    "audio": "La puce audio produit le son des haut-parleurs et des casques et gère le micro. Les cartes "
             "graphiques ont aussi leur propre sortie audio pour le son par HDMI/DisplayPort.",
    "display": "L'écran affiche l'image calculée par la carte graphique. Sa définition (ex. 2560×1440) indique "
               "le nombre de points affichés, et sa fréquence (Hz) le nombre d'images par seconde.",
    "cooling": "Les ventilateurs évacuent la chaleur des composants. Un PC trop chaud ralentit pour se protéger "
               "et devient bruyant : dépoussiérez-le une à deux fois par an.",
    "battery": "La batterie alimente l'ordinateur portable loin d'une prise. Elle s'use avec le temps : "
               "sa capacité réelle diminue au fil des cycles de charge.",
}

TITLES = {
    "cpu": "Processeur", "ram": "Mémoire vive", "gpu": "Carte graphique", "board": "Carte mère",
    "storage": "Stockage", "network": "Réseau", "audio": "Audio", "display": "Écran",
    "cooling": "Refroidissement", "battery": "Batterie",
}

RAM_TYPES = {20: "DDR", 21: "DDR2", 24: "DDR3", 26: "DDR4", 34: "DDR5", 35: "LPDDR5", 30: "LPDDR4"}
BUS_TYPES = {17: "NVMe", 11: "SATA", 7: "USB", 8: "RAID", 10: "SAS", 3: "ATA", 12: "SD", 13: "MMC"}
MEDIA_TYPES = {3: "Disque dur (HDD)", 4: "SSD", 5: "SCM"}


@dataclass
class Advice:
    level: str  # ok | info | warn
    text: str


@dataclass
class Component:
    kind: str
    key: str
    name: str
    summary: str
    specs: list[tuple[str, str]] = field(default_factory=list)
    advice: list[Advice] = field(default_factory=list)

    @property
    def title(self) -> str:
        return TITLES.get(self.kind, self.kind)

    @property
    def explanation(self) -> str:
        return EXPLANATIONS.get(self.kind, "")


@dataclass
class HardwareReport:
    components: list[Component]
    ram_slots: int = 0
    ram_modules: list[int] = field(default_factory=list)   # tailles en octets, une par barrette
    board_name: str = ""

    def of(self, kind: str) -> list[Component]:
        return [c for c in self.components if c.kind == kind]


# --------------------------------------------------------------------------- conseils (fonctions pures)

def ram_advice(total: int, modules: int, slots: int) -> list[Advice]:
    gb = total / 1024 ** 3
    advice = []
    if gb < 7.5:
        advice.append(Advice("warn", f"{gb:.0f} Go, c'est peu : suffisant pour Internet et la bureautique légère, "
                                     "mais ajouter de la mémoire rendrait le PC plus fluide."))
    elif gb < 15:
        advice.append(Advice("ok", f"{gb:.0f} Go : correct pour un usage courant (Internet, bureautique, vidéos)."))
    elif gb < 31:
        advice.append(Advice("ok", f"{gb:.0f} Go : confortable pour les jeux et le multitâche."))
    else:
        advice.append(Advice("ok", f"{gb:.0f} Go : très confortable, même pour le montage vidéo ou les jeux exigeants."))
    if slots and modules and slots > modules:
        free = slots - modules
        advice.append(Advice("info", f"{free} emplacement(s) libre(s) sur la carte mère : "
                                     "vous pourrez ajouter de la mémoire plus tard."))
    if modules == 1 and slots >= 2:
        advice.append(Advice("info", "Une seule barrette : en ajouter une seconde identique active le « double canal » "
                                     "et améliore nettement les performances."))
    return advice


def storage_advice(media: str, health: int | None, free_ratio: float | None) -> list[Advice]:
    advice = []
    if health not in (None, 0):
        advice.append(Advice("warn", "Windows signale un problème de santé sur ce disque : sauvegardez vos "
                                     "fichiers importants dès que possible."))
    if "HDD" in media:
        advice.append(Advice("info", "Disque dur mécanique : le remplacer (ou le compléter) par un SSD rendrait "
                                     "le démarrage et les programmes beaucoup plus rapides."))
    if free_ratio is not None:
        if free_ratio < 0.10:
            advice.append(Advice("warn", "Moins de 10 % d'espace libre : Windows peut ralentir. "
                                         "Utilisez la page Nettoyage et la recherche de gros fichiers."))
        elif free_ratio > 0.25:
            advice.append(Advice("ok", "Espace libre suffisant."))
    if not advice:
        advice.append(Advice("ok", "Aucun problème détecté."))
    return advice


def gpu_advice(temperature: float | None, fan: float | None) -> list[Advice]:
    advice = []
    if temperature is not None:
        if temperature >= 85:
            advice.append(Advice("warn", f"{temperature:.0f} °C : très chaud. Vérifiez la poussière et l'aération du boîtier."))
        elif temperature >= 75:
            advice.append(Advice("info", f"{temperature:.0f} °C : chaud mais normal en jeu."))
        else:
            advice.append(Advice("ok", f"{temperature:.0f} °C : température normale."))
    if fan is not None and fan == 0 and (temperature or 0) < 60:
        advice.append(Advice("info", "Ventilateurs à 0 % : c'est normal ! Ils s'arrêtent quand la carte est froide "
                                     "(mode silencieux) et redémarrent sous charge."))
    return advice


# --------------------------------------------------------------------------- collecte

def _dec(value: float, digits: int) -> str:
    """Nombre décimal à la française : 3.4 -> '3,4'."""
    return f"{value:.{digits}f}".replace(".", ",")


def _str(value) -> str:
    return "" if value is None else str(value).strip()


def _gpu_vram_from_registry() -> dict[str, int]:
    """La VRAM exacte est dans le registre (WMI plafonne à 4 Go)."""
    result: dict[str, int] = {}
    base = r"SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}"
    try:
        root = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, base)
    except OSError:
        return result
    with root:
        for i in range(64):
            try:
                sub = winreg.EnumKey(root, i)
            except OSError:
                break
            try:
                with winreg.OpenKey(root, sub) as key:
                    desc = winreg.QueryValueEx(key, "DriverDesc")[0]
                    size = None
                    for name in ("HardwareInformation.qwMemorySize", "HardwareInformation.MemorySize"):
                        try:
                            raw = winreg.QueryValueEx(key, name)[0]
                        except OSError:
                            continue
                        size = int.from_bytes(raw, "little") if isinstance(raw, bytes) else int(raw)
                        if size:
                            break
                    if size:
                        result[str(desc).strip()] = size
            except OSError:
                continue
    return result


def _monitor_names(wmi_root) -> list[str]:
    names = []
    try:
        for row in wmi_root.ExecQuery("SELECT UserFriendlyName, ManufacturerName FROM WmiMonitorID"):
            raw = row.UserFriendlyName or []
            name = "".join(chr(c) for c in raw if c).strip()
            maker = "".join(chr(c) for c in (row.ManufacturerName or []) if c).strip()
            names.append(name or maker or "Écran")
    except Exception:
        pass
    return names


def collect() -> HardwareReport:
    """Rassemble tout l'inventaire (2 à 5 secondes). À appeler hors du thread de l'interface."""
    from app.core.drivers import _with_com

    return _with_com(_collect)


def _collect() -> HardwareReport:
    import win32com.client  # type: ignore

    locator = win32com.client.Dispatch("WbemScripting.SWbemLocator")
    cim = locator.ConnectServer(".", r"root\cimv2")
    components: list[Component] = []

    # ---- Carte mère + BIOS
    board_maker_name = board_product = bios_text = ""
    for row in cim.ExecQuery("SELECT Manufacturer, Product, Version FROM Win32_BaseBoard"):
        board_maker_name, board_product = _str(row.Manufacturer), _str(row.Product)
    for row in cim.ExecQuery("SELECT Manufacturer, SMBIOSBIOSVersion, ReleaseDate FROM Win32_BIOS"):
        bios_text = f"{_str(row.SMBIOSBIOSVersion)} du {format_date(parse_wmi_datetime(row.ReleaseDate))}"
    maker = board_maker(board_maker_name)
    board_name = f"{maker.name if maker else board_maker_name} {board_product}".strip()
    info = sysinfo.get_system_info()
    pc_model = " ".join(x for x in (info.manufacturer, info.model) if x and "o.e.m" not in x.lower())
    components.append(Component("board", "board", board_name or "Carte mère", board_product or board_maker_name, [
        ("Fabricant", board_maker_name or "—"),
        ("Modèle", board_product or "—"),
        ("BIOS / UEFI", bios_text or "—"),
        ("PC", pc_model or "—"),
        ("Windows", info.os_name),
        ("Allumé depuis", f"{info.uptime.days} j {info.uptime.seconds // 3600} h"),
    ], [Advice("info", "Les pilotes du chipset, du son et du réseau se trouvent sur le site du fabricant "
                       "de la carte mère.")] if maker else []))

    # ---- Processeur
    for row in cim.ExecQuery("SELECT Name, NumberOfCores, NumberOfLogicalProcessors, MaxClockSpeed, "
                             "L2CacheSize, L3CacheSize, SocketDesignation FROM Win32_Processor"):
        name = " ".join(_str(row.Name).split())
        cores, threads = int(row.NumberOfCores or 0), int(row.NumberOfLogicalProcessors or 0)
        ghz = (row.MaxClockSpeed or 0) / 1000
        components.append(Component("cpu", f"cpu{len(components)}", name, f"{cores} cœurs • {_dec(ghz, 1)} GHz", [
            ("Modèle", name),
            ("Cœurs / threads", f"{cores} cœurs, {threads} threads"),
            ("Fréquence de base", f"{_dec(ghz, 2)} GHz"),
            ("Cache L2 / L3", f"{human_size((row.L2CacheSize or 0) * 1024)} / {human_size((row.L3CacheSize or 0) * 1024)}"),
            ("Support (socket)", _str(row.SocketDesignation) or "—"),
        ]))

    # ---- Mémoire vive
    modules = []
    ram_type = ""
    speeds = set()
    for row in cim.ExecQuery("SELECT Capacity, ConfiguredClockSpeed, Speed, Manufacturer, PartNumber, "
                             "DeviceLocator, SMBIOSMemoryType FROM Win32_PhysicalMemory"):
        size = int(row.Capacity or 0)
        modules.append((size, _str(row.Manufacturer), _str(row.PartNumber), _str(row.DeviceLocator)))
        ram_type = RAM_TYPES.get(int(row.SMBIOSMemoryType or 0), ram_type)
        speed = row.ConfiguredClockSpeed or row.Speed
        if speed:
            speeds.add(int(speed))
    slots = 0
    for row in cim.ExecQuery("SELECT MemoryDevices FROM Win32_PhysicalMemoryArray"):
        slots += int(row.MemoryDevices or 0)
    total = sum(m[0] for m in modules) or psutil.virtual_memory().total
    speed_text = f"{max(speeds)} MT/s" if speeds else ""
    specs = [("Total", human_size(total)), ("Type", ram_type or "—"), ("Vitesse", speed_text or "—"),
             ("Barrettes", f"{len(modules)} sur {slots} emplacements" if slots else str(len(modules)))]
    for size, maker_name, part, slot in modules:
        specs.append((slot or "Barrette", f"{human_size(size)} — {maker_name} {part}".strip()))
    components.append(Component("ram", "ram", f"{human_size(total)} {ram_type}".strip(),
                                f"{human_size(total)} {ram_type} {speed_text}".strip(), specs,
                                ram_advice(total, len(modules), slots)))

    # ---- Cartes graphiques
    vram = _gpu_vram_from_registry()
    smi = {g.name: g for g in nvidia.query_smi()}
    for row in cim.ExecQuery("SELECT Name, DriverVersion, DriverDate, AdapterRAM, CurrentHorizontalResolution, "
                             "CurrentVerticalResolution, CurrentRefreshRate, PNPDeviceID FROM Win32_VideoController"):
        name = _str(row.Name)
        if not name or "virtual" in name.lower() or "remote" in name.lower():
            continue
        live = smi.get(name)
        memory = vram.get(name) or (int(live.memory_total_mb * 1024 * 1024) if live and live.memory_total_mb else 0) \
            or int(row.AdapterRAM or 0)
        version = live.driver_version if live else _str(row.DriverVersion)
        specs = [("Modèle", name), ("Mémoire vidéo (VRAM)", human_size(memory) if memory else "—"),
                 ("Version du pilote", version), ("Date du pilote", format_date(parse_wmi_datetime(row.DriverDate)))]
        if row.CurrentHorizontalResolution:
            specs.append(("Affichage actuel", f"{row.CurrentHorizontalResolution}×{row.CurrentVerticalResolution} "
                                              f"à {row.CurrentRefreshRate} Hz"))
        advice = []
        if live:
            specs += [("Température", f"{live.temperature:.0f} °C" if live.temperature is not None else "—"),
                      ("Ventilateurs", f"{live.fan_percent:.0f} %" if live.fan_percent is not None else "—"),
                      ("Utilisation", f"{live.utilization:.0f} %" if live.utilization is not None else "—")]
            advice = gpu_advice(live.temperature, live.fan_percent)
        components.append(Component("gpu", f"gpu:{name}", name, human_size(memory) + " VRAM" if memory else "",
                                    specs, advice))

    # ---- Stockage
    system_usage = None
    try:
        system_usage = psutil.disk_usage("C:\\")
    except OSError:
        pass
    try:
        storage = locator.ConnectServer(".", r"root\Microsoft\Windows\Storage")
        rows = list(storage.ExecQuery("SELECT FriendlyName, MediaType, BusType, Size, HealthStatus, DeviceId "
                                      "FROM MSFT_PhysicalDisk"))
    except Exception:
        rows = []
    disk_index = 0
    for row in rows:
        bus = BUS_TYPES.get(int(row.BusType or 0), "")
        if int(row.BusType or 0) == 15:  # disque virtuel (fichier VHD)
            continue
        media = MEDIA_TYPES.get(int(row.MediaType or 0), "Disque")
        if media == "SSD" and bus == "NVMe":
            media = "SSD NVMe"
        size = int(row.Size or 0)
        health = int(row.HealthStatus) if row.HealthStatus is not None else None
        is_system = str(row.DeviceId) == "0"
        free_ratio = (system_usage.free / system_usage.total) if (is_system and system_usage) else None
        specs = [("Modèle", _str(row.FriendlyName)), ("Type", media), ("Connexion", bus or "—"),
                 ("Capacité", human_size(size)),
                 ("Santé", {0: "Bonne", 1: "Avertissement", 2: "Mauvaise"}.get(health, "Inconnue"))]
        if is_system and system_usage:
            specs.append(("Lecteur C: (Windows)", f"{human_size(system_usage.free)} libres sur "
                                                  f"{human_size(system_usage.total)}"))
        components.append(Component("storage", f"disk{disk_index}", _str(row.FriendlyName) or "Disque",
                                    f"{media} • {human_size(size)}", specs,
                                    storage_advice(media, health, free_ratio)))
        disk_index += 1

    # ---- Réseau (cartes physiques uniquement)
    for row in cim.ExecQuery("SELECT Name, NetConnectionStatus, Speed, PNPDeviceID, MACAddress "
                             "FROM Win32_NetworkAdapter WHERE PhysicalAdapter = TRUE"):
        pnp = _str(row.PNPDeviceID).upper()
        if not (pnp.startswith("PCI\\") or pnp.startswith("USB\\")):
            continue
        name = _str(row.Name)
        wifi = any(k in name.lower() for k in ("wi-fi", "wifi", "wireless", "wlan", "802.11"))
        connected = row.NetConnectionStatus == 2
        speed = int(row.Speed or 0)
        speed_text = (f"{_dec(speed / 1e9, 1)} Gb/s" if speed >= 1e9 else f"{speed / 1e6:.0f} Mb/s") if speed and connected else "—"
        components.append(Component("network", f"net:{name}", name,
                                    ("Wi-Fi" if wifi else "Ethernet") + (" • connecté" if connected else ""), [
            ("Modèle", name), ("Type", "Sans fil (Wi-Fi)" if wifi else "Câble (Ethernet)"),
            ("État", "Connecté" if connected else "Non connecté"), ("Débit de la liaison", speed_text),
        ]))

    # ---- Audio
    for row in cim.ExecQuery("SELECT Name, Manufacturer, PNPDeviceID FROM Win32_SoundDevice"):
        pnp = _str(row.PNPDeviceID).upper()
        if pnp.startswith("ROOT\\") or pnp.startswith("SWD\\"):
            continue  # périphériques audio virtuels (logiciels)
        name = _str(row.Name)
        components.append(Component("audio", f"audio:{name}", name, _str(row.Manufacturer),
                                    [("Modèle", name), ("Fabricant", _str(row.Manufacturer))]))

    # ---- Écrans
    try:
        wmi_root = locator.ConnectServer(".", r"root\wmi")
        monitors = _monitor_names(wmi_root)
    except Exception:
        monitors = []
    for i, name in enumerate(monitors):
        components.append(Component("display", f"display{i}", name, "Écran", [("Modèle", name)]))

    # ---- Refroidissement
    fans = []
    for row in cim.ExecQuery("SELECT Name, DesiredSpeed, ActiveCooling FROM Win32_Fan"):
        fans.append(_str(row.Name))
    cooling_specs: list[tuple[str, str]] = []
    cooling_advice: list[Advice] = []
    for gpu_name, live in smi.items():
        if live.fan_percent is not None:
            cooling_specs.append((f"Ventilateurs {gpu_name}", f"{live.fan_percent:.0f} %"))
        if live.temperature is not None:
            cooling_specs.append((f"Température {gpu_name}", f"{live.temperature:.0f} °C"))
        cooling_advice += gpu_advice(live.temperature, live.fan_percent)
    if fans:
        cooling_specs += [("Ventilateur", f) for f in fans]
    tool = maker.fan_tool if maker and maker.fan_tool else "l'outil de votre carte mère"
    cooling_advice.append(Advice("info", "Windows n'a pas accès à la vitesse des ventilateurs du processeur et du "
                                         f"boîtier sur ce PC. Pour les voir ou les régler, utilisez {tool} "
                                         "ou le BIOS/UEFI."))
    components.append(Component("cooling", "cooling", "Ventilation",
                                f"GPU {next(iter(smi.values())).temperature:.0f} °C" if smi and
                                next(iter(smi.values())).temperature is not None else "Ventilateurs",
                                cooling_specs or [("Capteurs", "Non accessibles depuis Windows")], cooling_advice))

    # ---- Batterie (portables)
    battery = psutil.sensors_battery()
    if battery is not None:
        mins = battery.secsleft // 60 if battery.secsleft and battery.secsleft > 0 else None
        components.append(Component("battery", "battery", "Batterie", f"{battery.percent:.0f} %", [
            ("Charge", f"{battery.percent:.0f} %"),
            ("Secteur", "Branché" if battery.power_plugged else "Sur batterie"),
            ("Autonomie restante", f"{mins // 60} h {mins % 60:02d}" if mins else "—"),
        ]))

    return HardwareReport(components, ram_slots=slots, ram_modules=[m[0] for m in modules], board_name=board_name)


def live_values() -> dict[str, str]:
    """Valeurs rafraîchies régulièrement sur la carte : utilisation CPU/RAM, température GPU."""
    values = {"cpu": f"{psutil.cpu_percent(interval=None):.0f} %",
              "ram": f"{psutil.virtual_memory().percent:.0f} % utilisés"}
    for live in nvidia.query_smi():
        if live.temperature is not None:
            values[f"gpu:{live.name}"] = f"{live.temperature:.0f} °C • {live.utilization or 0:.0f} %"
            values["cooling"] = f"{live.temperature:.0f} °C • {live.fan_percent or 0:.0f} %"
    return values
