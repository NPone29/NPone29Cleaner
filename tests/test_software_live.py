"""Suivi en direct d'une mise à jour winget : messages, téléchargement, activité, textes affichés."""

import sys
import time

import pytest

from app.core import software
from app.core.software import LiveStatus, SoftwareUpdate, UpgradeWatcher
from app.utils.system import run_cancellable

OUTPUT_FR = (
    "Trouvé 7-Zip [7zip.7zip] Version 26.06\r\n"
    "La licence d’utilisation de cette application vous est octroyée par son propriétaire.\r\n"
    "Téléchargement en cours https://github.com/ip7z/7zip/releases/download/26.06/7z2606-x64.msi\r\n"
    "  ██████▒▒▒▒▒▒  \r  ████████████  \r\n"
)


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def watcher(tmp_path, size=0):
    update = SoftwareUpdate("7-Zip", "7zip.7zip", "24.09", "26.06", "winget")
    clock = FakeClock()
    return UpgradeWatcher(update, clock=clock, size_probe=lambda url: size, download_dir=tmp_path), clock


def test_output_messages_hide_progress_bars_and_shorten_urls():
    messages = software.output_messages(OUTPUT_FR)
    assert messages[0] == "Trouvé 7-Zip [7zip.7zip] Version 26.06"
    assert messages[-1] == "Téléchargement en cours github.com"
    assert not any("█" in m for m in messages)


def test_download_url():
    assert software.download_url(OUTPUT_FR).endswith("/7z2606-x64.msi")
    assert software.download_url("Trouvé 7-Zip") is None


def test_clock_text():
    assert software.clock_text(7) == "7 s"
    assert software.clock_text(75) == "1 min 15 s"
    assert software.clock_text(3725) == "1 h 02 min"


def test_download_progress_from_winget_folder(tmp_path):
    w, clock = watcher(tmp_path, size=4000)
    folder = tmp_path / "7zip.7zip.26.06"
    folder.mkdir()
    (folder / "7z.msi").write_bytes(b"x" * 1000)
    clock.now += 1
    st = w.snapshot(None, OUTPUT_FR)
    w._probe.join(5)
    clock.now += 1
    (folder / "7z.msi").write_bytes(b"x" * 2000)
    st = w.snapshot(None, OUTPUT_FR)
    assert st.downloading
    assert st.downloaded == 2000 and st.total == 4000
    assert st.fraction == pytest.approx(0.5)
    assert st.speed == pytest.approx(1000)
    assert st.idle == 0


def test_other_packages_are_ignored(tmp_path):
    w, clock = watcher(tmp_path)
    (tmp_path / "Mozilla.Firefox.140.0").mkdir()
    (tmp_path / "Mozilla.Firefox.140.0" / "setup.exe").write_bytes(b"x" * 500)
    clock.now += 1
    assert w.snapshot(None, OUTPUT_FR).downloaded == 0


def test_idle_grows_without_activity(tmp_path):
    w, clock = watcher(tmp_path)
    w.snapshot(None, OUTPUT_FR)
    clock.now += 120
    st = w.snapshot(None, OUTPUT_FR)      # même sortie, aucun téléchargement, aucun processus
    assert st.idle == pytest.approx(120)
    assert st.stalled
    clock.now += 1
    st = w.snapshot(None, OUTPUT_FR + "Démarrage de l'installation du package...\r\n")
    assert st.idle == 0 and not st.downloading
    assert st.message == "Démarrage de l'installation du package..."


def test_describe_texts():
    step, activity, level = software.describe(LiveStatus(
        message="Téléchargement en cours github.com", downloading=True, downloaded=1024 * 1024,
        total=4 * 1024 * 1024, speed=512 * 1024))
    assert step == "Téléchargement en cours github.com — 1,0 Mo sur 4,0 Mo (25 %), 512 Ko/s"
    assert level == "ok"

    _, activity, level = software.describe(LiveStatus(message="Démarrage de l'installation…",
                                                      installer="setup.exe", cpu=12, idle=2))
    assert activity == "Actif : « setup.exe » travaille, processeur 12 %."
    assert level == "ok"

    _, activity, level = software.describe(LiveStatus(installer="setup.exe", idle=30))
    assert level == "wait" and "aucune activité depuis 30 s" in activity

    _, activity, level = software.describe(LiveStatus(installer="setup.exe", idle=200))
    assert level == "warning" and "Passer celui-ci" in activity

    _, activity, level = software.describe(LiveStatus(installer="setup.exe", idle=200,
                                                      windows=((1, "Installation de Truc"),)))
    assert level == "warning" and "Afficher la fenêtre" in activity


@pytest.mark.skipif(sys.platform != "win32", reason="Windows uniquement")
def test_run_cancellable_reports_output_while_running():
    seen = []
    script = "echo premier & ping -n 3 127.0.0.1 >nul & echo second"
    start = time.monotonic()
    result = run_cancellable(["cmd", "/c", script], timeout=30, on_tick=lambda pid, out: seen.append(out),
                             tick=0.3)
    assert result.ok
    assert "premier" in result.stdout and "second" in result.stdout
    assert any("premier" in out and "second" not in out for out in seen)   # vu avant la fin
    assert time.monotonic() - start < 20


@pytest.mark.skipif(sys.platform != "win32", reason="Windows uniquement")
def test_on_tick_error_does_not_break_command():
    def boom(pid, out):
        raise RuntimeError("affichage")
    result = run_cancellable(["cmd", "/c", "ping -n 2 127.0.0.1 >nul & echo fin"], timeout=30, on_tick=boom,
                             tick=0.2)
    assert result.ok and "fin" in result.stdout
