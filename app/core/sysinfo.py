"""Informations système affichées sur le tableau de bord."""

from __future__ import annotations

import platform
import winreg
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import psutil


@dataclass
class DiskInfo:
    mount: str
    label: str
    total: int
    used: int
    free: int

    @property
    def percent(self) -> float:
        return (self.used / self.total * 100) if self.total else 0.0


@dataclass
class SystemInfo:
    os_name: str
    computer_name: str
    cpu_name: str
    cpu_cores: int
    cpu_threads: int
    ram_total: int
    manufacturer: str
    model: str
    uptime: timedelta
    disks: list[DiskInfo] = field(default_factory=list)


def _reg(path: str, name: str, hive=winreg.HKEY_LOCAL_MACHINE) -> str:
    try:
        with winreg.OpenKey(hive, path, 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as key:
            return str(winreg.QueryValueEx(key, name)[0]).strip()
    except OSError:
        return ""


def os_name() -> str:
    path = r"SOFTWARE\Microsoft\Windows NT\CurrentVersion"
    product = _reg(path, "ProductName") or f"Windows {platform.release()}"
    build = _reg(path, "CurrentBuild")
    try:
        # Windows 11 indique encore « Windows 10 » dans ProductName.
        if build and int(build) >= 22000:
            product = product.replace("Windows 10", "Windows 11")
    except ValueError:
        pass
    display = _reg(path, "DisplayVersion")
    ubr = _reg(path, "UBR")
    parts = [product]
    if display:
        parts.append(display)
    if build:
        parts.append(f"(build {build}{'.' + ubr if ubr else ''})")
    return " ".join(parts)


def list_disks() -> list[DiskInfo]:
    disks = []
    for part in psutil.disk_partitions(all=False):
        if "cdrom" in part.opts or not part.fstype:
            continue
        try:
            usage = psutil.disk_usage(part.mountpoint)
        except OSError:
            continue
        disks.append(DiskInfo(part.mountpoint, part.fstype, usage.total, usage.used, usage.free))
    return disks


def get_system_info() -> SystemInfo:
    bios = r"HARDWARE\DESCRIPTION\System\BIOS"
    return SystemInfo(
        os_name=os_name(),
        computer_name=platform.node(),
        cpu_name=_reg(r"HARDWARE\DESCRIPTION\System\CentralProcessor\0", "ProcessorNameString")
        or platform.processor(),
        cpu_cores=psutil.cpu_count(logical=False) or 0,
        cpu_threads=psutil.cpu_count(logical=True) or 0,
        ram_total=psutil.virtual_memory().total,
        manufacturer=_reg(bios, "SystemManufacturer"),
        model=_reg(bios, "SystemProductName"),
        uptime=datetime.now() - datetime.fromtimestamp(psutil.boot_time()),
        disks=list_disks(),
    )


def live_usage() -> tuple[float, float]:
    """(CPU %, RAM %) instantanés."""
    return psutil.cpu_percent(interval=None), psutil.virtual_memory().percent
