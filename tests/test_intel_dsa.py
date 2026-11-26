"""Source Intel (liste publique d'Intel Driver & Support Assistant) et installateurs officiels vérifiés.

Sans réseau : la liste Intel est reconstituée ici au format réel de dsadata.intel.com.
"""

from __future__ import annotations

import hashlib
import io
import json
import zipfile
from datetime import datetime

import pytest

from app.core import driver_analysis, packages
from app.core.driver_analysis import analyse
from app.core.driver_sources import intel_dsa
from app.core.driver_sources.base import DeviceRef, SystemContext
from app.core.drivers import InstalledDriver

NOW = datetime(2026, 11, 26, 10, 0)
W11 = SystemContext(windows_build=26100, windows_release="24H2")
W11_SYS = "windows-11-24h2-64"


def entry(id_, name, version, components, systems=(W11_SYS,), size=1000, beta=False, intel_only=False,
          boards=(), date="2026-10-20T00:00:00Z"):
    return {"Id": id_, "Version": version, "DisplayReleaseDate": date, "Name": name, "Details": "",
            "ExtendedDetails": "<p>…</p>", "Url": f"https://www.intel.com/content/www/us/en/download/{id_}/x.html",
            "IntelSystemOnly": intel_only, "ForceReboot": False, "InstallOptions": "", "GroupId": "1",
            "AllowSilentInstallation": False, "AllowAutomaticInstallation": False, "IsCritical": False,
            "IsBeta": beta, "BoardNumbers": list(boards), "ModelNumbers": [],
            "Files": [{"Url": f"https://downloadmirror.intel.com/{id_}/setup_{id_}.exe", "Hash": "ab" * 20,
                       "Size": size, "OperatingSystems": list(systems)}],
            "Components": components}


def comp(category, version, values, detection="Default"):
    return {"Category": category, "Version": version, "VersionDisplayMask": None, "VersionComparisonMask": None,
            "RevisionDate": "1970-01-01T00:00:00Z", "DetectionType": detection, "DetectionValues": values,
            "DchDriver": False, "OperatingSystemOverrides": []}


FEED = [
    entry(1, "Intel® Wireless Wi-Fi Drivers for Windows® 10 and Windows 11*", "24.80.0",
          [comp("Wireless", "24.80.0.2", ["VEN_8086&DEV_2723&SUBSYS_*8086", "VEN_8086&DEV_7A70"])]),
    entry(2, "Intel® Wireless Wi-Fi Drivers (ancienne)", "23.40.0",
          [comp("Wireless", "23.40.0.4", ["VEN_8086&DEV_7A70"])]),
    entry(3, "Intel® Wireless Wi-Fi Drivers (bêta)", "25.0.0", [comp("Wireless", "25.0.0.1", ["VEN_8086&DEV_7A70"])],
          beta=True),
    entry(4, "Intel® Wireless Bluetooth® Drivers", "24.80.0",
          [comp("Bluetooth", "24.80.0.2", ["VID_8087&PID_0026", "VID_8087&PID_0029"])]),
    entry(5, "Intel® Arc™ Graphics - Windows*", "32.0.101.9175 WHQL Certified",
          [comp("Graphics", "32.0.101.9175", ["VEN_8086&DEV_E211", "VEN_8086&DEV_A7A0"])], size=900_000_000),
    entry(6, "Intel® Arc™ Pro Graphics - Windows*", "32.0.101.8976 - Q3.26",
          [comp("Graphics", "32.0.101.8976", ["VEN_8086&DEV_E211"])]),
    entry(7, "Intel® Killer™ Performance Suite", "50.26.915.2621",
          [comp("OtherDownloads", "50.26.915", ["A523549E-6218-494F-AFD9-67122B3DA898"], "Guid"),
           comp("Wireless", "24.80.0.2", ["VEN_8086&DEV_2723&SUBSYS_*1A56"])], size=400_000_000),
    entry(8, "Intel® Wireless Wi-Fi Drivers (Killer)", "24.80.0",
          [comp("Wireless", "24.80.0.2", ["VEN_8086&DEV_2723&SUBSYS_*1A56"])], size=50_000_000),
    entry(9, "Pilote réservé aux NUC", "9.9", [comp("LAN", "99.0.0.1", ["VEN_8086&DEV_15F3"])], intel_only=True),
    entry(10, "Intel® Network Adapter Driver for Windows® 10", "31.3",
          [comp("LAN", "12.19.2.68", ["VEN_8086&DEV_15F3"])], systems=("windows-10-22h2-64",)),
]


def dev(name, device_id, version, hardware_ids=(), compatible_ids=(), cls="Net"):
    return DeviceRef(key=device_id, name=name, device_id=device_id, hardware_id=device_id.rsplit("\\", 1)[0],
                     version=version, device_class=cls, vendor_name="Intel", provider="Intel",
                     hardware_ids=tuple(hardware_ids), compatible_ids=tuple(compatible_ids))


AX201 = dev("Intel(R) Wi-Fi 6 AX201", r"PCI\VEN_8086&DEV_7A70&SUBSYS_00748086\3&1", "23.130.0.5",
            [r"PCI\VEN_8086&DEV_7A70&SUBSYS_00748086&REV_20", r"PCI\VEN_8086&DEV_7A70&SUBSYS_00748086"],
            [r"PCI\VEN_8086&DEV_7A70&REV_20", r"PCI\VEN_8086&DEV_7A70", r"PCI\VEN_8086&CC_028000"])
AX200 = dev("Intel(R) Wi-Fi 6 AX200 160MHz", r"PCI\VEN_8086&DEV_2723&SUBSYS_00848086\4&2", "24.80.0.2",
            [r"PCI\VEN_8086&DEV_2723&SUBSYS_00848086&REV_1A", r"PCI\VEN_8086&DEV_2723&SUBSYS_00848086"])
KILLER = dev("Killer(R) Wi-Fi 6 AX1650", r"PCI\VEN_8086&DEV_2723&SUBSYS_16511A56\4&2", "22.250.0.2",
             [r"PCI\VEN_8086&DEV_2723&SUBSYS_16511A56&REV_1A", r"PCI\VEN_8086&DEV_2723&SUBSYS_16511A56"])
BT = dev("Intel(R) Wireless Bluetooth(R)", r"USB\VID_8087&PID_0026\5&1", "23.60.5.10",
         [r"USB\VID_8087&PID_0026&REV_0002", r"USB\VID_8087&PID_0026"], cls="Bluetooth")
ARC_PRO = dev("Intel(R) Arc(TM) Pro B60 Graphics", r"PCI\VEN_8086&DEV_E211&SUBSYS_10008086\4&1", "32.0.101.6000",
              [r"PCI\VEN_8086&DEV_E211&SUBSYS_10008086"], [r"PCI\VEN_8086&DEV_E211"], cls="Display")
ARC = dev("Intel(R) Arc(TM) B580 Graphics", r"PCI\VEN_8086&DEV_E211&SUBSYS_20008086\4&1", "32.0.101.6000",
          [r"PCI\VEN_8086&DEV_E211&SUBSYS_20008086"], [r"PCI\VEN_8086&DEV_E211"], cls="Display")
ETH = dev("Intel(R) Ethernet Controller I225-V", r"PCI\VEN_8086&DEV_15F3&SUBSYS_00008086\4&1", "1.1.4.40",
          [r"PCI\VEN_8086&DEV_15F3&SUBSYS_00008086"])


@pytest.fixture
def catalog():
    return intel_dsa.parse_configurations(FEED)


def known(catalog):
    return {s for p in catalog for f in p.files for s in f.systems}


def best(device, catalog, system=W11_SYS):
    return intel_dsa.find_candidate(device, catalog, system, known(catalog))


# --------------------------------------------------------------------------- lecture

def test_parse_feed_reads_only_the_json():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("DSARules.dll", b"MZ\x90\x00 ne doit jamais etre lu")
        archive.writestr("software-configurations.json", json.dumps(FEED))
    parsed = intel_dsa.parse_feed(buffer.getvalue())
    assert len(parsed) == len(FEED)
    killer = next(p for p in parsed if p.id == 7)
    assert [c.category for c in killer.components] == ["Wireless"]       # détection « Guid » ignorée
    assert next(p for p in parsed if p.id == 9).restricted


def test_windows_id():
    assert intel_dsa.windows_id(W11, "AMD64") == "windows-11-24h2-64"
    assert intel_dsa.windows_id(SystemContext(windows_build=19045, windows_release="22H2"), "X86") == \
        "windows-10-22h2-32"
    assert intel_dsa.windows_id(W11, "ARM64") == ""                        # Intel ne publie rien pour ARM


# --------------------------------------------------------------------------- choix du pilote

def test_newest_matching_driver_is_chosen(catalog):
    c = best(AX201, catalog)
    assert c.package.id == 1 and c.component.version == "24.80.0.2"      # pas l'ancienne, pas la bêta


def test_wildcard_subsys_and_usb_ids(catalog):
    assert best(AX200, catalog).package.id == 1                            # SUBSYS_*8086
    assert best(BT, catalog).package.id == 4                               # VID_8087&PID_0026 (USB)


def test_killer_prefers_driver_only_package(catalog):
    # Même version dans la suite Killer (400 Mo) et le paquet pilote seul (50 Mo) : le plus léger.
    assert best(KILLER, catalog).package.id == 8


def test_pro_and_consumer_graphics_are_not_mixed(catalog):
    assert best(ARC_PRO, catalog).package.id == 6
    assert best(ARC, catalog).package.id == 5


def test_os_and_restricted_packages_are_filtered(catalog):
    assert best(ETH, catalog) is None                                      # paquet Windows 10 uniquement / NUC
    assert best(ETH, catalog, "windows-10-22h2-64").package.id == 10


def test_unknown_newer_windows_uses_same_generation(catalog):
    assert best(AX201, catalog, "windows-11-26h1-64").package.id == 1
    assert best(AX201, catalog, "windows-10-22h2-64") is None              # version connue mais non prévue


def test_different_numbering_is_not_compared(catalog):
    odd = dev("Intel Wi-Fi", AX201.device_id, "1.0", AX201.hardware_ids, AX201.compatible_ids)
    assert best(odd, catalog) is None


# --------------------------------------------------------------------------- résultat de la vérification

def test_check_newer_gives_verified_package(catalog):
    check = intel_dsa.make_check(AX201, best(AX201, catalog), NOW)
    assert check.update_available and check.comparable
    pkg = check.extra
    assert isinstance(pkg, packages.VendorPackage)
    assert pkg.download_url == "https://downloadmirror.intel.com/1/setup_1.exe"
    assert pkg.allowed_hosts == ("downloadmirror.intel.com",) and pkg.publisher == "Intel Corporation"
    assert pkg.sha1 == "ab" * 20 and pkg.version == "24.80.0.2"


def test_check_up_to_date(catalog):
    check = intel_dsa.make_check(AX200, best(AX200, catalog), NOW)
    assert not check.update_available and check.extra is None and check.latest_version == "24.80.0.2"


def test_check_not_offered_again_when_windows_kept_old_driver(catalog):
    attempts = {AX201.key: {"offered": "24.80.0.2", "before": "23.130.0.5"}}
    check = intel_dsa.make_check(AX201, best(AX201, catalog), NOW, attempts)
    assert not check.update_available and check.generic_newer and "déjà été installé" in check.note


def test_analysis_offers_automatic_install(catalog):
    installed = InstalledDriver(device_name=AX201.name, device_class="Net", manufacturer="Intel", provider="Intel",
                                version=AX201.version, date=datetime(2025, 1, 1), inf_name="oem3.inf",
                                is_signed=True, device_id=AX201.device_id, hardware_id=AX201.hardware_id)
    check = intel_dsa.make_check(AX201, best(AX201, catalog), NOW)
    row = analyse([installed], [], [], {AX201.key: [check]}, W11, now=NOW).devices[0]
    assert row.status == driver_analysis.VENDOR_UPDATE and row.can_auto_install
    assert row.package is check.extra
    assert "downloadmirror.intel.com" in row.explanation
    assert row.best_link.url == "https://downloadmirror.intel.com/1/setup_1.exe"


def test_source_note_when_intel_has_nothing():
    source = intel_dsa.IntelSource()
    assert "ne propose aucun pilote" in source.note_for(W11, AX201, ["Intel"])
    assert "n'a pas pu être consultée" in source.note_for(W11, AX201, [])


# --------------------------------------------------------------------------- installateurs vérifiés

def make_pkg(**kw):
    base = dict(vendor="Intel", title="t", version="1.0", download_url="https://downloadmirror.intel.com/1/a.exe",
                allowed_hosts=("downloadmirror.intel.com",), publisher="Intel Corporation")
    base.update(kw)
    return packages.VendorPackage(**base)


def test_hash_check(tmp_path):
    f = tmp_path / "a.exe"
    f.write_bytes(b"installateur")
    packages.check_hash(f, make_pkg(sha1=hashlib.sha1(b"installateur").hexdigest().upper()))
    packages.check_hash(f, make_pkg())                                   # aucune empreinte publiée
    with pytest.raises(packages.VerificationError):
        packages.check_hash(f, make_pkg(sha1="00" * 20))
    with pytest.raises(packages.VerificationError):
        packages.check_hash(f, make_pkg(sha256="00" * 32, sha1=hashlib.sha1(b"installateur").hexdigest()))


def test_remember_install(tmp_path):
    path = tmp_path / "attempts.json"
    packages.remember_install(make_pkg(version="24.80.0.2"), [("dev1", "23.130.0.5")], path, NOW)
    data = packages.load_attempts(path)
    assert packages.already_tried(data, "dev1", "24.80.0.2", "23.130.0.5")
    assert not packages.already_tried(data, "dev1", "24.90.0.1", "23.130.0.5")   # nouvelle version : reproposée
    assert not packages.already_tried(data, "dev1", "24.80.0.2", "24.80.0.2")
    assert not packages.already_tried(data, "dev2", "24.80.0.2", "23.130.0.5")


def test_nvidia_driver_becomes_package():
    from app.core.nvidia import NvidiaDriver

    nv = NvidiaDriver("581.57", "2026-10-01", "https://us.download.nvidia.com/x/581.57.exe",
                      "https://www.nvidia.com/fr-fr/drivers/details/1/", "890 Mo", "GeForce Game Ready")
    pkg = nv.package()
    assert pkg.publisher == "NVIDIA Corporation" and pkg.size_text == "890 Mo"
    assert pkg.host == "us.download.nvidia.com"
