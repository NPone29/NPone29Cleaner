"""Outils de maintenance ponctuels (DNS, SFC, DISM, Explorateur…)."""

from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass
from typing import Callable

from app.utils.system import CommandResult, is_admin, launch_console, run_command


def flush_dns() -> CommandResult:
    return run_command(["ipconfig", "/flushdns"], timeout=30)


def restart_explorer() -> CommandResult:
    result = run_command(["taskkill", "/f", "/im", "explorer.exe"], timeout=30)
    time.sleep(1.0)
    subprocess.Popen(["explorer.exe"])
    return result


def run_sfc() -> bool:
    return launch_console("sfc /scannow", elevated=True)


def run_dism() -> bool:
    return launch_console("DISM /Online /Cleanup-Image /RestoreHealth", elevated=True)


def run_chkdsk_scan() -> bool:
    return launch_console("chkdsk C: /scan", elevated=True)


def open_disk_cleanup() -> None:
    subprocess.Popen(["cleanmgr.exe"])


def open_storage_settings() -> None:
    subprocess.Popen(["explorer.exe", "ms-settings:storagesense"])


def open_windows_update() -> None:
    subprocess.Popen(["explorer.exe", "ms-settings:windowsupdate"])


def open_device_manager() -> None:
    subprocess.Popen(["mmc.exe", "devmgmt.msc"])


def open_system_protection() -> None:
    subprocess.Popen(["SystemPropertiesProtection.exe"])


def reset_network() -> bool:
    """Réinitialise Winsock et la pile IP (redémarrage nécessaire ensuite)."""
    return launch_console("netsh winsock reset && netsh int ip reset && ipconfig /flushdns", elevated=True)


@dataclass
class MaintenanceAction:
    id: str
    title: str
    description: str
    run: Callable[[], object]
    admin: bool = False
    background: bool = False   # True : s'exécute en arrière-plan et renvoie un CommandResult


ACTIONS: list[MaintenanceAction] = [
    MaintenanceAction("dns", "Vider le cache DNS",
                      "Corrige certains sites qui ne s'ouvrent plus ou des erreurs de connexion.",
                      flush_dns, background=True),
    MaintenanceAction("explorer", "Redémarrer l'Explorateur",
                      "Débloque la barre des tâches ou le bureau qui ne répondent plus.",
                      restart_explorer, background=True),
    MaintenanceAction("sfc", "Réparer les fichiers système (SFC)",
                      "Analyse et répare les fichiers Windows endommagés (10 à 20 min).",
                      run_sfc, admin=True),
    MaintenanceAction("dism", "Réparer l'image Windows (DISM)",
                      "Répare l'image système si SFC n'y arrive pas (Internet requis).",
                      run_dism, admin=True),
    MaintenanceAction("chkdsk", "Vérifier le disque C:",
                      "Recherche les erreurs du système de fichiers, sans redémarrage.",
                      run_chkdsk_scan, admin=True),
    MaintenanceAction("network", "Réinitialiser le réseau",
                      "Winsock + pile IP + DNS. À utiliser si Internet ne fonctionne plus. Redémarrage requis.",
                      reset_network, admin=True),
    MaintenanceAction("cleanmgr", "Nettoyage de disque Windows",
                      "Outil intégré : anciennes installations de Windows (Windows.old), etc.",
                      open_disk_cleanup),
    MaintenanceAction("storage", "Assistant de stockage",
                      "Paramètres Windows de nettoyage automatique.", open_storage_settings),
    MaintenanceAction("wu", "Ouvrir Windows Update",
                      "Mises à jour de Windows lui-même.", open_windows_update),
    MaintenanceAction("devmgr", "Gestionnaire de périphériques",
                      "Vue détaillée du matériel et des pilotes.", open_device_manager),
    MaintenanceAction("restore", "Protection du système",
                      "Gérer les points de restauration.", open_system_protection),
]


def admin_available() -> bool:
    return is_admin()
