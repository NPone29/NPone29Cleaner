"""Tests des sources officielles de pilotes (sans réseau) et des nouveaux états de l'analyse."""

from __future__ import annotations

import io
from datetime import datetime

from app.core import driver_analysis, driver_sources
from app.core.driver_analysis import analyse
from app.core.driver_sources import dell, hp, lenovo
from app.core.driver_sources.base import (DOWNLOAD, EXACT, GENERAL, LINK_ORDER, PREFILLED, SEARCH, DeviceRef,
                                          DownloadLink, DriverSource, SourceRun, SystemContext, VendorCheck,
                                          compare_versions, newest, read_xml_text, version_parts)
from app.core.driver_sources.boards import MsiSource
from app.core.drivers import InstalledDriver

NOW = datetime(2026, 11, 24, 10, 30)


def drv(name, cls, provider, device_id, version="1.0", date=datetime(2025, 1, 1)):
    return InstalledDriver(device_name=name, device_class=cls, manufacturer=provider, provider=provider,
                           version=version, date=date, inf_name="oem1.inf", is_signed=True,
                           device_id=device_id, hardware_id=device_id.rsplit("\\", 1)[0])


def ref(d: InstalledDriver) -> DeviceRef:
    return driver_analysis.to_ref(d)


CHIPSET = drv("Intel SMBus", "System", "INTEL", r"PCI\VEN_8086&DEV_7A30\3&0", version="10.1.46.3")
WIFI = drv("Intel(R) Wi-Fi 6 AX201", "Net", "Intel", r"PCI\VEN_8086&DEV_7A70&SUBSYS_00748086\3&1",
           version="22.200.0.6")
AUDIO = drv("Realtek Audio", "Media", "Realtek", r"HDAUDIO\FUNC_01&VEN_10EC&DEV_0236&SUBSYS_103C8DE2&REV_1000\4&1",
            version="6.0.9400.1")
CAMERA = drv("Surface Camera", "Camera", "Microsoft", r"USB\VID_045E&PID_0990&MI_00\6&1", version="10.0.1.1")

DELL = SystemContext(system_maker="Dell Inc.", model="Dell Pro 13 Plus PB13250", sku="0CF4",
                     windows_build=26100, windows_release="24H2")
HP = SystemContext(system_maker="HP", model="HP EliteBook 840", board_maker="HP", board_product="8DE2",
                   windows_build=26100, windows_release="24H2")
LENOVO = SystemContext(system_maker="LENOVO", model="20XWCTO1WW", windows_build=22631, windows_release="23H2")


# --------------------------------------------------------------------------- versions

def test_compare_versions():
    assert compare_versions("31.0.101.5590", "31.0.101.4502") == 1
    assert compare_versions("1.2", "1.2.0.0") == 0
    assert compare_versions("1.0.0.9", "1.0.0.10") == -1
    assert compare_versions("30.100.2129.8^", "30.100.2129.8") == 0
    assert compare_versions("6.0.9.1 Rev.A", "6.0.9.1") is None       # non comparable : jamais « à jour »
    assert compare_versions("", "1.0") is None
    assert version_parts("A01") is None
    assert newest(["1.2.3", "x", "1.10.0", "1.9.9"]) == "1.10.0"


def test_read_xml_text_handles_utf16_and_bom():
    xml = '<?xml version="1.0" encoding="utf-16"?><a>é</a>'
    assert read_xml_text(xml.encode("utf-16")) == "<a>é</a>"
    assert read_xml_text(b"\xef\xbb\xbf<?xml version='1.0' encoding='UTF-8'?><a/>") == "<a/>"


# --------------------------------------------------------------------------- Dell

DELL_XML = '''<?xml version="1.0" encoding="utf-16"?>
<Manifest baseLocation="downloads.dell.com" version="2026.11">
  <SoftwareComponent releaseID="025D7" path="FOLDER1/1/Intel-Chipset_A01.EXE" vendorVersion="10.1.50.1"
      dellVersion="A01" releaseDate="March 31, 2026">
    <Name><Display lang="en"><![CDATA[Intel Chipset Device Software]]></Display></Name>
    <ComponentType value="DRVR"><Display lang="en">Driver</Display></ComponentType>
    <Category value="CS"><Display lang="en">Chipset</Display></Category>
    <SupportedDevices><Device componentID="1">
      <PCIInfo deviceID="7A30" vendorID="8086" subDeviceID="" subVendorID=""/></Device></SupportedDevices>
    <SupportedSystems><Brand key="1"><Model systemID="0CF4"><Display>Dell-Pro-13-Plus-PB13250</Display></Model>
    </Brand></SupportedSystems>
  </SoftwareComponent>
  <SoftwareComponent releaseID="OLD01" path="FOLDER0/1/Intel-Chipset_A00.EXE" vendorVersion="10.1.46.3"
      dellVersion="A00" releaseDate="January 1, 2025">
    <Name><Display lang="en">Intel Chipset (ancien)</Display></Name>
    <ComponentType value="DRVR"/>
    <SupportedDevices><Device><PCIInfo deviceID="7A30" vendorID="8086"/></Device></SupportedDevices>
    <SupportedSystems><Brand><Model systemID="0cf4"/></Brand></SupportedSystems>
  </SoftwareComponent>
  <SoftwareComponent releaseID="OTHER" path="X/other.EXE" vendorVersion="99.0.0.0">
    <ComponentType value="DRVR"/>
    <SupportedDevices><Device><PCIInfo deviceID="7A30" vendorID="8086"/></Device></SupportedDevices>
    <SupportedSystems><Brand><Model systemID="0AAA"/></Brand></SupportedSystems>
  </SoftwareComponent>
  <SoftwareComponent releaseID="BIOS1" path="X/bios.EXE" vendorVersion="1.5.0">
    <ComponentType value="BIOS"/>
    <SupportedSystems><Brand><Model systemID="0CF4"/></Brand></SupportedSystems>
  </SoftwareComponent>
  <SoftwareComponent releaseID="WIFI1" path="F/wifi.EXE" vendorVersion="23.10.0.4">
    <Name><Display>Intel Wi-Fi</Display></Name>
    <ComponentType value="DRVR"/>
    <SupportedDevices><Device><PCIInfo deviceID="7A70" vendorID="8086" subDeviceID="0074" subVendorID="8086"/>
    </Device></SupportedDevices>
    <SupportedSystems><Brand><Model systemID="0CF4"/></Brand></SupportedSystems>
  </SoftwareComponent>
  <SoftwareComponent releaseID="WIFI2" path="F/wifi-autre.EXE" vendorVersion="99.0.0.0">
    <ComponentType value="DRVR"/>
    <SupportedDevices><Device><PCIInfo deviceID="7A70" vendorID="8086" subDeviceID="9999" subVendorID="1028"/>
    </Device></SupportedDevices>
    <SupportedSystems><Brand><Model systemID="0CF4"/></Brand></SupportedSystems>
  </SoftwareComponent>
</Manifest>'''


def dell_drivers():
    return dell.parse_catalog(io.BytesIO(DELL_XML.encode("utf-16")), "0CF4")


def test_dell_catalog_parsing():
    drivers = dell_drivers()
    assert sorted(d.release_id for d in drivers) == ["025D7", "OLD01", "WIFI1", "WIFI2"]   # ni BIOS ni autre PC
    chipset = next(d for d in drivers if d.release_id == "025D7")
    assert chipset.name == "Intel Chipset Device Software" and chipset.category == "Chipset"
    assert chipset.download_url == "https://downloads.dell.com/FOLDER1/1/Intel-Chipset_A01.EXE"
    assert chipset.page_url.endswith("driversdetails?driverid=025D7")
    assert dell.parse_catalog(io.BytesIO(DELL_XML.encode("utf-16")), "") == []


def test_dell_matching_and_newest_version():
    drivers = dell_drivers()
    best = dell.best(dell.matching(drivers, ref(CHIPSET)))
    assert best.release_id == "025D7"
    check = dell.make_check(ref(CHIPSET), best, NOW)
    assert check.update_available and check.comparable and check.latest_version == "10.1.50.1"
    # Le SUBSYS précisé par Dell doit correspondre : WIFI2 (autre sous-système) est écarté.
    assert [d.release_id for d in dell.matching(drivers, ref(WIFI))] == ["WIFI1"]
    assert dell.matching(drivers, ref(AUDIO)) == []      # pas un périphérique PCI


# --------------------------------------------------------------------------- HP

HP_PLATFORMS = '''<ImagePal><Platform><SystemID>8DE2</SystemID><SystemID>8DE3</SystemID>
  <OS><OSVersion>10.0</OSVersion><OSReleaseIdFilename>23H2</OSReleaseIdFilename><IsWindows11>true</IsWindows11></OS>
  <OS><OSVersion>10.0</OSVersion><OSReleaseIdFilename>24H2</OSReleaseIdFilename><IsWindows11>true</IsWindows11></OS>
  <OS><OSVersion>10.0</OSVersion><OSReleaseIdFilename>22H2</OSReleaseIdFilename><IsWindows11>false</IsWindows11></OS>
</Platform><Platform><SystemID>1234</SystemID>
  <OS><OSVersion>10.0</OSVersion><OSReleaseIdFilename>2009</OSReleaseIdFilename><IsWindows11>false</IsWindows11></OS>
</Platform></ImagePal>'''

HP_REFERENCE = r'''<ImagePal>
  <SystemInfo><System><Solutions><UpdateInfo><Id>sp1</Id><Name>NE PAS UTILISER</Name></UpdateInfo></Solutions>
  </System></SystemInfo>
  <Solutions>
    <UpdateInfo><Id>sp1</Id><Name>Realtek Audio Driver</Name><Category>Driver - Audio</Category>
      <Version>6.0.9.1 Rev.A</Version><Url>ftp.hp.com/pub/softpaq/sp174001-174500/sp174332.exe</Url>
      <ReleaseNotesUrl>ftp.hp.com/pub/softpaq/sp174001-174500/sp174332.html</ReleaseNotesUrl>
      <DateReleased>2026-08-13</DateReleased></UpdateInfo>
    <UpdateInfo><Id>sp2</Id><Name>Realtek Audio (générique)</Name><Url>ftp.hp.com/pub/softpaq/sp2.exe</Url>
    </UpdateInfo>
  </Solutions>
  <Devices>
    <Device><DeviceId>HDAUDIO\FUNC_01&amp;VEN_10EC&amp;DEV_0236&amp;SUBSYS_103C8DE2</DeviceId>
      <DriverVersion>6.0.9500.1</DriverVersion><Solutions><UpdateInfo IdRef="sp1"/></Solutions></Device>
    <Device><DeviceId>HDAUDIO\FUNC_01&amp;VEN_10EC&amp;DEV_0236&amp;SUBSYS_103C0000</DeviceId>
      <DriverVersion>6.0.9999.1</DriverVersion><Solutions><UpdateInfo IdRef="sp2"/></Solutions></Device>
  </Devices>
</ImagePal>'''


def test_hp_platform_list_and_reference_url():
    oses = hp.parse_platform_list(HP_PLATFORMS, "8de2")
    assert {o.release for o in oses} == {"23H2", "24H2", "22H2"}
    chosen = hp.choose_os(oses, windows11=True, release="24H2")
    assert chosen.release == "24H2"
    assert hp.choose_os(oses, windows11=True, release="25H2").release == "24H2"    # la plus récente
    assert hp.choose_os(oses, windows11=False, release="21H2").release == "22H2"
    assert hp.reference_url("8DE2", chosen) == "https://hpia.hpcloud.hp.com/ref/8de2/8de2_64_11.0.24h2.cab"
    assert hp.release_key("2009") == (20, 2) and hp.release_key("2004") == (20, 1)
    assert hp.parse_platform_list(HP_PLATFORMS, "FFFF") == []


def test_hp_reference_parsing_and_matching():
    entries = hp.parse_reference(HP_REFERENCE)
    assert len(entries) == 2
    first = entries[0]
    assert first.name == "Realtek Audio Driver"           # Solutions enfant direct, pas SystemInfo
    assert first.url == "https://ftp.hp.com/pub/softpaq/sp174001-174500/sp174332.exe"
    assert first.notes_url.startswith("https://ftp.hp.com/")
    # Même SUBSYS en priorité (même si l'autre entrée est plus récente)…
    check = hp.make_check(ref(AUDIO), hp.matching(entries, ref(AUDIO)), NOW)
    assert check.latest_version == "6.0.9500.1" and check.update_available
    # … sinon même puce (VEN/DEV) : la plus récente.
    other = drv("Realtek Audio", "Media", "Realtek", r"HDAUDIO\FUNC_01&VEN_10EC&DEV_0236&SUBSYS_17AA1234\4&1",
                version="6.0.9999.1")
    check = hp.make_check(ref(other), hp.matching(entries, ref(other)), NOW)
    assert check.latest_version == "6.0.9999.1" and not check.update_available and check.comparable


# --------------------------------------------------------------------------- Lenovo

LENOVO_CATALOG = '''<packages>
  <package><location>https://download.lenovo.com/pccbbs/mobiles/n32li04w_2_.xml</location>
    <category>Networking: Wireless LAN</category></package>
  <package><location>https://evil.example/pccbbs/x.xml</location></package>
</packages>'''

LENOVO_PACKAGE = '''<?xml version="1.0" encoding="UTF-8"?>
<Package name="INTELWLAN" id="n32li04w" version="30.100.2129.8" hide="False">
  <Title default="EN"><Desc id="EN">Intel Wireless LAN Driver</Desc></Title>
  <ReleaseDate>2021-10-26</ReleaseDate>
  <DetectInstall><And><_Driver><HardwareID><![CDATA[PCI\\VEN_8086&DEV_A0F0]]></HardwareID>
    <Version>30.100.2129.8^</Version></_Driver></And></DetectInstall>
  <Files><Installer><File><Name>n32li04w.exe</Name></File></Installer>
    <Readme><File><Name>n32li04w.txt</Name></File></Readme></Files>
</Package>'''

LENOVO_BIOS = '''<Package id="n32uj20w" version="1.20"><Title><Desc>BIOS</Desc></Title>
  <DetectInstall><_Bios><Level>N32ET</Level></_Bios></DetectInstall></Package>'''

LENOVO_WIFI = drv("Intel(R) Wi-Fi 6 AX201", "Net", "Intel", r"PCI\VEN_8086&DEV_A0F0&SUBSYS_00748086\3&1",
                  version="22.0.0.1")
LOCATION = "https://download.lenovo.com/pccbbs/mobiles/n32li04w_2_.xml"


def lenovo_package():
    data = b"\xef\xbb\xbf" + LENOVO_PACKAGE.encode("utf-8")
    return lenovo.parse_package(read_xml_text(data), LOCATION)


def test_lenovo_catalog_and_package_parsing():
    assert lenovo.parse_catalog(LENOVO_CATALOG) == [LOCATION]           # domaine non officiel ignoré
    pkg = lenovo_package()
    assert pkg.package_id == "n32li04w" and pkg.title == "Intel Wireless LAN Driver"
    assert pkg.drivers == [([r"PCI\VEN_8086&DEV_A0F0"], "30.100.2129.8")]
    assert pkg.download_url == "https://download.lenovo.com/pccbbs/mobiles/n32li04w.exe"
    assert pkg.readme_url == "https://download.lenovo.com/pccbbs/mobiles/n32li04w.txt"
    assert lenovo.parse_package(LENOVO_BIOS, LOCATION) is None
    assert lenovo.machine_type("20XWCTO1WW") == "20XW" and lenovo.machine_type("") == ""


def test_lenovo_check():
    check = lenovo.make_check(ref(LENOVO_WIFI), [lenovo_package()], NOW)
    assert check.update_available and check.latest_version == "30.100.2129.8"
    assert lenovo.make_check(ref(WIFI), [lenovo_package()], NOW) is None     # autre puce


# --------------------------------------------------------------------------- nouveaux états

def test_unverified_by_default():
    report = analyse([WIFI], [], [], now=NOW)
    wifi = report.devices[0]
    assert wifi.status == driver_analysis.UNVERIFIED            # jamais « à jour » sans source fiable
    assert wifi.checked_source == "" and wifi.checked_at is None
    assert "Intel ne propose pas de service public" in wifi.no_api_note
    # Pas de lien propre au modèle : la recherche par ID matériel (pré-remplie) passe avant la page générale.
    assert wifi.best_link.label.startswith("Catalogue Microsoft")
    assert wifi.links[1].label.startswith("Intel Driver & Support Assistant")


def test_verified_via_dell():
    drivers = dell_drivers()
    up_to_date = drv("Intel Chipset", "System", "INTEL", r"PCI\VEN_8086&DEV_7A30\3&0", version="10.1.50.1")
    check = dell.make_check(ref(up_to_date), dell.best(dell.matching(drivers, ref(up_to_date))), NOW)
    report = analyse([up_to_date], [], [], checks={ref(up_to_date).key: [check]}, ctx=DELL, now=NOW,
                     consulted=["Dell"])
    row = report.devices[0]
    assert row.status == driver_analysis.VERIFIED
    assert row.checked_source == "Catalogue officiel Dell" and row.checked_at == NOW
    assert [link.kind for link in row.links[:3]] == [DOWNLOAD, EXACT, PREFILLED]
    assert row.best_link.url == "https://downloads.dell.com/FOLDER1/1/Intel-Chipset_A01.EXE"


def test_dell_device_missing_from_catalog_explained():
    report = analyse([AUDIO], [], [], ctx=DELL, now=NOW, consulted=["Dell"])
    row = report.devices[0]
    assert row.status == driver_analysis.UNVERIFIED
    assert "ne figure pas dans le catalogue officiel Dell" in row.no_api_note
    assert "Dell ne propose pas" not in row.no_api_note       # Dell a bien un catalogue


def test_update_via_lenovo():
    check = lenovo.make_check(ref(LENOVO_WIFI), [lenovo_package()], NOW)
    report = analyse([LENOVO_WIFI], [], [], checks={ref(LENOVO_WIFI).key: [check]}, ctx=LENOVO, now=NOW)
    row = report.devices[0]
    assert row.status == driver_analysis.VENDOR_UPDATE
    assert not row.can_auto_install                            # Lenovo : on ouvre le lien, on n'exécute rien
    assert row.latest_version == "30.100.2129.8" and row.checked_source == "Catalogue officiel Lenovo"
    assert row.best_link.kind == DOWNLOAD
    assert row.best_link.url == "https://download.lenovo.com/pccbbs/mobiles/n32li04w.exe"
    assert any(link.url == "https://pcsupport.lenovo.com/fr/fr/products/20XW/downloads/driver-list"
               for link in row.links)


def test_non_comparable_check_does_not_verify():
    check = VendorCheck("Catalogue officiel HP", "6.0.9.1 Rev.A", False, NOW, comparable=False)
    report = analyse([AUDIO], [], [], checks={ref(AUDIO).key: [check]}, ctx=HP, now=NOW)
    assert report.devices[0].status == driver_analysis.UNVERIFIED


def test_windows_update_failure_verifies_nothing():
    ok = analyse([CAMERA], [], [], wu_ok=True, now=NOW).devices[0]
    assert ok.status == driver_analysis.VERIFIED and ok.checked_source == "Windows Update"
    failed = analyse([CAMERA], [], [], wu_ok=False, now=NOW)
    assert failed.devices[0].status == driver_analysis.UNVERIFIED
    assert any("Windows Update n'a pas pu être consulté" in n for n in failed.notes)


# --------------------------------------------------------------------------- liens

class FakeSource(DriverSource):
    name = "Fake"
    allowed_hosts = (".example.com",)

    def __init__(self, links=(), fail=False, api=False):
        self._links = list(links)
        self.fail = fail
        self.has_public_api = api

    def applies_to_device(self, dev):
        return True

    def check(self, ctx, devices, run, progress=None):
        if self.fail:
            raise OSError("réseau coupé")
        run.verified_by.append(self.name)

    def links(self, ctx, dev, checks):
        return [link for link in self._links if self.safe(link)]


def test_links_outside_official_domains_are_refused():
    source = FakeSource([
        DownloadLink("ok", "https://downloads.example.com/a.exe", DOWNLOAD),
        DownloadLink("http", "http://downloads.example.com/a.exe", DOWNLOAD),
        DownloadLink("faux domaine", "https://example.com.evil.test/a.exe", DOWNLOAD),
        DownloadLink("recherche", "https://www.google.com/search?q=x", SEARCH),
        DownloadLink("google mais pas une recherche", "https://www.google.com/a.exe", DOWNLOAD),
    ])
    links = driver_sources.links_for(SystemContext(), ref(WIFI), [], sources=[source])
    assert [link.label for link in links if not link.label.startswith("Catalogue")] == ["ok", "recherche"]
    # Fichier Dell : le chemin vient du catalogue mais le domaine est imposé.
    bad = VendorCheck(dell.SOURCE_LABEL, "1.0", True, NOW, download_url="https://evil.test/x.exe",
                      page_url="https://www.dell.com/support/home/fr-fr/drivers/driversdetails?driverid=1")
    urls = [link.url for link in dell.DellSource().links(DELL, ref(CHIPSET), [bad])]
    assert "https://evil.test/x.exe" not in urls and len(urls) == 2


def test_links_sorted_and_deduplicated():
    source = FakeSource([
        DownloadLink("recherche", "https://www.google.com/search?q=x", SEARCH),
        DownloadLink("général", "https://www.example.com/", GENERAL),
        DownloadLink("doublon", "https://www.example.com/", GENERAL),
        DownloadLink("page", "https://www.example.com/p", EXACT),
        DownloadLink("fichier", "https://www.example.com/f.exe", DOWNLOAD),
        DownloadLink("modèle", "https://www.example.com/m", PREFILLED),
    ])
    links = driver_sources.links_for(SystemContext(), ref(WIFI), [], sources=[source])
    kinds = [link.kind for link in links]
    assert kinds == sorted(kinds, key=LINK_ORDER.index)
    assert [link.label for link in links if link.kind == GENERAL] == ["général"]
    assert links[0].label == "fichier"
    assert any(link.label.startswith("Catalogue Microsoft") for link in links)


def test_check_all_survives_a_failing_source():
    run = driver_sources.check_all(SystemContext(), [ref(WIFI)],
                                   sources=[FakeSource(fail=True, api=True), FakeSource(api=True)])
    assert run.verified_by == ["Fake"]
    assert any("Catalogue Fake indisponible" in n for n in run.notes)


def test_board_prefilled_links():
    assert MsiSource.slug("MAG B550 TOMAHAWK (MS-7C91)") == "MAG-B550-TOMAHAWK"
    msi = SystemContext(board_maker="Micro-Star International Co., Ltd.", board_product="MAG B550 TOMAHAWK (MS-7C91)")
    links = driver_sources.links_for(msi, ref(AUDIO), [])
    assert links[0].url == "https://fr.msi.com/Motherboard/MAG-B550-TOMAHAWK/support"
    # Carte graphique NVIDIA d'un PC assemblé : pas de lien carte mère.
    gpu = drv("NVIDIA GeForce RTX 4070", "Display", "NVIDIA", r"PCI\VEN_10DE&DEV_2786\4&1")
    assert not any("msi.com" in link.url for link in driver_sources.links_for(msi, ref(gpu), []))


def test_source_run_add():
    run = SourceRun()
    run.add("k", VendorCheck("s", "1.0", False, NOW))
    assert len(run.checks["k"]) == 1
