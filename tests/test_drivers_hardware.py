"""Tests de l'analyse des pilotes, de NVIDIA, des fabricants et des conseils matériels."""

from __future__ import annotations

from datetime import datetime

from app.core import driver_analysis, hardware, nvidia, vendors
from app.core.driver_analysis import analyse
from app.core.drivers import DriverUpdate, InstalledDriver, ProblemDevice
from app.utils.download import is_allowed_url

NOW = datetime(2026, 11, 23)


def drv(name, cls, provider, device_id, version="1.0", date=datetime(2025, 1, 1), hwid=""):
    return InstalledDriver(device_name=name, device_class=cls, manufacturer=provider, provider=provider,
                           version=version, date=date, inf_name="oem1.inf", is_signed=True,
                           device_id=device_id, hardware_id=hwid or device_id.rsplit("\\", 1)[0])


GPU = drv("NVIDIA GeForce RTX 4070", "Display", "NVIDIA", r"PCI\VEN_10DE&DEV_2786&SUBSYS_00000000\4&1",
          version="32.0.16.2114")
WIFI = drv("Intel(R) Wi-Fi 6 AX201", "Net", "Intel", r"PCI\VEN_8086&DEV_7A70&SUBSYS_00748086\3&1")
LAN = drv("Realtek Gaming 2.5GbE", "Net", "Realtek", r"PCI\VEN_10EC&DEV_8125&SUBSYS_87D71043\01",
          date=datetime(2020, 6, 14))
BT_ENUM = drv("Microsoft Bluetooth Enumerator", "Bluetooth", "Microsoft", r"BTH\MS_BTHBRB\7&1",
              date=datetime(2006, 6, 21))
CHIPSET = [drv(f"Intel PCI Express Root Port #{i}", "System", "INTEL", fr"PCI\VEN_8086&DEV_7A3{i}\3&{i}",
               version="10.1.46.3") for i in range(5)]
BASIC = drv("Microsoft Basic Display Adapter", "Display", "Microsoft", r"PCI\VEN_1002&DEV_73BF\4&2")
BOARD = {"board_maker": "ASUSTeK COMPUTER INC.", "board_product": "PRIME Z790-P WIFI"}


def by_name(report, name):
    return next(d for d in report.devices if d.name.startswith(name))


# --------------------------------------------------------------------------- fabricants

def test_parse_ids_and_vendor():
    assert vendors.parse_ids(r"PCI\VEN_8086&DEV_7A70&SUBSYS_00748086\3&1") == ("PCI", "8086", "7A70")
    assert vendors.parse_ids(r"USB\VID_046D&PID_C52B\5&1") == ("USB", "046D", "C52B")
    assert vendors.vendor_for(r"HDAUDIO\FUNC_01&VEN_10EC&DEV_0897").name == "Realtek"
    assert vendors.vendor_for(r"USB\VID_046D&PID_C52B").name == "Logitech"
    assert vendors.vendor_for("", "Inconnu SA").name == "Inconnu SA"


def test_short_hardware_id_and_catalog_url():
    assert vendors.short_hardware_id(r"PCI\VEN_8086&DEV_7A70&SUBSYS_00748086\3&1") == r"PCI\VEN_8086&DEV_7A70"
    assert vendors.short_hardware_id(r"USB\VID_046D&PID_C52B\5") == r"USB\VID_046D&PID_C52B"
    url = vendors.microsoft_catalog_url(r"PCI\VEN_8086&DEV_7A70&SUBSYS_00748086\3&1")
    assert url.startswith("https://www.catalog.update.microsoft.com/Search.aspx?q=PCI%5CVEN_8086%26DEV_7A70")


def test_board_maker():
    assert vendors.board_maker("ASUSTeK COMPUTER INC.").name == "ASUS"
    assert vendors.board_maker("Micro-Star International Co., Ltd.").name == "MSI"
    assert vendors.board_maker("To Be Filled By O.E.M.") is None


# --------------------------------------------------------------------------- NVIDIA

def test_nvidia_version_conversion():
    assert nvidia.windows_to_nvidia_version("32.0.16.2114") == "621.14"
    assert nvidia.windows_to_nvidia_version("32.0.15.6214") == "562.14"
    assert nvidia.windows_to_nvidia_version("31.0.15.5222") == "552.22"
    assert nvidia.is_newer("621.24", "621.14")
    assert not nvidia.is_newer("621.14", "621.14")


def test_nvidia_lookup_and_driver_parsing():
    xml = ('<?xml version="1.0"?><LookupValueSearch><LookupValues>'
           '<LookupValue ParentID="127"><Name>NVIDIA GeForce RTX 4070</Name><Value>1015</Value></LookupValue>'
           '<LookupValue ParentID="120"><Name>GeForce RTX 3060</Name><Value>942</Value></LookupValue>'
           '</LookupValues></LookupValueSearch>')
    products = nvidia.parse_lookup(xml)
    assert products["geforce rtx 4070"] == ("127", "1015")
    assert products["geforce rtx 3060"] == ("120", "942")
    data = {"IDS": [{"downloadInfo": {"Version": "621.24", "DownloadURL": "https://fr.download.nvidia.com/x.exe",
                                       "DetailsURL": "https://www.nvidia.com/fr-fr/drivers/details/1/",
                                       "Name": "Pilote%20GeForce", "DownloadURLFileSize": "1.04 GB"}}]}
    driver = nvidia.parse_driver_response(data)
    assert driver.version == "621.24" and driver.name == "Pilote GeForce"
    assert nvidia.parse_driver_response({"IDS": []}) is None


def test_parse_smi_output():
    gpus = nvidia.parse_smi_output("NVIDIA GeForce RTX 4070, 621.14, 46, 0, 13, 12282, 1395, 11.91\n")
    assert gpus[0].temperature == 46 and gpus[0].fan_percent == 0 and gpus[0].driver_version == "621.14"
    assert nvidia.parse_smi_output("garbage") == []


def test_download_url_allowlist():
    hosts = nvidia.ALLOWED_DOWNLOAD_HOSTS
    assert is_allowed_url("https://fr.download.nvidia.com/Windows/621.24/x.exe", hosts)
    assert not is_allowed_url("http://fr.download.nvidia.com/x.exe", hosts)        # pas de HTTP
    assert not is_allowed_url("https://nvidia.com.evil.example/x.exe", hosts)      # faux domaine
    assert not is_allowed_url("https://evil.example/nvidia.com/x.exe", hosts)


# --------------------------------------------------------------------------- analyse des pilotes

def test_windows_update_match_marks_update():
    update = DriverUpdate("u1", "Intel net Driver Update", "Intel", "AX201", "Networking", None, 10, False,
                          hardware_id=r"PCI\VEN_8086&DEV_7A70&SUBSYS_00748086")
    report = analyse([WIFI], [update], [], board_info=BOARD, now=NOW)
    wifi = by_name(report, "Intel(R) Wi-Fi")
    assert wifi.status == driver_analysis.UPDATE
    assert wifi.wu_update_ids == ["u1"] and wifi.can_auto_install


def test_unmatched_update_still_listed():
    update = DriverUpdate("u2", "Audio Update", "Microsoft", "AudioProcessingObject", "OtherHardware", None, 1,
                          False, hardware_id=r"SWD\DRIVERENUM\APO")
    report = analyse([WIFI], [update], [], now=NOW)
    assert any(d.wu_update_ids == ["u2"] and d.status == driver_analysis.UPDATE for d in report.devices)


def test_nvidia_vendor_update():
    latest = nvidia.NvidiaDriver("621.24", "", "https://fr.download.nvidia.com/a.exe",
                                 "https://www.nvidia.com/d/", "1 GB", "Game Ready")
    report = analyse([GPU], [], [], nvidia_latest={GPU.device_name: latest}, now=NOW)
    gpu = by_name(report, "NVIDIA GeForce")
    assert gpu.status == driver_analysis.VENDOR_UPDATE
    assert gpu.version == "621.14" and gpu.nvidia_driver is latest
    same = nvidia.NvidiaDriver("621.14", "", "", "https://www.nvidia.com/d/", "", "")
    assert by_name(analyse([GPU], [], [], nvidia_latest={GPU.device_name: same}, now=NOW),
                   "NVIDIA GeForce").status == driver_analysis.OK


def test_old_driver_links_to_motherboard():
    report = analyse([LAN], [], [], board_info=BOARD, now=NOW)
    lan = by_name(report, "Realtek")
    assert lan.status == driver_analysis.OLD
    labels = [label for label, _ in lan.links]
    assert labels[0] == "Pilotes de la carte mère ASUS"
    assert "Catalogue Microsoft" in labels


def test_generic_and_virtual_devices():
    report = analyse([BT_ENUM, BASIC], [], [], now=NOW)
    names = [d.name for d in report.devices]
    assert "Microsoft Bluetooth Enumerator" not in names          # périphérique virtuel ignoré
    assert by_name(report, "Microsoft Basic").status == driver_analysis.GENERIC


def test_chipset_components_are_grouped():
    report = analyse(CHIPSET, [], [], now=NOW)
    assert len(report.devices) == 1
    assert report.devices[0].count == 5 and "(5 composants)" in report.devices[0].name


def test_problems_and_disabled_devices():
    broken = ProblemDevice("Contrôleur inconnu", 28, r"PCI\VEN_8086&DEV_1234\1", [r"PCI\VEN_8086&DEV_1234"])
    disabled = ProblemDevice("VirtualBox", 22, r"ROOT\NET\0002", [])
    report = analyse([], [], [broken, disabled], now=NOW)
    assert [d.name for d in report.devices] == ["Contrôleur inconnu"]
    assert report.devices[0].status == driver_analysis.PROBLEM


def test_report_sorted_by_urgency():
    update = DriverUpdate("u1", "Intel net", "Intel", "AX201", "Networking", None, 1, False,
                          hardware_id=r"PCI\VEN_8086&DEV_7A70&SUBSYS_00748086")
    report = analyse([*CHIPSET, LAN, WIFI], [update], [], now=NOW)
    order = [driver_analysis.STATUS_ORDER.index(d.status) for d in report.devices]
    assert order == sorted(order)


# --------------------------------------------------------------------------- conseils matériels

def test_ram_advice():
    gb = 1024 ** 3
    low = hardware.ram_advice(4 * gb, 1, 2)
    assert low[0].level == "warn"
    assert any("double canal" in a.text for a in low)
    ok = hardware.ram_advice(32 * gb, 2, 4)
    assert ok[0].level == "ok" and any("2 emplacement" in a.text for a in ok)


def test_storage_and_gpu_advice():
    assert any("SSD" in a.text for a in hardware.storage_advice("Disque dur (HDD)", 0, 0.5))
    assert hardware.storage_advice("SSD NVMe", 2, 0.5)[0].level == "warn"
    assert any(a.level == "warn" for a in hardware.storage_advice("SSD", 0, 0.05))
    assert hardware.gpu_advice(90, 80)[0].level == "warn"
    assert any("0 %" in a.text for a in hardware.gpu_advice(45, 0))


def test_notify_structure_size():
    import ctypes

    from app.utils import notify

    expected = 976 if ctypes.sizeof(ctypes.c_void_p) == 8 else 956
    assert ctypes.sizeof(notify.NOTIFYICONDATAW) == expected
