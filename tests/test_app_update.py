"""Mises à jour de l'application : versions, choix de la release, vérification du téléchargement."""

import hashlib

import pytest

from app.core import app_update
from app.core.app_update import Asset, Release, UpdateError

REPO = "NPone29/NPone29Cleaner"


def release(tag, prerelease=False, draft=False, digest="sha256:" + "a" * 64, setup=True):
    assets = [{"name": "NPone29Cleaner.exe", "size": 10,
               "browser_download_url": f"https://github.com/{REPO}/releases/download/{tag}/NPone29Cleaner.exe",
               "digest": "sha256:" + "b" * 64}]
    if setup:
        name = f"NPone29Cleaner-Setup-{tag.lstrip('v')}.exe"
        assets.append({"name": name, "size": 20, "digest": digest,
                       "browser_download_url": f"https://github.com/{REPO}/releases/download/{tag}/{name}"})
    return {"tag_name": tag, "name": f"NPone29 Cleaner {tag}", "body": "## Nouveautés\n- **Rapide**",
            "html_url": f"https://github.com/{REPO}/releases/tag/{tag}", "prerelease": prerelease,
            "draft": draft, "assets": assets}


def test_versions():
    assert app_update.is_newer("v1.4.0", "1.3.0")
    assert app_update.is_newer("1.3.1", "1.3.0")
    assert app_update.is_newer("1.10.0", "1.9.9")
    assert not app_update.is_newer("1.3.0", "1.3.0")
    assert not app_update.is_newer("v1.2.9", "1.3.0")
    assert app_update.is_newer("1.4.0-beta.1", "1.3.0")
    assert app_update.is_newer("1.4.0", "1.4.0-beta.2")          # la finale passe après la bêta
    assert not app_update.is_newer("1.4.0-beta.1", "1.4.0")
    assert app_update.is_newer("1.4", "1.3.0")
    assert not app_update.is_newer("nimporte-quoi", "1.3.0")


def test_pick_latest_stable_release():
    found = app_update.pick_update([release("v1.3.1"), release("v1.5.0", draft=True), release("v1.4.0"),
                                    release("v1.2.0")], current="1.3.0")
    assert found.version == "1.4.0"
    assert found.setup.name == "NPone29Cleaner-Setup-1.4.0.exe"
    assert found.setup.sha256 == "a" * 64
    assert found.portable.name == "NPone29Cleaner.exe"


def test_prereleases_only_for_beta_users():
    releases = [release("v1.4.0-beta.1", prerelease=True), release("v1.3.1")]
    assert app_update.pick_update(releases, current="1.3.0").version == "1.3.1"
    assert app_update.pick_update(releases, current="1.3.0-beta.3").version == "1.4.0-beta.1"
    assert app_update.pick_update(releases, current="1.3.0", include_prerelease=True).version == "1.4.0-beta.1"


def test_no_update_when_up_to_date():
    assert app_update.pick_update([release("v1.3.0"), release("v1.2.0")], current="1.3.0") is None
    assert app_update.pick_update([], current="1.3.0") is None


def test_install_kind(tmp_path):
    exe = tmp_path / "NPone29Cleaner.exe"
    exe.write_bytes(b"")
    assert app_update.install_kind(frozen=False) == "dev"
    assert app_update.install_kind(frozen=True, executable=str(exe)) == "portable"
    (tmp_path / "unins000.exe").write_bytes(b"")
    assert app_update.install_kind(frozen=True, executable=str(exe)) == "installed"


def test_can_install_needs_installer_with_digest():
    ok = app_update.parse_release(release("v1.4.0"))
    assert app_update.can_install(ok, kind="installed")
    assert not app_update.can_install(ok, kind="portable")
    assert not app_update.can_install(app_update.parse_release(release("v1.4.0", digest="")), kind="installed")
    assert not app_update.can_install(app_update.parse_release(release("v1.4.0", setup=False)), kind="installed")


def _release_with(asset):
    return Release("1.4.0", "v1.4.0", "", "", "", False, asset, None)


def test_download_refuses_other_hosts_and_repos(monkeypatch):
    monkeypatch.setattr("app.utils.download.download_file",
                        lambda *a, **k: pytest.fail("ne doit pas télécharger"))
    for url in ("https://evil.example/NPone29Cleaner-Setup-1.4.0.exe",
                "http://github.com/NPone29/NPone29Cleaner/releases/download/v1.4.0/x.exe",
                "https://github.com/autre/depot/releases/download/v1.4.0/x.exe",
                "https://evilgithub.com/NPone29/NPone29Cleaner/releases/download/v1.4.0/x.exe"):
        with pytest.raises(UpdateError):
            app_update.download_installer(_release_with(Asset("x.exe", url, 0, "a" * 64)), repo=REPO)


def test_download_checks_hash(monkeypatch, tmp_path):
    payload = b"installateur"
    good = hashlib.sha256(payload).hexdigest()

    def fake_download(url, hosts, progress=None, cancel=None):
        path = tmp_path / "setup.exe"
        path.write_bytes(payload)
        return path

    monkeypatch.setattr("app.utils.download.download_file", fake_download)
    url = f"https://github.com/{REPO}/releases/download/v1.4.0/NPone29Cleaner-Setup-1.4.0.exe"
    path = app_update.download_installer(_release_with(Asset("s.exe", url, len(payload), good)), repo=REPO)
    assert path.read_bytes() == payload

    with pytest.raises(UpdateError):
        app_update.download_installer(_release_with(Asset("s.exe", url, len(payload), "0" * 64)), repo=REPO)
    assert not (tmp_path / "setup.exe").exists()      # fichier douteux supprimé


def test_short_notes():
    text = app_update.short_notes("## Nouveautés\n<!-- caché -->\n- **Rapide** [lien](https://x)\n\n\n\nFin")
    assert text == "Nouveautés\n\n- Rapide lien\n\nFin"
    assert app_update.short_notes("ligne\n" * 500, limit=50).endswith("…")


def test_installer_command_waits_for_app_and_relaunches(tmp_path):
    cmd = app_update.installer_command(tmp_path / "setup.exe", [123, 456])
    assert cmd[0].endswith("setup.exe")
    assert "/SILENT" in cmd and "/RELAUNCH=1" in cmd and "/CLOSEAPPLICATIONS" in cmd
    assert cmd[-1] == "/WAITPID=123,456"


def test_wheel_scrolls_like_a_browser():
    from app.ui.widgets import SCROLL_PIXELS, wheel_pixels

    assert wheel_pixels(-120) == SCROLL_PIXELS          # un cran vers le bas
    assert wheel_pixels(120) == -SCROLL_PIXELS          # un cran vers le haut
    assert wheel_pixels(-240, scaling=1.25) == 250      # deux crans, écran agrandi à 125 %
    assert wheel_pixels(-1) == 1                        # pavé tactile : jamais zéro
    assert wheel_pixels(0) == 0
