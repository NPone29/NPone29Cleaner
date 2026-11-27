"""Sauvegarde automatique : planification Windows et mode sans interface (``--backup``).

Fonctionnement :
  * une tâche du Planificateur de tâches Windows lance ``NPone29Cleaner.exe --backup`` à l'ouverture de
    session puis toutes les heures ;
  * ce mode sans fenêtre relit les réglages, et décide lui-même s'il doit agir : sauvegarde « due » selon la
    fréquence choisie (toutes les 4 h, chaque jour…), disque de destination présent, aucune autre sauvegarde
    en cours. Un portable éteint à l'heure prévue, ou un disque branché de temps en temps, est donc sauvegardé
    dès que possible ;
  * le résultat est écrit dans ``backup_status.json`` (et non dans ``settings.json``, que l'interface
    ouverte réécrit) puis signalé par une notification Windows.

Les fonctions de décision (échéance, type, XML de la tâche, commande) sont pures et testées ; les appels à
Windows (schtasks, verrou, notification) sont isolés.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable
from xml.sax.saxutils import escape

from app.config import APP_ID, APP_NAME
from app.core import backup
from app.core.backup import DIFFERENTIAL, FULL, INCREMENTAL, KIND_LABELS

log = logging.getLogger(__name__)

TASK_NAME = "NPone29 Cleaner - Sauvegarde"
STATUS_FILE = "backup_status.json"
LOCK_FILE = "backup.lock"
DEFAULT_SELECTION = ("Documents", "Bureau", "Images")   # dossiers cochés par défaut (comme la page)
MODES = (FULL, DIFFERENTIAL, INCREMENTAL)

# Fréquences proposées, en heures. La tâche vérifie toutes les heures : impossible d'aller plus vite.
FREQUENCIES: dict[str, int] = {
    "Toutes les heures": 1, "Toutes les 2 heures": 2, "Toutes les 4 heures": 4, "Toutes les 6 heures": 6,
    "Toutes les 12 heures": 12, "Chaque jour": 24, "Chaque semaine": 168,
}
DEFAULT_HOURS = 24
# « Une sauvegarde complète toutes les N semaines » (0 = jamais : uniquement le type choisi).
FULL_EVERY: dict[str, int] = {"Jamais": 0, "Chaque semaine": 1, "Toutes les 2 semaines": 2,
                              "Toutes les 4 semaines": 4}
# La tâche se déclenche à heure fixe : sans marge, une sauvegarde « toutes les 4 h » lancée à 10 h 00 min 05 s
# ne serait pas due à 14 h 00 min 03 s et attendrait 15 h.
TOLERANCE = timedelta(minutes=10)
MISSING_REMINDER_DAYS = 7          # rappel si le disque n'a pas été branché depuis ce nombre de jours
REMINDER_EVERY = timedelta(days=1)  # au plus un rappel (ou une alerte d'échec identique) par jour
EXECUTION_LIMIT = "PT72H"          # une toute première sauvegarde complète sur USB 2 peut être très longue

OK, WARNING, ERROR, MISSING = "success", "warning", "error", "missing"


# =========================================================================== réglages

def frequency_label(hours: int) -> str:
    return next((label for label, h in FREQUENCIES.items() if h == hours), f"Toutes les {hours} heures")


def configured_sources(settings) -> list[str]:
    """Dossiers à sauvegarder, exactement comme les coche la page Sauvegarde."""
    saved = settings.get("backup_sources", None)
    if saved is not None:
        return [p for p in saved if p]
    custom = [p for p in (settings.get("backup_custom", []) or []) if p]
    return [path for label, path in backup.default_sources() if label in DEFAULT_SELECTION] + custom


@dataclass(frozen=True)
class AutoConfig:
    enabled: bool
    hours: int
    mode: str
    full_weeks: int
    destination: str
    sources: list[str]
    keep_full: int

    @classmethod
    def from_settings(cls, settings) -> "AutoConfig":
        hours = int(settings.get("backup_auto_hours", DEFAULT_HOURS) or DEFAULT_HOURS)
        mode = settings.get("backup_auto_mode", INCREMENTAL)
        return cls(
            enabled=bool(settings.get("backup_auto", False)),
            hours=max(1, hours),
            mode=mode if mode in MODES else INCREMENTAL,
            full_weeks=max(0, int(settings.get("backup_auto_full_weeks", 0) or 0)),
            destination=(settings.get("backup_destination", "") or "").strip(),
            sources=configured_sources(settings),
            keep_full=int(settings.get("backup_keep_full", 0) or 0),
        )


# =========================================================================== décisions (fonctions pures)

def is_due(last_start: datetime | None, hours: int, now: datetime) -> bool:
    """Une sauvegarde est-elle due ? `last_start` : début de la dernière sauvegarde automatique réussie."""
    if last_start is None or last_start > now:      # jamais faite, ou horloge du PC reculée
        return True
    return now - last_start >= timedelta(hours=hours) - TOLERANCE


def next_due(last_start: datetime | None, hours: int) -> datetime | None:
    return None if last_start is None else last_start + timedelta(hours=hours)


def choose_kind(mode: str, full_weeks: int, last_full: datetime | None, now: datetime) -> str:
    """Type à effectuer : le type choisi, ou une complète si la dernière complète est trop ancienne."""
    if mode == FULL:
        return FULL
    if full_weeks > 0 and (last_full is None or now - last_full >= timedelta(weeks=full_weeks) - TOLERANCE):
        return FULL
    return mode


def should_remind_missing(missing_since: datetime | None, last_reminder: datetime | None, now: datetime,
                          days: int = MISSING_REMINDER_DAYS) -> bool:
    """Rappel « disque absent depuis N jours », au plus une fois par jour."""
    if missing_since is None or now - missing_since < timedelta(days=days):
        return False
    return last_reminder is None or now - last_reminder >= REMINDER_EVERY


def should_alert_failure(message: str, last_message: str, last_alert: datetime | None, now: datetime) -> bool:
    """Une même erreur (ex. disque plein) n'est signalée qu'une fois par jour, pas à chaque essai horaire."""
    if message != last_message or last_alert is None:
        return True
    return now - last_alert >= REMINDER_EVERY


def days_text(delta: timedelta) -> str:
    days = delta.days
    if days >= 2:
        return f"{days} jours"
    if days == 1:
        return "1 jour"
    hours = delta.seconds // 3600
    return f"{hours} h" if hours else f"{max(1, delta.seconds // 60)} min"


# =========================================================================== état (backup_status.json)

def status_path() -> Path:
    from app.utils.paths import data_dir

    return data_dir() / STATUS_FILE


def load_status(path: Path | None = None) -> dict:
    try:
        data = json.loads((path or status_path()).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        log.warning("État de la sauvegarde automatique illisible : %s", exc)
        return {}


def save_status(status: dict, path: Path | None = None) -> None:
    path = path or status_path()
    try:
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(status, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)
    except OSError as exc:
        log.warning("Impossible d'enregistrer l'état de la sauvegarde automatique : %s", exc)


def _iso(when: datetime) -> str:
    return when.isoformat(timespec="seconds")


def last_backup(settings_last: dict | None, status: dict | None) -> dict:
    """La plus récente des sauvegardes réussies, manuelle (`backup_last`) ou automatique (état)."""
    manual = dict(settings_last or {})
    status = status or {}
    auto = {}
    if status.get("last_success"):
        auto = {"finished": status["last_success"], "kind": status.get("last_kind", ""),
                "destination": status.get("destination", ""), "set": status.get("last_set", ""), "auto": True}
    m, a = backup.parse_time(manual.get("finished")), backup.parse_time(auto.get("finished"))
    if a and (m is None or a > m):
        return auto
    return manual


# =========================================================================== verrou

def lock_path() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home())
    path = Path(base) / APP_ID
    path.mkdir(parents=True, exist_ok=True)
    return path / LOCK_FILE


class BackupLock:
    """Empêche deux sauvegardes simultanées (interface et tâche planifiée, ou deux tâches).

    Verrou sur un fichier : Windows le libère tout seul si le processus s'arrête brutalement.
    """

    def __init__(self, path: Path | None = None):
        self.path = path or lock_path()
        self._fd: int | None = None

    @property
    def held(self) -> bool:
        return self._fd is not None

    def acquire(self) -> bool:
        if self._fd is not None:
            return True
        try:
            fd = os.open(str(self.path), os.O_RDWR | os.O_CREAT, 0o666)
        except OSError as exc:
            log.warning("Verrou de sauvegarde impossible (%s) : on continue sans.", exc)
            return True
        try:
            os.lseek(fd, 0, os.SEEK_SET)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            else:  # pragma: no cover - l'application ne tourne que sous Windows
                import fcntl

                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(fd)
            return False
        self._fd = fd
        return True

    def release(self) -> None:
        if self._fd is None:
            return
        try:
            os.lseek(self._fd, 0, os.SEEK_SET)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self._fd, msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
        finally:
            os.close(self._fd)
            self._fd = None

    def __enter__(self) -> "BackupLock":
        return self

    def __exit__(self, *_exc) -> None:
        self.release()


# =========================================================================== tâche planifiée

def task_command(frozen: bool | None = None, executable: str | None = None,
                 root: str | Path | None = None) -> tuple[str, str, str]:
    """(programme, arguments, dossier de travail) lancés par la tâche.

    Application compilée : le .exe lui-même. Développement : pythonw.exe (sans fenêtre) + main.py.
    """
    frozen = bool(getattr(sys, "frozen", False)) if frozen is None else frozen
    exe = Path(executable or sys.executable)
    if frozen:
        return str(exe), "--backup", str(exe.parent)
    pythonw = exe.with_name("pythonw.exe")
    use_pythonw = exe.name.lower() == "python.exe" and (executable is not None or pythonw.exists())
    program = pythonw if use_pythonw else exe
    if root is None:
        from app.utils.paths import project_root

        root = project_root()
    root = Path(root)
    return str(program), f'"{root / "main.py"}" --backup', str(root)


def task_xml(command: str, arguments: str, workdir: str, user_id: str, start: datetime) -> str:
    """Définition de la tâche (format XML du Planificateur de tâches).

    - à l'ouverture de session (après 3 min) puis toutes les heures : le mode --backup décide s'il faut agir ;
    - StartWhenAvailable : une heure manquée (PC éteint) est rattrapée au démarrage ;
    - aucune restriction sur batterie ; une seule instance à la fois ;
    - session de l'utilisateur (InteractiveToken) : accès à ses dossiers ; droits les plus élevés car le .exe
      demande les droits administrateur.
    """
    e = escape
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Author>{e(APP_NAME)}</Author>
    <Description>Sauvegarde automatique de vos fichiers par {e(APP_NAME)}. La tâche vérifie toutes les heures si une sauvegarde est due et si le disque de sauvegarde est branché. Pour l'arrêter, désactivez la sauvegarde automatique dans l'application (page Sauvegarde).</Description>
    <URI>\\{e(TASK_NAME)}</URI>
  </RegistrationInfo>
  <Triggers>
    <LogonTrigger>
      <Enabled>true</Enabled>
      <UserId>{e(user_id)}</UserId>
      <Delay>PT3M</Delay>
    </LogonTrigger>
    <TimeTrigger>
      <Repetition>
        <Interval>PT1H</Interval>
        <StopAtDurationEnd>false</StopAtDurationEnd>
      </Repetition>
      <StartBoundary>{start:%Y-%m-%dT%H:%M:%S}</StartBoundary>
      <Enabled>true</Enabled>
    </TimeTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <UserId>{e(user_id)}</UserId>
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>HighestAvailable</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <IdleSettings>
      <StopOnIdleEnd>false</StopOnIdleEnd>
      <RestartOnIdle>false</RestartOnIdle>
    </IdleSettings>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>false</Hidden>
    <RunOnlyIfIdle>false</RunOnlyIfIdle>
    <WakeToRun>false</WakeToRun>
    <ExecutionTimeLimit>{EXECUTION_LIMIT}</ExecutionTimeLimit>
    <Priority>7</Priority>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{e(command)}</Command>
      <Arguments>{e(arguments)}</Arguments>
      <WorkingDirectory>{e(workdir)}</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"""


@dataclass(frozen=True)
class TaskInfo:
    command: str
    arguments: str

    def matches(self, command: str, arguments: str) -> bool:
        """La tâche lance-t-elle bien cette copie de l'application (et non un ancien emplacement) ?"""
        return (os.path.normcase(os.path.normpath(self.command.strip('"'))) ==
                os.path.normcase(os.path.normpath(command.strip('"')))
                and self.arguments.strip().lower() == arguments.strip().lower())


def parse_task_xml(text: str) -> TaskInfo | None:
    from html import unescape

    cmd = re.search(r"<Command>(.*?)</Command>", text, re.S)
    if not cmd:
        return None
    args = re.search(r"<Arguments>(.*?)</Arguments>", text, re.S)
    return TaskInfo(unescape(cmd.group(1).strip()), unescape(args.group(1).strip()) if args else "")


def current_user_id() -> str:
    """Identifiant (SID) de l'utilisateur de la session, sinon DOMAINE\\nom."""
    try:
        import win32api
        import win32security

        token = win32security.OpenProcessToken(win32api.GetCurrentProcess(), win32security.TOKEN_QUERY)
        sid = win32security.GetTokenInformation(token, win32security.TokenUser)[0]
        return win32security.ConvertSidToStringSid(sid)
    except Exception:  # noqa: BLE001 - pywin32 absent ou erreur : on se rabat sur le nom
        domain = os.environ.get("USERDOMAIN", "")
        user = os.environ.get("USERNAME", "")
        return f"{domain}\\{user}" if domain else user


def _schtasks() -> str:
    return str(Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "schtasks.exe")


def create_task(now: datetime | None = None):
    """Crée (ou remplace) la tâche planifiée. Nécessite les droits administrateur."""
    from app.utils.system import run_command

    now = now or datetime.now()
    command, arguments, workdir = task_command()
    start = now.replace(second=0, microsecond=0) + timedelta(minutes=5)
    xml = task_xml(command, arguments, workdir, current_user_id(), start)
    fd, name = tempfile.mkstemp(prefix="npone29_task_", suffix=".xml")
    os.close(fd)
    try:
        Path(name).write_text(xml, encoding="utf-16")
        result = run_command([_schtasks(), "/Create", "/TN", TASK_NAME, "/XML", name, "/F"], timeout=60)
    finally:
        try:
            os.unlink(name)
        except OSError:
            pass
    if result.ok:
        log.info("Tâche planifiée « %s » créée : %s %s", TASK_NAME, command, arguments)
    else:
        log.warning("Création de la tâche planifiée impossible : %s", result.output)
    return result


def delete_task():
    from app.utils.system import run_command

    result = run_command([_schtasks(), "/Delete", "/TN", TASK_NAME, "/F"], timeout=60)
    log.info("Suppression de la tâche planifiée : %s", "ok" if result.ok else result.output)
    return result


def query_task() -> TaskInfo | None:
    """La tâche existe-t-elle, et que lance-t-elle ? None si elle est absente."""
    from app.utils.system import run_command

    result = run_command([_schtasks(), "/Query", "/TN", TASK_NAME, "/XML"], timeout=30)
    if not result.ok:
        return None
    return parse_task_xml(result.stdout)


def network_drive_target(path: str) -> str:
    """Si `path` est sur un lecteur réseau (Z:), renvoie le chemin réseau équivalent (\\\\NAS\\partage\\…).

    Une tâche lancée avec les droits administrateur ne voit pas les lettres de lecteurs réseau de l'utilisateur.
    """
    drive, rest = os.path.splitdrive(path or "")
    if len(drive) != 2 or drive[1] != ":":
        return ""
    if backup.drive_type(drive + "\\") != backup.DRIVE_REMOTE:
        return ""
    try:
        import win32wnet

        unc = win32wnet.WNetGetConnection(drive)
    except Exception:  # noqa: BLE001
        return ""
    return unc.rstrip("\\") + (rest if rest.startswith("\\") else "\\" + rest if rest else "")


# =========================================================================== exécution sans interface

@dataclass
class RunOutcome:
    code: int          # code de sortie du processus
    action: str        # disabled | busy | not_due | missing | success | warning | error
    message: str = ""


Notifier = Callable[[str, str, str], None]


def run_scheduled(force: bool = False, settings=None, status_file: Path | None = None,
                  now: datetime | None = None, notify: Notifier | None = None,
                  lock: BackupLock | None = None) -> RunOutcome:
    """Ce que fait ``--backup`` : décide, sauvegarde si besoin, enregistre l'état et prévient l'utilisateur.

    `force` : sauvegarde même si elle n'est pas due ou si la sauvegarde automatique est désactivée (test).
    """
    from app.utils.fmt import human_size
    from app.utils.settings import Settings

    settings = settings if settings is not None else Settings()
    notify = notify or notify_windows
    now = now or datetime.now()
    cfg = AutoConfig.from_settings(settings)
    if not cfg.enabled and not force:
        log.info("Sauvegarde automatique désactivée : rien à faire.")
        return RunOutcome(0, "disabled")

    lock = lock or BackupLock()
    if not lock.acquire():
        log.info("Une sauvegarde est déjà en cours : rien à faire.")
        return RunOutcome(0, "busy")
    try:
        status = load_status(status_file)
        last_start = backup.parse_time(status.get("last_success_started"))
        if not force and not is_due(last_start, cfg.hours, now):
            log.info("Sauvegarde automatique pas encore due (dernière : %s, fréquence %s h).", last_start, cfg.hours)
            return RunOutcome(0, "not_due")

        status["last_attempt"] = _iso(now)
        status["destination"] = cfg.destination
        if not cfg.destination or not os.path.isdir(cfg.destination):
            return _disk_missing(cfg, status, status_file, now, notify)
        status.pop("disk_missing_since", None)
        status.pop("last_reminder", None)
        if not cfg.sources:
            return _failed(status, status_file, now, notify, "Aucun dossier à sauvegarder n'est sélectionné.")

        repo = backup.repository_for(cfg.destination)
        backup.remove_incomplete(repo)      # une sauvegarde interrompue ne doit pas bloquer les suivantes
        try:
            sets = backup.list_sets(repo)
        except OSError:
            sets = []
        last_full = max((s.created for s in sets if s.kind == FULL and s.complete), default=None)
        kind = choose_kind(cfg.mode, cfg.full_weeks, last_full, now)
        log.info("Sauvegarde automatique : %s vers %s (%d dossier(s)).", KIND_LABELS[kind], cfg.destination,
                 len(cfg.sources))
        try:
            plan = backup.plan_backup(cfg.sources, cfg.destination, kind)
            try:
                free = shutil.disk_usage(cfg.destination).free
            except OSError:
                free = None
            if free is not None and plan.copy_bytes > free:
                return _failed(status, status_file, now, notify,
                               f"Place insuffisante sur {cfg.destination} : {human_size(plan.copy_bytes)} à copier, "
                               f"{human_size(free)} libres. Libérez de la place ou réduisez « Sauvegardes complètes "
                               "à conserver ».")
            result = backup.run_backup(plan, keep_full=cfg.keep_full)
        except backup.RepoBusy:
            log.info("Une autre sauvegarde de ce PC écrit sur ce disque (autre compte Windows) : rien à faire.")
            return RunOutcome(0, "busy")
        except (OSError, ValueError) as exc:
            log.exception("Sauvegarde automatique impossible")
            return _failed(status, status_file, now, notify, str(exc))

        if result.cancelled:
            reason = ("Le disque de sauvegarde est plein." if result.disk_full
                      else "La sauvegarde a été interrompue.")
            return _failed(status, status_file, now, notify, reason)

        level = WARNING if result.errors else OK
        status.update({
            "result": level, "last_success": _iso(datetime.now()), "last_success_started": _iso(now),
            "last_kind": result.kind, "last_set": result.set_id, "copied": result.copied,
            "copied_bytes": result.copied_bytes, "file_count": result.file_count, "errors": len(result.errors),
            "message": "",
        })
        status.pop("last_error_alert", None)
        status.pop("last_error_message", None)
        save_status(status, status_file)
        msg = (f"Sauvegarde {KIND_LABELS[result.kind].lower()} terminée : {result.copied} fichier(s) copié(s) "
               f"({human_size(result.copied_bytes)}).")
        if result.errors:
            msg += f" {len(result.errors)} fichier(s) non copié(s) (ouverts dans un programme ?)."
        log.info(msg)
        notify("Sauvegarde automatique", msg, "warning" if result.errors else "success")
        return RunOutcome(0, level, msg)
    finally:
        lock.release()


def _disk_missing(cfg: AutoConfig, status: dict, status_file: Path | None, now: datetime,
                  notify: Notifier) -> RunOutcome:
    since = backup.parse_time(status.get("disk_missing_since")) or now
    status["disk_missing_since"] = _iso(since)
    status["result"] = MISSING
    where = cfg.destination or "(aucune destination choisie)"
    status["message"] = f"Disque de sauvegarde absent : {where}"
    message = status["message"]
    if should_remind_missing(since, backup.parse_time(status.get("last_reminder")), now):
        status["last_reminder"] = _iso(now)
        notify("Sauvegarde en attente",
               f"Le disque de sauvegarde ({where}) n'a pas été branché depuis {days_text(now - since)}. "
               "Branchez-le : la sauvegarde se fera toute seule.", "warning")
    save_status(status, status_file)
    log.info(message)
    return RunOutcome(0, MISSING, message)


def _failed(status: dict, status_file: Path | None, now: datetime, notify: Notifier, message: str) -> RunOutcome:
    status["result"] = ERROR
    status["message"] = message
    if should_alert_failure(message, status.get("last_error_message", ""),
                            backup.parse_time(status.get("last_error_alert")), now):
        status["last_error_alert"] = _iso(now)
        status["last_error_message"] = message
        notify("Sauvegarde automatique impossible", message, "error")
    save_status(status, status_file)
    log.warning("Sauvegarde automatique impossible : %s", message)
    return RunOutcome(1, ERROR, message)


def notify_windows(title: str, message: str, level: str = "info") -> None:
    """Notification Windows sans fenêtre principale : une fenêtre Tk invisible porte l'icône de notification."""
    try:
        import tkinter as tk

        from app.utils import notify as winnotify
        from app.utils.paths import resource_path

        root = tk.Tk()
        root.withdraw()
        root.update_idletasks()
        hwnd = int(root.wm_frame(), 16)
        notifier = winnotify.WindowsNotifier(hwnd, resource_path("assets/icon.ico"), APP_NAME)
        notifier.show(title, message, level)
        # Laisser à Windows le temps d'afficher la notification avant de retirer l'icône.
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            root.update()
            time.sleep(0.1)
        notifier.remove()
        root.destroy()
    except Exception:  # noqa: BLE001 - une notification ratée ne doit pas faire échouer la sauvegarde
        log.debug("Notification de sauvegarde impossible", exc_info=True)
