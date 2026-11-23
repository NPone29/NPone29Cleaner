"""Tests des fonctions d'analyse de texte et de formatage."""

from __future__ import annotations

from datetime import datetime, timedelta

from app.core import bigfiles, drivers, software, startup, uninstaller
from app.utils.fmt import format_duration, human_size, parse_wmi_datetime

WINGET_FR = (
    "   - \r   \\ \r"
    "Nom                                 ID                         Version      Disponible   Source\n"
    "------------------------------------------------------------------------------------------------\n"
    "7-Zip 23.01 (x64)                   7zip.7zip                  23.01        26.06        winget\n"
    "CapCut                              XP9KN75RRB9NHS             7.5.0.3053   9.8.0.4213   msstore\n"
    "Python Launcher                     Python.Launcher            < 3.15.2     3.15.2       winget\n"
    "35 mises à niveau disponibles.\n"
    "2 package(s) a(ont) des numéros de version qui ne peuvent pas être déterminés.\n"
)

WINGET_EN = (
    "\x1b[2K\rName           Id              Version Available Source\n"
    "-------------------------------------------------------\n"
    "Git            Git.Git         2.52.0  2.56.1    winget\n"
    "\n"
    "1 upgrades available.\n"
)


def test_parse_winget_french():
    ups = software.parse_upgrade_output(WINGET_FR)
    assert [u.id for u in ups] == ["7zip.7zip", "XP9KN75RRB9NHS", "Python.Launcher"]
    assert ups[0].name == "7-Zip 23.01 (x64)"
    assert ups[0].version == "23.01" and ups[0].available == "26.06"
    assert ups[1].source == "msstore"
    assert ups[2].version == "< 3.15.2"


def test_parse_winget_english_with_ansi():
    ups = software.parse_upgrade_output(WINGET_EN)
    assert len(ups) == 1
    assert (ups[0].name, ups[0].id, ups[0].version, ups[0].available, ups[0].source) == \
        ("Git", "Git.Git", "2.52.0", "2.56.1", "winget")


def test_parse_winget_no_table():
    assert software.parse_upgrade_output("Aucune mise à niveau disponible.") == []


def test_human_size():
    assert human_size(0) == "0 o"
    assert human_size(512) == "512 o"
    assert human_size(1536) == "1,5 Ko"
    assert human_size(5 * 1024 ** 3) == "5,0 Go"
    assert human_size(150 * 1024 ** 2) == "150 Mo"
    assert human_size(None) == "—"


def test_format_duration():
    assert format_duration(timedelta(minutes=5)) == "5 min"
    assert format_duration(timedelta(hours=2, minutes=3)) == "2 h 03 min"
    assert format_duration(timedelta(days=3, hours=4)) == "3 j 4 h"


def test_parse_wmi_datetime():
    assert parse_wmi_datetime("20230512000000.******+000") == datetime(2023, 5, 12)
    assert parse_wmi_datetime(None) is None
    assert parse_wmi_datetime("garbage") is None


def test_startup_approved_bytes():
    assert startup.is_enabled_value(None)
    assert startup.is_enabled_value(bytes([2]) + bytes(11))
    assert not startup.is_enabled_value(bytes([3]) + bytes(11))
    disabled = startup.make_approved_value(False)
    assert len(disabled) == 12 and not startup.is_enabled_value(disabled)
    assert startup.is_enabled_value(startup.make_approved_value(True))


def test_extract_executable():
    assert startup.extract_executable('"C:\\Program Files\\App\\app.exe" --min') == "C:\\Program Files\\App\\app.exe"
    assert startup.extract_executable("C:\\Tools\\tool.exe /background") == "C:\\Tools\\tool.exe"


def test_split_uninstall_command():
    assert uninstaller.split_command('"C:\\Program Files\\X\\unins000.exe" /SILENT') == \
        ("C:\\Program Files\\X\\unins000.exe", "/SILENT")
    assert uninstaller.split_command("MsiExec.exe /X{1234}") == ("MsiExec.exe", "/X{1234}")


def test_parse_install_date():
    assert uninstaller.parse_install_date("20240131") == datetime(2024, 1, 31)
    assert uninstaller.parse_install_date("") is None


def test_vendor_tools_detection():
    tools = drivers.recommended_vendor_tools({"system": "Dell Inc.", "gpus": "NVIDIA GeForce RTX 4070 | Intel(R) UHD"})
    vendors = [t.vendor for t in tools]
    assert vendors == ["NVIDIA", "Intel", "Dell"]
    assert drivers.recommended_vendor_tools({"system": "To Be Filled By O.E.M.", "gpus": ""}) == []


def test_device_error_text():
    assert drivers.ProblemDevice("X", 28, "id").error_text == "Aucun pilote installé"
    assert drivers.ProblemDevice("X", 999, "id").error_text == "Erreur 999"


def test_find_large_files(tmp_path):
    (tmp_path / "small.bin").write_bytes(b"x" * 10)
    (tmp_path / "big.bin").write_bytes(b"x" * 5000)
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "bigger.bin").write_bytes(b"x" * 9000)
    found = bigfiles.find_large_files(str(tmp_path), min_size=1000, limit=10)
    assert [f.name for f in found] == ["bigger.bin", "big.bin"]
    assert bigfiles.find_large_files(str(tmp_path), min_size=1000, limit=1)[0].name == "bigger.bin"
