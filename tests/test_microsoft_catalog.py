"""Tests du Catalogue Microsoft Update (sans réseau) : lecture des pages, règles de Windows, analyse."""

from __future__ import annotations

from datetime import datetime

from app.core import driver_analysis
from app.core.driver_analysis import analyse
from app.core.driver_sources import microsoft_catalog as mc
from app.core.driver_sources.base import DeviceRef
from app.core.drivers import DeviceIds, InstalledDriver

NOW = datetime(2026, 11, 25, 18, 0)

# --------------------------------------------------------------------------- pages (extraits réels)

SEARCH_PAGE = """
<table id="ctl00_catalogBody_updateMatches">
<tr id="headerRow"><th>Title</th></tr>
<tr id="c426f458-47c5-48da-9a13-8008d41c4d23_R0" style="border-width:0px;">
  <td class="resultsbottomBorder" id="c426f458-47c5-48da-9a13-8008d41c4d23_C0_R0"></td>
  <td class="resultsbottomBorder resultspadding" id="c426f458-47c5-48da-9a13-8008d41c4d23_C1_R0">
    <a id='c426f458-47c5-48da-9a13-8008d41c4d23_link' href="javascript:void(0);">
      Killer Net Driver Update (1168.28.50.1224)
    </a></td>
  <td class="resultsbottomBorder resultspadding" id="c426f458-47c5-48da-9a13-8008d41c4d23_C2_R0">
    Windows 11 Client,  version 24H2 and later,  Servicing Drivers</td>
  <td class="resultsbottomBorder resultspadding" id="c426f458-47c5-48da-9a13-8008d41c4d23_C3_R0">
    Drivers (Networking)</td>
  <td class="resultsbottomBorder resultspadding " id="c426f458-47c5-48da-9a13-8008d41c4d23_C4_R0">12/23/2025</td>
  <td class="resultsbottomBorder resultspadding" id="c426f458-47c5-48da-9a13-8008d41c4d23_C5_R0">1168.28.50.1224</td>
  <td class="resultsbottomBorder resultspadding resultsSizeWidth" id="c426f458-47c5-48da-9a13-8008d41c4d23_C6_R0">
    <span id="c426f458-47c5-48da-9a13-8008d41c4d23_size">306 KB</span>
    <span class="noDisplay" id="c426f458-47c5-48da-9a13-8008d41c4d23_originalSize">313344</span></td>
</tr><tr id="de8268fb-0cfa-49ce-98e7-264ad0c72a38_R1" style="border-width:0px;">
  <td class="resultsbottomBorder resultspadding" id="de8268fb-0cfa-49ce-98e7-264ad0c72a38_C1_R1">
    <a id='de8268fb-0cfa-49ce-98e7-264ad0c72a38_link'>Acer Incorporated - HIDClass - 1.0.0.12</a></td>
  <td class="resultsbottomBorder resultspadding" id="de8268fb-0cfa-49ce-98e7-264ad0c72a38_C2_R1">
    Windows 11 Client, version 24H2 and later, Servicing Drivers</td>
  <td class="resultsbottomBorder resultspadding" id="de8268fb-0cfa-49ce-98e7-264ad0c72a38_C3_R1">Drivers (Other Hardware)</td>
  <td class="resultsbottomBorder resultspadding " id="de8268fb-0cfa-49ce-98e7-264ad0c72a38_C4_R1">6/1/2024</td>
  <td class="resultsbottomBorder resultspadding" id="de8268fb-0cfa-49ce-98e7-264ad0c72a38_C5_R1">n/a</td>
</tr></table>
"""

DETAILS_PAGE = """
<span id="ScopedViewHandler_titleText">Intel Corporation Bluetooth Driver Update (24.80.0.2)</span>
<div id="archDiv">
    <span id="ScopedViewHandler_labelArchitecture_Separator" class="labelTitle">Architecture:</span>
        AMD64
</div>
<div id="productsDiv">
    <span id="ScopedViewHandler_labelSupportedProducts_Separator" class="labelTitle">Supported products:</span>
        Windows 11 Client, version 25H2 and later, Servicing Drivers
</div>
<span id="ScopedViewHandler_size">35 MB</span>
<span id="ScopedViewHandler_company">Intel Corporation</span>
<span id="ScopedViewHandler_manufacturer">Intel Corporation</span>
<span id="ScopedViewHandler_driverClass">Bluetooth</span>
<span id="ScopedViewHandler_driverModel">Intel(R) Wireless Bluetooth(R)</span>
<span id="ScopedViewHandler_driverProvider">Intel Corporation</span>
<span id="ScopedViewHandler_version">24.80.0.2</span>
<span id="ScopedViewHandler_versionDate">9/1/2026</span>
<div id="packageBox" style="display: none">
    <div id="ScopedViewHandler_driverPackage">
        <div class="tabBodyContentBox tabDriverPackageBodyContentBox boxTitleContainer">
            <span id="ScopedViewHandler_labelSupportedHWIDs" class="boxTitleDetails">Supported Hardware IDs</span>
            <div class="tabDriverPackageContentBox" id="driverhwIDs" TABINDEX="1">
                    <div style="padding-bottom: 0.3em;">
                        usb\\vid_8087&amp;pid_0026&amp;rev_0000
                    </div>
                    <div style="padding-bottom: 0.3em;">
                        usb\\vid_8087&amp;pid_0026&amp;rev_0002
                    </div>
            </div>
        </div>
        <div class="tabBodyContentBox tabDriverPackageBodyContentBox boxTitleContainer">
            <span id="ScopedViewHandler_labelSupportedUpdateIDs" class="boxTitleDetails">Supported Update IDs</span>
            <div class="tabDriverPackageContentBox" id="driverupdateIDs" TABINDEX="1">
                    <div style="padding-bottom: 0.3em;">27228977-8b86-426f-9d2a-697e9da8e369</div>
            </div>
        </div>
    </div>
</div>
"""

DOWNLOAD_PAGE = """
downloadInformation[0].files[0] = new Object();
downloadInformation[0].files[0].url = 'https://catalog.s.download.windowsupdate.com/d/msdownload/update/driver/drvs/2024/07/x_y.cab';
downloadInformation[0].files[0].digest = 'yfIMVudKMNtXbTvo/arxr6hETrM=';
downloadInformation[0].files[0].sha256 = 'nx/Fjt6rUxxIvsPgADSFTfU4chJ4x3dv/p+d7hHC4fY=';
"""


def test_parse_search():
    killer, acer = mc.parse_search(SEARCH_PAGE)
    assert killer.update_id == "c426f458-47c5-48da-9a13-8008d41c4d23"
    assert killer.title == "Killer Net Driver Update (1168.28.50.1224)"
    assert killer.products == "Windows 11 Client, version 24H2 and later, Servicing Drivers"
    assert killer.version == "1168.28.50.1224" and killer.size == 313344 and killer.last_updated == "12/23/2025"
    assert acer.version == "" and mc.entry_version(acer) == "1.0.0.12"     # « n/a » : version lue dans le titre


def test_parse_details_reads_every_hardware_id():
    drv = mc.parse_details(DETAILS_PAGE, "27228977-8B86-426F-9D2A-697E9DA8E369")
    assert drv.update_id == "27228977-8b86-426f-9d2a-697e9da8e369"
    assert drv.version == "24.80.0.2" and drv.driver_date == datetime(2026, 9, 1)
    assert drv.architecture == "AMD64"
    assert drv.products == "Windows 11 Client, version 25H2 and later, Servicing Drivers"
    # Le dernier identifiant est suivi de plusieurs </div> : il ne doit pas être perdu.
    assert drv.hardware_ids == ["usb\\vid_8087&pid_0026&rev_0000", "usb\\vid_8087&pid_0026&rev_0002"]
    assert drv.provider == "Intel Corporation" and drv.company == "Intel Corporation"
    assert drv.size == 35 * 1024 ** 2
    assert drv.page_url.endswith("updateid=27228977-8b86-426f-9d2a-697e9da8e369")


def test_parse_download_dialog():
    files = mc.parse_download_dialog(DOWNLOAD_PAGE)
    assert files == [("https://catalog.s.download.windowsupdate.com/d/msdownload/update/driver/drvs/2024/07/x_y.cab",
                      "nx/Fjt6rUxxIvsPgADSFTfU4chJ4x3dv/p+d7hHC4fY=")]


# --------------------------------------------------------------------------- périphériques

def dev_ref(name="Intel(R) Wireless Bluetooth(R)", version="21.70.0.3", date=datetime(2019, 12, 30),
            hardware=("USB\\VID_8087&PID_0026&REV_0002", "USB\\VID_8087&PID_0026"),
            compatible=("USB\\Class_E0&SubClass_01", "USB\\Class_E0"), matching="USB\\VID_8087&PID_0026&REV_0002",
            provider="Intel Corporation", vendor="Intel", cls="Bluetooth", device_id="USB\\VID_8087&PID_0026\\5&1"):
    return DeviceRef(key=device_id, name=name, device_id=device_id, hardware_id=hardware[0] if hardware else "",
                     version=version, device_class=cls, vendor_name=vendor, date=date, provider=provider,
                     hardware_ids=tuple(hardware), compatible_ids=tuple(compatible), matching_id=matching)


KILLER = dev_ref("Killer E2600 Gigabit Ethernet Controller", "10.38.1118.2019", datetime(2019, 11, 18),
                 ("PCI\\VEN_10EC&DEV_2600&SUBSYS_143F1025&REV_21", "PCI\\VEN_10EC&DEV_2600&SUBSYS_143F1025",
                  "PCI\\VEN_10EC&DEV_2600&CC_020000", "PCI\\VEN_10EC&DEV_2600&CC_0200"),
                 ("PCI\\VEN_10EC&DEV_2600&REV_21", "PCI\\VEN_10EC&DEV_2600"), "PCI\\VEN_10EC&DEV_2600&REV_21",
                 provider="Rivet", vendor="Realtek", cls="Net",
                 device_id="PCI\\VEN_10EC&DEV_2600&SUBSYS_143F1025&REV_21\\01")
UHD = dev_ref("Intel(R) UHD Graphics", "27.20.100.8280", datetime(2020, 5, 19),
              ("PCI\\VEN_8086&DEV_9BC4&SUBSYS_143F1025&REV_05", "PCI\\VEN_8086&DEV_9BC4&SUBSYS_143F1025"),
              ("PCI\\VEN_8086&DEV_9BC4&REV_05", "PCI\\VEN_8086&DEV_9BC4"), "PCI\\VEN_8086&DEV_9BC4&SUBSYS_143F1025",
              provider="Intel Corporation", vendor="Intel", cls="Display",
              device_id="PCI\\VEN_8086&DEV_9BC4&SUBSYS_143F1025&REV_05\\3&1")
ACPI_DTT = dev_ref("Intel(R) Dynamic Tuning Generic Participant", "8.7.10200.12510", datetime(2019, 5, 11),
                   ("ACPI\\VEN_INT&DEV_3403", "ACPI\\INT3403", "*INT3403"), (), "ACPI\\INT3403",
                   provider="Intel", vendor="Intel", cls="System", device_id="ACPI\\INT3403\\SEN3")


def catalog_driver(version, date, hwids, uid="u1", provider="Intel Corporation", arch="AMD64",
                   products="Windows 11 Client, version 22H2 and later, Servicing Drivers", **kw):
    return mc.CatalogDriver(update_id=uid, title=f"Driver Update ({version})", version=version, date=date,
                            architecture=arch, products=products, hardware_ids=[h.lower() for h in hwids],
                            provider=provider, manufacturer=provider, **kw)


def test_search_terms():
    # L'identifiant exact (avec le SUBSYS du fabricant du PC) d'abord, puis la puce seule.
    assert mc.search_terms(UHD) == ["PCI\\VEN_8086&DEV_9BC4&SUBSYS_143F1025", "PCI\\VEN_8086&DEV_9BC4"]
    assert mc.search_terms(dev_ref()) == ["USB\\VID_8087&PID_0026"]
    # ACPI : « VEN_INT&DEV_3403 » est inconnu du catalogue, « ACPI\INT3403 » non ; « *INT3403 » est ignoré.
    assert mc.search_terms(ACPI_DTT) == ["ACPI\\VEN_INT&DEV_3403", "ACPI\\INT3403"]


def test_match_index_follows_windows_order():
    drv = catalog_driver("1168.28.50.1224", "12/24/2025", ["pci\\ven_10ec&dev_2600&rev_21"])
    assert mc.match_index(drv, KILLER) == 4           # identifiant compatible (après les 4 identifiants matériels)
    assert mc.installed_index(KILLER) == 4            # le pilote installé utilise le même
    unknown = dev_ref(matching="")
    assert mc.installed_index(unknown) == 0           # inconnu : on suppose le plus précis (prudence)


def test_supports_windows():
    assert mc.supports_windows("Windows 11 Client, version 24H2 and later, Servicing Drivers", 26200)
    assert not mc.supports_windows("Windows 11 Client, version 25H2 and later, Servicing Drivers", 26100)
    assert mc.supports_windows("Windows 10, Vibranium and later, Servicing Drivers", 26200)
    assert mc.supports_windows("Windows 10 version 1803 and Later Servicing Drivers", 19045)
    assert mc.supports_windows("Windows - Client, version 21H2 and later, Servicing Drivers", 22000)
    assert not mc.supports_windows("Windows 11 Client, version 30H2 and later", 26200)    # version future inconnue
    assert not mc.supports_windows("Surface Hub 2S drivers", 26200)
    assert not mc.supports_windows("Windows Server 2022 and later, Servicing Drivers", 26200)
    assert mc.supports_windows("Windows 11 Client, version 26H1 and later", 0)            # Windows inconnu


def test_supports_architecture():
    assert mc.supports_architecture("AMD64", "AMD64")
    assert not mc.supports_architecture("ARM64", "AMD64")
    assert mc.supports_architecture("AMD64, X86", "AMD64")
    assert mc.supports_architecture("", "AMD64") and mc.supports_architecture("n/a", "ARM64")


def test_same_maker():
    elan = catalog_driver("24.12.4.1", "5/8/2020", [], provider="ELAN", company="ELAN Microelectronic Corp.")
    smbus = dev_ref("Intel(R) SMBus - 06A3", provider="INTEL", vendor="Intel")
    assert not mc.same_maker(elan, smbus)            # pilote ELAN déclarant le SMBus Intel : écarté
    killer = catalog_driver("1168.28.50.1224", "12/24/2025", [], provider="Killer",
                            company="Realtek Semiconductor Corp")
    assert mc.same_maker(killer, KILLER)             # « Rivet Networks » = Killer
    assert mc.family("Intel(R) Corporation") == mc.family("INTEL") == "intel"


def test_is_newer_like_windows():
    bt = dev_ref()
    assert mc.is_newer(catalog_driver("24.80.0.2", "9/1/2026", []), bt)
    assert not mc.is_newer(catalog_driver("21.70.0.3", "1/1/2026", []), bt)     # même version : jamais « plus récent »
    assert not mc.is_newer(catalog_driver("22.0.0.1", "1/1/2018", []), bt)      # date plus ancienne : Windows garde l'actuel
    chipset = dev_ref(version="10.1.31.2", date=datetime(1968, 7, 18))
    assert mc.is_newer(catalog_driver("10.1.31.3", "7/18/1968", []), chipset)    # même date : la version départage


# --------------------------------------------------------------------------- conclusions

def test_evaluate_update_windows_would_choose():
    drv = catalog_driver("24.80.0.2", "9/1/2026", ["usb\\vid_8087&pid_0026&rev_0002"])
    check = mc.evaluate(dev_ref(), [mc.Candidate(drv, 0)], NOW)
    assert check.update_available and check.comparable and check.extra is drv
    assert check.source == mc.SOURCE_LABEL and check.latest_version == "24.80.0.2"


def test_evaluate_less_precise_newer_is_only_signalled():
    generic = catalog_driver("31.0.101.2141", "3/30/2026", ["pci\\ven_8086&dev_9bc4"], uid="g")
    acer = catalog_driver("27.20.100.8280", "5/19/2020", ["pci\\ven_8086&dev_9bc4&subsys_143f1025"], uid="a")
    check = mc.evaluate(UHD, [mc.Candidate(generic, 5), mc.Candidate(acer, 1)], NOW)
    assert not check.update_available and check.comparable and check.generic_newer
    assert check.latest_version == "31.0.101.2141" and check.extra is None
    assert "version générique plus récente" in check.note


def test_evaluate_verified_and_newer_than_catalog():
    old = catalog_driver("1.3.38.34", "6/8/2020", ["hdaudio\\func_01&ven_10de&dev_0099"])
    nv_audio = dev_ref("NVIDIA High Definition Audio", "1.4.6.6", datetime(2026, 8, 25),
                       ("HDAUDIO\\FUNC_01&VEN_10DE&DEV_0099&SUBSYS_10251440",), ("HDAUDIO\\FUNC_01&VEN_10DE&DEV_0099",),
                       "HDAUDIO\\FUNC_01&VEN_10DE&DEV_0099&SUBSYS_10251440", provider="NVIDIA", vendor="NVIDIA")
    check = mc.evaluate(nv_audio, [mc.Candidate(old, 1)], NOW)
    assert not check.update_available and check.comparable and not check.generic_newer
    assert "même plus récente" in check.note
    assert mc.evaluate(nv_audio, [], NOW) is None


def test_evaluate_driver_already_installed_but_not_kept_is_not_proposed_again():
    drv = catalog_driver("24.80.0.2", "9/1/2026", ["usb\\vid_8087&pid_0026&rev_0002"], uid="bt")
    check = mc.evaluate(dev_ref(), [mc.Candidate(drv, 0)], NOW, {"bt": {"version": "24.80.0.2",
                                                                         "when": "2026-11-25T18:00:00"}})
    assert not check.update_available and not check.comparable
    assert "déjà été installée par l'application le 2026-11-25" in check.note


def test_evaluate_smart_sound_is_never_installed_alone():
    sst = dev_ref("Contrôleur audio de la technologie Intel(R) Smart Sound", "10.25.0.3488", datetime(2020, 1, 23),
                  ("PCI\\VEN_8086&DEV_06C8&SUBSYS_143F1025&REV_00",), ("PCI\\VEN_8086&DEV_06C8",),
                  "PCI\\VEN_8086&DEV_06C8", cls="System", device_id="PCI\\VEN_8086&DEV_06C8\\3&1")
    drv = catalog_driver("10.25.0.8130", "10/17/2022", ["pci\\ven_8086&dev_06c8"])
    check = mc.evaluate(sst, [mc.Candidate(drv, 1)], NOW)
    assert not check.update_available and not check.comparable and "Smart Sound" in check.note


# --------------------------------------------------------------------------- recherche (réseau simulé)

def test_find_candidates_prefers_exact_search_and_skips_other_pcs():
    searches = {
        "PCI\\VEN_8086&DEV_9BC4&SUBSYS_143F1025": [mc.CatalogEntry("acer", "Intel - Display - 27.20.100.8280",
                                                    "Windows 10, Vibranium and later", "", "5/19/2020", "")],
        "PCI\\VEN_8086&DEV_9BC4": [mc.CatalogEntry(f"dell{i}", "Intel Display Driver Update (31.0.101.2141)",
                                                   "Windows 11 Client, version 22H2 and later", "", "3/30/2026",
                                                   "31.0.101.2141") for i in range(3)],
    }
    pages = {
        "acer": catalog_driver("27.20.100.8280", "5/19/2020", ["pci\\ven_8086&dev_9bc4&subsys_143f1025"], uid="acer"),
        # La même version, publiée pour des PC Dell : identifiants différents, à ignorer.
        "dell0": catalog_driver("31.0.101.2141", "3/30/2026", ["pci\\ven_8086&dev_9bc4&subsys_09841028"], uid="dell0"),
        "dell1": catalog_driver("31.0.101.2141", "3/30/2026", ["pci\\ven_8086&dev_9bc4"], uid="dell1", arch="ARM64"),
        "dell2": catalog_driver("31.0.101.2141", "3/30/2026", ["pci\\ven_8086&dev_9bc4"], uid="dell2"),
    }
    calls = []

    def fake_details(uid):
        calls.append(uid)
        return pages[uid]

    found = mc.find_candidates(UHD, 26200, "AMD64", search_fn=lambda t: searches.get(t, []),
                               details_fn=fake_details)
    assert [(c.driver.update_id, c.index) for c in found] == [("acer", 1), ("dell2", 3)]
    assert calls == ["acer", "dell0", "dell1", "dell2"]
    check = mc.evaluate(UHD, found, NOW)
    assert check.generic_newer and not check.update_available


def test_find_candidates_rejects_other_maker():
    smbus = dev_ref("Intel(R) SMBus - 06A3", "10.1.31.2", datetime(1968, 7, 18),
                    ("PCI\\VEN_8086&DEV_06A3&SUBSYS_143F1025&REV_00",), ("PCI\\VEN_8086&DEV_06A3",),
                    "PCI\\VEN_8086&DEV_06A3", provider="INTEL", cls="System", device_id="PCI\\VEN_8086&DEV_06A3\\3&1")
    entries = [mc.CatalogEntry("elan", "ELAN SMBus - System - 24.12.4.1", "Windows 10, Vibranium and later", "",
                               "5/7/2020", ""),
               mc.CatalogEntry("intel", "INTEL System Driver Update (10.1.31.3)",
                               "Windows 11 Client, version 22H2 and later", "", "7/17/1968", "10.1.31.3")]
    pages = {"elan": catalog_driver("24.12.4.1", "5/8/2020", ["pci\\ven_8086&dev_06a3"], uid="elan", provider="ELAN"),
             "intel": catalog_driver("10.1.31.3", "7/18/1968", ["pci\\ven_8086&dev_06a3"], uid="intel",
                                     provider="INTEL")}
    found = mc.find_candidates(smbus, 26200, "AMD64", search_fn=lambda t: entries if "SUBSYS" not in t else [],
                               details_fn=pages.__getitem__)
    assert [c.driver.update_id for c in found] == ["intel"]
    assert mc.evaluate(smbus, found, NOW).latest_version == "10.1.31.3"


# --------------------------------------------------------------------------- analyse complète

def installed(name, device_id, version, date, provider="INTEL", cls="System"):
    return InstalledDriver(device_name=name, device_class=cls, manufacturer=provider, provider=provider,
                           version=version, date=date, inf_name="oem7.inf", is_signed=True, device_id=device_id,
                           hardware_id=device_id.rsplit("\\", 1)[0])


def test_analysis_catalog_update_is_auto_installable_and_grouped():
    smbus = installed("Intel(R) SMBus - 06A3", "PCI\\VEN_8086&DEV_06A3&SUBSYS_143F1025&REV_00\\3&1", "10.1.31.2",
                      datetime(1968, 7, 18))
    spi = installed("Intel(R) SPI (flash) Controller - 06A4", "PCI\\VEN_8086&DEV_06A4&SUBSYS_143F1025&REV_00\\3&2",
                    "10.1.31.2", datetime(1968, 7, 18))
    mei = installed("Intel(R) Management Engine Interface", "PCI\\VEN_8086&DEV_06E0&SUBSYS_143F1025&REV_00\\3&3",
                    "1952.14.0.1470", datetime(2019, 12, 25))
    chipset_inf = catalog_driver("10.1.31.3", "7/18/1968", [], uid="inf", size=20000)
    mei_drv = catalog_driver("2603.9.4.0", "1/13/2026", [], uid="mei")

    def update(drv):
        return mc.VendorCheck(mc.SOURCE_LABEL, drv.version, True, NOW, extra=drv)

    checks = {smbus.device_id: [update(chipset_inf)], spi.device_id: [update(chipset_inf)],
              mei.device_id: [update(mei_drv)]}
    report = analyse([smbus, spi, mei], [], [], checks=checks, now=NOW, consulted=["Microsoft Update"])
    rows = {r.name: r for r in report.devices}
    group = rows["Intel — carte mère / chipset (pilote 10.1.31.3) (2 composants)"]
    assert group.status == driver_analysis.VENDOR_UPDATE and group.can_auto_install
    assert group.catalog_driver is chipset_inf and len(group.device_ids) == 2
    assert "Windows la choisira" in group.explanation
    # Groupe d'un seul composant : le vrai nom du périphérique est conservé.
    single = rows["Intel(R) Management Engine Interface"]
    assert single.catalog_driver is mei_drv and single.count == 1


def test_analysis_generic_newer_is_optional_status():
    uhd = installed("Intel(R) UHD Graphics", "PCI\\VEN_8086&DEV_9BC4&SUBSYS_143F1025&REV_05\\3&1",
                    "27.20.100.8280", datetime(2020, 5, 19), provider="Intel Corporation", cls="Display")
    check = mc.VendorCheck(mc.SOURCE_LABEL, "31.0.101.2141", False, NOW, generic_newer=True,
                           note="Le catalogue contient une version générique plus récente.")
    row = analyse([uhd], [], [], checks={uhd.device_id: [check]}, now=NOW).devices[0]
    assert row.status == driver_analysis.OPTIONAL and not row.can_auto_install
    assert row.latest_version == "31.0.101.2141" and row.checked_source == mc.SOURCE_LABEL
    assert "version générique plus récente" in row.explanation


def test_to_ref_carries_windows_ids():
    d = installed("Killer", "PCI\\VEN_10EC&DEV_2600&SUBSYS_143F1025&REV_21\\01", "10.38.1118.2019",
                  datetime(2019, 11, 18), provider="Rivet", cls="Net")
    ref = driver_analysis.to_ref(d, DeviceIds(("A",), ("B",), "B"))
    assert ref.hardware_ids == ("A",) and ref.compatible_ids == ("B",) and ref.matching_id == "B"
    assert ref.date == datetime(2019, 11, 18) and ref.provider == "Rivet"
    assert mc.MicrosoftCatalogSource().applies_to_device(ref)
    ms = driver_analysis.to_ref(installed("Caméra", "USB\\VID_0408&PID_A061\\6&1", "10.0.1", None,
                                          provider="Microsoft", cls="Camera"))
    assert not mc.MicrosoftCatalogSource().applies_to_device(ms)   # pilote de Windows : suivi par Windows Update
