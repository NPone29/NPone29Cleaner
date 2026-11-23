"""Tests du moteur de nettoyage, sur des dossiers temporaires créés pour l'occasion."""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

import pytest

from app.core import cleaner
from app.core.cleaner import CleanTarget, clean_target, iter_target_files, scan_target


def _make(path: Path, size: int = 10, age_hours: float = 0) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)
    if age_hours:
        t = time.time() - age_hours * 3600
        os.utime(path, (t, t))
    return path


def _target(root: Path, **kw) -> CleanTarget:
    return CleanTarget("test", "Test", "Windows", "", lambda: [root], **kw)


def test_scan_counts_files_and_sizes(tmp_path):
    _make(tmp_path / "a.tmp", 100)
    _make(tmp_path / "sub" / "b.tmp", 50)
    result = scan_target(_target(tmp_path))
    assert result.files == 2
    assert result.size == 150


def test_scan_does_not_delete(tmp_path):
    f = _make(tmp_path / "a.tmp")
    scan_target(_target(tmp_path))
    assert f.exists()


def test_clean_removes_contents_but_keeps_root(tmp_path):
    _make(tmp_path / "a.tmp", 100)
    _make(tmp_path / "sub" / "deep" / "b.tmp", 50)
    result = clean_target(_target(tmp_path))
    assert result.deleted == 2
    assert result.freed == 150
    assert tmp_path.exists()
    assert list(tmp_path.iterdir()) == []  # sous-dossiers vides supprimés aussi


def test_min_age_keeps_recent_files(tmp_path):
    old = _make(tmp_path / "old.tmp", 10, age_hours=5)
    new = _make(tmp_path / "new.tmp", 10)
    result = clean_target(_target(tmp_path, min_age_hours=1))
    assert not old.exists()
    assert new.exists()
    assert result.deleted == 1


def test_patterns_and_non_recursive(tmp_path):
    keep = _make(tmp_path / "keep.txt")
    match = _make(tmp_path / "thumbcache_32.db")
    nested = _make(tmp_path / "sub" / "thumbcache_64.db")
    clean_target(_target(tmp_path, patterns=("thumbcache_*.db",), recursive=False))
    assert keep.exists()
    assert not match.exists()
    assert nested.exists()


def test_readonly_files_are_deleted(tmp_path):
    f = _make(tmp_path / "ro.tmp")
    os.chmod(f, 0o444)
    clean_target(_target(tmp_path))
    assert not f.exists()


def test_single_file_target(tmp_path):
    f = _make(tmp_path / "MEMORY.DMP", 42)
    other = _make(tmp_path / "other.txt")
    result = clean_target(CleanTarget("t", "T", "Windows", "", lambda: [f]))
    assert result.freed == 42
    assert not f.exists()
    assert other.exists()


def test_missing_paths_are_ignored(tmp_path):
    result = scan_target(_target(tmp_path / "does-not-exist"))
    assert result.files == 0


def test_excluded_roots_are_never_touched(tmp_path):
    protected = _make(tmp_path / "_MEI1234" / "base_library.zip")
    _make(tmp_path / "junk.tmp")
    files = [p for p, _ in iter_target_files(_target(tmp_path), excluded=[str(tmp_path / "_MEI1234")])]
    assert str(protected) not in files
    assert len(files) == 1


def test_junctions_are_not_followed(tmp_path):
    outside = tmp_path / "outside"
    precious = _make(outside / "precious.docx")
    inside = tmp_path / "temp"
    inside.mkdir()
    junction = inside / "link"
    rc = subprocess.run(["cmd", "/c", "mklink", "/J", str(junction), str(outside)],
                        capture_output=True).returncode
    if rc != 0:
        pytest.skip("Impossible de créer une jonction sur ce système")
    clean_target(_target(inside))
    assert precious.exists(), "le nettoyage ne doit jamais suivre une jonction"


def test_cancel_stops_scan(tmp_path):
    import threading

    for i in range(20):
        _make(tmp_path / f"d{i}" / "f.tmp")
    cancel = threading.Event()
    cancel.set()
    assert scan_target(_target(tmp_path), cancel).files == 0


def test_before_and_after_hooks(tmp_path):
    calls = []
    target = _target(tmp_path, before_clean=lambda: calls.append("before") or "state",
                     after_clean=lambda state: calls.append(("after", state)))
    clean_target(target)
    assert calls == ["before", ("after", "state")]


def test_chromium_cache_dirs(tmp_path):
    user_data = tmp_path / "User Data"
    for d in ("Default/Cache", "Profile 1/Code Cache", "Default/Login Data Dir", "ShaderCache", "Other/Cache"):
        (user_data / d).mkdir(parents=True)
    found = {p.relative_to(user_data).as_posix() for p in cleaner.chromium_cache_dirs(user_data)}
    assert found == {"Default/Cache", "Profile 1/Code Cache", "ShaderCache"}


def test_build_targets_have_unique_ids_and_known_categories():
    targets = cleaner.build_targets()
    ids = [t.id for t in targets]
    assert len(ids) == len(set(ids))
    assert {t.category for t in targets} <= set(cleaner.CATEGORY_ORDER)
    for t in targets:
        assert isinstance(t.locate(), list)
