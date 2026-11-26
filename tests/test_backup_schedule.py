"""Tests de la sauvegarde automatique : décisions, tâche planifiée (XML seulement), verrou, mode --backup.

Aucune vraie tâche planifiée n'est créée ici.
"""

from __future__ import annotations

import os
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from app.core import backup, backup_schedule as bs
from app.core.backup import DIFFERENTIAL, FULL, INCREMENTAL

NOW = datetime(2026, 11, 26, 14, 0, 3)
NS = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}


class FakeSettings(dict):
    def get(self, key, default=None):
        return super().get(key, default)


def _settings(dest: Path, sources: list[Path], **kw) -> FakeSettings:
    data = FakeSettings(backup_auto=True, backup_auto_hours=4, backup_auto_mode=INCREMENTAL,
                        backup_auto_full_weeks=0, backup_destination=str(dest),
                        backup_sources=[str(s) for s in sources], backup_keep_full=0)
    data.update(kw)
    return data


# --------------------------------------------------------------------------- décisions

def test_due_when_never_done():
    assert bs.is_due(None, 24, NOW)


def test_due_with_tolerance_for_hourly_trigger():
    # Lancée à 10:00:05, vérifiée à 14:00:03 : due (sinon elle attendrait 15 h).
    assert bs.is_due(datetime(2026, 11, 26, 10, 0, 5), 4, NOW)
    assert not bs.is_due(datetime(2026, 11, 26, 11, 0, 5), 4, NOW)


def test_due_daily_and_weekly():
    assert not bs.is_due(NOW - timedelta(hours=20), 24, NOW)
    assert bs.is_due(NOW - timedelta(hours=24), 24, NOW)
    assert not bs.is_due(NOW - timedelta(days=6), 168, NOW)
    assert bs.is_due(NOW - timedelta(days=7), 168, NOW)


def test_due_if_clock_went_back():
    assert bs.is_due(NOW + timedelta(days=1), 24, NOW)


def test_choose_kind():
    assert bs.choose_kind(FULL, 0, None, NOW) == FULL
    assert bs.choose_kind(INCREMENTAL, 0, None, NOW) == INCREMENTAL      # plan_backup gère la 1re complète
    assert bs.choose_kind(INCREMENTAL, 2, None, NOW) == FULL
    assert bs.choose_kind(DIFFERENTIAL, 2, NOW - timedelta(days=10), NOW) == DIFFERENTIAL
    assert bs.choose_kind(DIFFERENTIAL, 2, NOW - timedelta(days=14), NOW) == FULL


def test_missing_disk_reminder_once_a_day():
    since = NOW - timedelta(days=9)
    assert not bs.should_remind_missing(NOW - timedelta(days=3), None, NOW)
    assert bs.should_remind_missing(since, None, NOW)
    assert not bs.should_remind_missing(since, NOW - timedelta(hours=5), NOW)
    assert bs.should_remind_missing(since, NOW - timedelta(days=1), NOW)


def test_same_failure_alerted_once_a_day():
    assert bs.should_alert_failure("plein", "", None, NOW)
    assert not bs.should_alert_failure("plein", "plein", NOW - timedelta(hours=1), NOW)
    assert bs.should_alert_failure("autre", "plein", NOW - timedelta(hours=1), NOW)
    assert bs.should_alert_failure("plein", "plein", NOW - timedelta(days=1), NOW)


def test_frequency_labels_cover_4h_and_daily():
    assert bs.FREQUENCIES["Toutes les 4 heures"] == 4
    assert bs.frequency_label(24) == "Chaque jour"


def test_configured_sources_match_page_defaults(monkeypatch, tmp_path):
    monkeypatch.setattr(backup, "default_sources", lambda: [("Documents", "D"), ("Musique", "M"), ("Bureau", "B")])
    assert bs.configured_sources(FakeSettings(backup_custom=["X"])) == ["D", "B", "X"]
    assert bs.configured_sources(FakeSettings(backup_sources=["M"], backup_custom=["X"])) == ["M"]


def test_last_backup_picks_most_recent():
    manual = {"finished": "2026-11-20T10:00:00", "kind": FULL, "destination": "E:\\"}
    status = {"last_success": "2026-11-25T10:00:00", "last_kind": INCREMENTAL, "destination": "E:\\"}
    assert bs.last_backup(manual, status)["auto"] is True
    assert bs.last_backup({**manual, "finished": "2026-11-26T08:00:00"}, status)["kind"] == FULL
    assert bs.last_backup({}, {}) == {}


# --------------------------------------------------------------------------- tâche planifiée

def test_task_command_frozen_and_dev(tmp_path):
    exe = r"C:\Apps\NPone29\NPone29Cleaner.exe"
    assert bs.task_command(True, exe) == (exe, "--backup", r"C:\Apps\NPone29")
    prog, args, wd = bs.task_command(False, r"C:\Python315\python.exe", r"C:\src\NPone29Cleaner")
    assert prog == r"C:\Python315\pythonw.exe"
    assert args == r'"C:\src\NPone29Cleaner\main.py" --backup'
    assert wd == r"C:\src\NPone29Cleaner"


def test_task_xml_is_valid_and_complete():
    xml = bs.task_xml(r"C:\A & B\NPone29Cleaner.exe", "--backup", r"C:\A & B", "S-1-5-21-1-2-3-1001",
                      datetime(2026, 11, 26, 14, 5))
    root = ET.fromstring(xml.split("?>", 1)[1])       # ElementTree refuse la déclaration UTF-16 sur du texte
    get = lambda path: root.find(path, NS).text      # noqa: E731
    assert get("t:Actions/t:Exec/t:Command") == r"C:\A & B\NPone29Cleaner.exe"
    assert get("t:Actions/t:Exec/t:Arguments") == "--backup"
    assert get("t:Principals/t:Principal/t:LogonType") == "InteractiveToken"
    assert get("t:Principals/t:Principal/t:RunLevel") == "HighestAvailable"
    assert get("t:Settings/t:MultipleInstancesPolicy") == "IgnoreNew"
    assert get("t:Settings/t:StartWhenAvailable") == "true"
    assert get("t:Settings/t:DisallowStartIfOnBatteries") == "false"
    assert get("t:Settings/t:StopIfGoingOnBatteries") == "false"
    assert get("t:Triggers/t:TimeTrigger/t:Repetition/t:Interval") == "PT1H"
    assert get("t:Triggers/t:TimeTrigger/t:StartBoundary") == "2026-11-26T14:05:00"
    assert get("t:Triggers/t:LogonTrigger/t:UserId") == "S-1-5-21-1-2-3-1001"


def test_task_info_detects_moved_exe():
    text = bs.task_xml(r"C:\Old\NPone29Cleaner.exe", "--backup", r"C:\Old", "u", NOW)
    info = bs.parse_task_xml(text)
    assert info.command == r"C:\Old\NPone29Cleaner.exe"
    assert info.matches(r"c:\old\npone29cleaner.exe", "--backup")
    assert not info.matches(r"C:\New\NPone29Cleaner.exe", "--backup")
    assert bs.parse_task_xml("ERREUR : la tâche n'existe pas") is None


# --------------------------------------------------------------------------- verrou

def test_lock_is_exclusive(tmp_path):
    first, second = bs.BackupLock(tmp_path / "l"), bs.BackupLock(tmp_path / "l")
    assert first.acquire()
    assert not second.acquire()
    first.release()
    assert second.acquire()
    second.release()


# --------------------------------------------------------------------------- mode --backup

@pytest.fixture
def env(tmp_path):
    src = tmp_path / "Documents"
    (src / "sub").mkdir(parents=True)
    (src / "a.txt").write_text("A", encoding="utf-8")
    (src / "sub" / "b.txt").write_text("B", encoding="utf-8")
    dest = tmp_path / "USB"
    dest.mkdir()
    return src, dest, tmp_path / "status.json", tmp_path / "lock"


def _run(settings, status, lock_file, now=NOW, force=False):
    notes: list[tuple[str, str, str]] = []
    outcome = bs.run_scheduled(force=force, settings=settings, status_file=status, now=now,
                               notify=lambda *a: notes.append(a), lock=bs.BackupLock(lock_file))
    return outcome, notes


def test_disabled_does_nothing(env):
    src, dest, status, lock = env
    outcome, notes = _run(_settings(dest, [src], backup_auto=False), status, lock)
    assert outcome.action == "disabled" and outcome.code == 0
    assert not status.exists() and not notes


def test_backup_then_not_due_then_due(env):
    src, dest, status, lock = env
    settings = _settings(dest, [src])
    outcome, notes = _run(settings, status, lock)
    assert outcome.action == bs.OK and outcome.code == 0
    sets = backup.list_sets(backup.repository_for(str(dest)))
    assert len(sets) == 1 and sets[0].kind == FULL      # première sauvegarde : complète
    saved = bs.load_status(status)
    assert saved["last_success_started"] == NOW.isoformat(timespec="seconds")
    assert saved["copied"] == 2 and notes[0][2] == "success"

    outcome, _ = _run(settings, status, lock, now=NOW + timedelta(hours=1))
    assert outcome.action == "not_due"

    (src / "c.txt").write_text("C", encoding="utf-8")
    outcome, _ = _run(settings, status, lock, now=NOW + timedelta(hours=4))
    assert outcome.action == bs.OK
    sets = backup.list_sets(backup.repository_for(str(dest)))
    assert [s.kind for s in sets] == [FULL, INCREMENTAL]
    assert bs.load_status(status)["copied"] == 1


def test_full_every_n_weeks(env):
    src, dest, status, lock = env
    settings = _settings(dest, [src], backup_auto_full_weeks=1, backup_auto_hours=24)
    _run(settings, status, lock)
    _run(settings, status, lock, now=NOW + timedelta(days=1))
    # Les sauvegardes sont datées de l'heure réelle : la 3e est simulée 8 jours après la complète.
    repo = backup.repository_for(str(dest))
    assert [s.kind for s in backup.list_sets(repo)] == [FULL, INCREMENTAL]
    later = datetime.now() + timedelta(days=8)
    outcome, _ = _run(settings, status, lock, now=later)
    assert outcome.action == bs.OK
    kinds = [s.kind for s in backup.list_sets(repo)]
    assert kinds.count(FULL) == 2 and kinds.count(INCREMENTAL) == 1


def test_missing_disk_waits_then_reminds(env, tmp_path):
    src, _dest, status, lock = env
    settings = _settings(tmp_path / "Disque débranché", [src])
    outcome, notes = _run(settings, status, lock)
    assert outcome.action == bs.MISSING and outcome.code == 0 and not notes
    assert bs.load_status(status)["disk_missing_since"] == NOW.isoformat(timespec="seconds")

    outcome, notes = _run(settings, status, lock, now=NOW + timedelta(days=8))
    assert outcome.action == bs.MISSING and len(notes) == 1 and notes[0][2] == "warning"
    assert "8 jours" in notes[0][1]
    _, notes = _run(settings, status, lock, now=NOW + timedelta(days=8, hours=2))
    assert not notes                                     # pas de rappel toutes les heures


def test_disk_back_clears_missing_state(env, tmp_path):
    src, dest, status, lock = env
    bs.save_status({"disk_missing_since": "2026-11-01T10:00:00"}, status)
    outcome, _ = _run(_settings(dest, [src]), status, lock)
    assert outcome.action == bs.OK
    assert "disk_missing_since" not in bs.load_status(status)


def test_busy_when_another_backup_runs(env):
    src, dest, status, lock = env
    holder = bs.BackupLock(lock)
    assert holder.acquire()
    try:
        outcome, _ = _run(_settings(dest, [src]), status, lock)
    finally:
        holder.release()
    assert outcome.action == "busy"
    assert not backup.list_sets(backup.repository_for(str(dest)))


def test_failure_exit_code_and_single_alert(env):
    src, dest, status, lock = env
    settings = _settings(dest, [], backup_sources=[])
    outcome, notes = _run(settings, status, lock)
    assert outcome.code == 1 and outcome.action == bs.ERROR and notes[0][2] == "error"
    _, notes = _run(settings, status, lock, now=NOW + timedelta(hours=1))
    assert not notes


def test_force_runs_even_if_disabled_or_not_due(env):
    src, dest, status, lock = env
    settings = _settings(dest, [src], backup_auto=False)
    outcome, _ = _run(settings, status, lock, force=True)
    assert outcome.action == bs.OK
    outcome, _ = _run(settings, status, lock, now=NOW + timedelta(minutes=5), force=True)
    assert outcome.action == bs.OK


def test_network_drive_target_ignores_local_paths():
    assert bs.network_drive_target(r"\\NAS\partage") == ""
    assert bs.network_drive_target(os.environ.get("SystemDrive", "C:") + "\\") == ""
