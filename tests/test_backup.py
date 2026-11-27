"""Tests du moteur de sauvegarde : complète / différentielle / incrémentale, restauration exacte."""

from __future__ import annotations

import gzip
import json
import os
import threading
from pathlib import Path

import pytest

from app.core import backup
from app.core.backup import DIFFERENTIAL, FOLDER, FULL, INCREMENTAL, ORIGINAL

T0 = 1_700_000_000  # dates de modification fixes : indépendantes de la vitesse des tests


def _write(path: Path, text: str, mtime: int = T0) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    os.utime(path, (mtime, mtime))
    return path


def _tree(root: Path) -> dict[str, str]:
    return {p.relative_to(root).as_posix(): p.read_text(encoding="utf-8")
            for p in sorted(root.rglob("*")) if p.is_file()}


def _backup(src: Path, dest: Path, mode: str, **kw) -> backup.BackupResult:
    plan = backup.plan_backup([str(src)], str(dest), mode)
    result = backup.run_backup(plan, **kw)
    assert not result.cancelled
    return result


@pytest.fixture
def env(tmp_path):
    src = tmp_path / "Documents"
    dest = tmp_path / "USB"
    dest.mkdir()
    _write(src / "a.txt", "A1")
    _write(src / "sub" / "b.txt", "B1")
    _write(src / "c.txt", "C1")
    return src, dest


def test_normalize_sources_removes_nested_and_missing(tmp_path):
    (tmp_path / "a" / "b").mkdir(parents=True)
    result = backup.normalize_sources([str(tmp_path / "a" / "b"), str(tmp_path / "a"), str(tmp_path / "absent")])
    assert result == [str(tmp_path / "a")]


def test_labels_are_unique():
    labels = backup.source_labels([r"C:\x\Documents", r"D:\Documents", "E:\\"])
    assert labels == {r"C:\x\Documents": "Documents", r"D:\Documents": "Documents (2)", "E:\\": "Disque E"}


def test_labels_stay_stable_and_are_never_reused():
    previous = [{"path": r"D:\Documents", "label": "Documents"}, {"path": r"C:\Old\Photos", "label": "Photos"}]
    labels = backup.source_labels([r"C:\x\Documents", r"D:\Documents", r"C:\New\Photos"], previous)
    # D:\Documents garde son nom ; « Photos » appartenait à un autre dossier : il n'est pas réutilisé.
    assert labels == {r"C:\x\Documents": "Documents (2)", r"D:\Documents": "Documents",
                      r"C:\New\Photos": "Photos (2)"}


def test_full_backup_copies_everything_in_clear(env):
    src, dest = env
    result = _backup(src, dest, FULL)
    assert result.copied == 3 and result.file_count == 3 and not result.errors
    set_dir = result.path
    # Les fichiers sont lisibles directement, rangés selon leur chemin d'origine.
    copy = set_dir / "Documents" / "sub" / "b.txt"
    assert copy.read_text(encoding="utf-8") == "B1"
    assert int(copy.stat().st_mtime) == T0
    assert (set_dir / backup.MANIFEST).is_file() and not (set_dir / backup.MARKER).exists()
    assert (set_dir / backup.SCRIPT).is_file()
    assert (set_dir.parent / backup.README).is_file()
    assert set_dir.parent == backup.repository_for(dest)


def test_incremental_copies_only_changes_and_restores_each_point(env, tmp_path):
    src, dest = env
    first = _backup(src, dest, FULL)

    _write(src / "a.txt", "A2", T0 + 100)          # modifié
    (src / "c.txt").unlink()                        # supprimé
    _write(src / "d.txt", "D1")                     # nouveau
    second = _backup(src, dest, INCREMENTAL)
    assert second.kind == INCREMENTAL
    assert second.copied == 2                       # a.txt + d.txt
    assert not (second.path / "Documents" / "sub").exists()

    _write(src / "sub" / "b.txt", "B2", T0 + 200)
    third = _backup(src, dest, INCREMENTAL)
    assert third.copied == 1

    repo = backup.repository_for(dest)
    sets = backup.list_sets(repo)
    assert [s.id for s in sets] == [first.set_id, second.set_id, third.set_id]
    assert sets[1].requires == [first.set_id]
    assert sets[2].requires == [second.set_id]   # plus aucun fichier ne vient de la complète

    expected = {
        first.set_id: {"a.txt": "A1", "c.txt": "C1", "sub/b.txt": "B1"},
        second.set_id: {"a.txt": "A2", "d.txt": "D1", "sub/b.txt": "B1"},
        third.set_id: {"a.txt": "A2", "d.txt": "D1", "sub/b.txt": "B2"},
    }
    for set_id, files in expected.items():
        out = tmp_path / f"restore_{set_id}"
        res = backup.restore(repo, set_id, FOLDER, str(out))
        assert not res.errors and not res.missing
        assert _tree(out / "Documents") == files      # les fichiers supprimés ne réapparaissent pas


def test_differential_is_cumulative_since_full(env):
    src, dest = env
    full = _backup(src, dest, FULL)
    _write(src / "a.txt", "A2", T0 + 100)
    d1 = _backup(src, dest, DIFFERENTIAL)
    _write(src / "c.txt", "C2", T0 + 100)
    d2 = _backup(src, dest, DIFFERENTIAL)
    assert d1.copied == 1
    assert d2.copied == 2                          # a.txt recopié : on compare toujours à la complète
    sets = {s.id: s for s in backup.list_sets(backup.repository_for(dest))}
    assert sets[d2.set_id].requires == [full.set_id]  # n'a besoin que de la complète


def test_first_incremental_becomes_full(env):
    src, dest = env
    plan = backup.plan_backup([str(src)], str(dest), INCREMENTAL)
    assert plan.kind == FULL and plan.requested == INCREMENTAL and plan.notes


def test_nothing_changed_copies_nothing(env):
    src, dest = env
    _backup(src, dest, FULL)
    plan = backup.plan_backup([str(src)], str(dest), INCREMENTAL)
    assert plan.to_copy == {} and plan.file_count == 3


def test_exclusions_and_backup_folder_inside_source(tmp_path):
    src = tmp_path / "Data"
    _write(src / "doc.txt", "x")
    _write(src / "~$doc.docx", "verrou Word")
    _write(src / "Thumbs.db", "cache")
    # Sauvegarde rangée à l'intérieur du dossier sauvegardé : elle ne doit pas se copier elle-même.
    result = _backup(src, src, FULL)
    assert result.copied == 1
    second = backup.plan_backup([str(src)], str(src), INCREMENTAL)
    assert second.to_copy == {}


def test_cancelled_backup_is_not_used_as_reference(env):
    src, dest = env
    _backup(src, dest, FULL)
    _write(src / "a.txt", "A2", T0 + 100)
    cancel = threading.Event()
    cancel.set()
    plan = backup.plan_backup([str(src)], str(dest), INCREMENTAL)
    interrupted = backup.run_backup(plan, cancel)
    assert interrupted.cancelled
    sets = backup.list_sets(backup.repository_for(dest))
    assert [s.complete for s in sets] == [True]                  # copie partielle supprimée tout de suite
    assert not (backup.repository_for(dest) / interrupted.set_id).exists()
    plan = backup.plan_backup([str(src)], str(dest), INCREMENTAL)
    assert plan.reference.id == sets[0].id and list(plan.to_copy) == ["Documents/a.txt"]


def _leftover_set(dest: Path, name: str = "2020-01-01_10h00_complete") -> Path:
    """Sauvegarde interrompue brutalement (PC éteint, application fermée) : seul le marqueur existe."""
    set_dir = backup.repository_for(dest) / name
    _write(set_dir / "Documents" / "gros.bin", "x" * 1000)
    (set_dir / backup.MARKER).write_text(json.dumps({"kind": FULL, "created": "2020-01-01T10:00:00",
                                                     "complete": False}), encoding="utf-8")
    return set_dir


def test_remove_incomplete_keeps_finished_sets(env):
    src, dest = env
    done = _backup(src, dest, FULL)
    leftover = _leftover_set(dest)
    assert backup.remove_incomplete(backup.repository_for(dest)) == [leftover.name]
    assert not leftover.exists()
    assert [s.id for s in backup.list_sets(backup.repository_for(dest))] == [done.set_id]


def test_remove_incomplete_spares_running_backup(env):
    """Une sauvegarde tient le verrou du dossier (autre compte Windows, PC en veille…) : rien n'est supprimé."""
    src, dest = env
    running = _leftover_set(dest)
    holder = backup.RepoLock(backup.repository_for(dest))
    assert holder.acquire()
    try:
        assert backup.remove_incomplete(backup.repository_for(dest)) == []
    finally:
        holder.release()
    assert running.exists()


def test_remove_incomplete_never_deletes_a_finished_backup(env):
    """Inventaire illisible sur le moment (disque réseau) : la sauvegarde paraît incomplète mais est terminée."""
    src, dest = env
    done = _backup(src, dest, FULL)
    set_dir = backup.repository_for(dest) / done.set_id
    (set_dir / backup.INVENTORY).rename(set_dir / "inventaire_cache")
    assert not backup.list_sets(backup.repository_for(dest))[0].complete
    assert backup.remove_incomplete(backup.repository_for(dest)) == []
    assert set_dir.exists()


def test_backup_and_delete_refused_while_another_backup_writes(env):
    src, dest = env
    done = _backup(src, dest, FULL)
    repo = backup.repository_for(dest)
    holder = backup.RepoLock(repo)
    assert holder.acquire()
    try:
        with pytest.raises(backup.RepoBusy):
            backup.run_backup(backup.plan_backup([str(src)], str(dest), FULL))
        with pytest.raises(backup.RepoBusy):
            backup.delete_sets(repo, [done.set_id])
    finally:
        holder.release()
    assert [s.id for s in backup.list_sets(repo)] == [done.set_id]
    backup.delete_sets(repo, [done.set_id])          # verrou libre : la suppression passe
    assert not backup.list_sets(repo)


def test_disk_without_locks_still_backs_up_but_never_auto_deletes(env, monkeypatch):
    """Lecteur réseau qui refuse les verrous, ou _verrou.lock non modifiable : la sauvegarde ne doit pas être
    bloquée (« une autre sauvegarde est en cours » à tort), mais rien n'est supprimé sans verrou."""
    import errno
    import msvcrt

    src, dest = env
    leftover = _leftover_set(dest)

    def no_locking(fd, mode, nbytes):
        raise OSError(errno.EINVAL, "verrous non pris en charge")

    monkeypatch.setattr(msvcrt, "locking", no_locking)
    result = _backup(src, dest, FULL)
    assert result.set_id and not result.cancelled
    assert backup.remove_incomplete(backup.repository_for(dest)) == []
    assert leftover.exists()
    backup.delete_sets(backup.repository_for(dest), [leftover.name])   # suppression demandée : autorisée
    assert not leftover.exists()


def test_unopenable_lock_file_does_not_block_backup(env, monkeypatch):
    src, dest = env
    real_open = backup.os.open

    def refuse_lock_file(path, *args, **kwargs):
        if str(path).endswith(backup.REPO_LOCK):
            raise PermissionError(13, "Accès refusé", str(path))
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(backup.os, "open", refuse_lock_file)
    assert not _backup(src, dest, FULL).cancelled


def test_backup_deleted_during_copy_is_never_marked_complete(env, monkeypatch):
    src, dest = env
    real_copy = backup.copy_file

    def copy_then_lose_marker(src_path, dst, cancel=None, on_bytes=None):
        for marker in backup.repository_for(dest).glob("*/" + backup.MARKER):
            marker.unlink()                          # dossier supprimé par quelqu'un pendant la copie
        return real_copy(src_path, dst, cancel, on_bytes)

    monkeypatch.setattr(backup, "copy_file", copy_then_lose_marker)
    with pytest.raises(OSError):
        backup.run_backup(backup.plan_backup([str(src)], str(dest), FULL))
    assert not list(backup.repository_for(dest).glob("*/" + backup.MANIFEST))
    # Les fichiers recopiés sans marqueur ne doivent pas rester sur le disque (l'application ne les verrait plus).
    assert not [c for c in backup.repository_for(dest).iterdir() if c.is_dir()]


def test_find_incomplete_lists_only_interrupted_sets(env):
    src, dest = env
    done = _backup(src, dest, FULL)
    leftover = _leftover_set(dest)
    repo = backup.repository_for(dest)
    assert backup.find_incomplete(repo) == [leftover]
    assert backup.tree_size(leftover) > 1000                 # gros.bin + marqueur
    assert (repo / done.set_id).exists() and leftover.exists()   # rien n'est supprimé par la recherche


def test_restore_original_location_does_not_overwrite_by_default(env):
    src, dest = env
    result = _backup(src, dest, FULL)
    repo = backup.repository_for(dest)
    (src / "c.txt").unlink()                        # perdu
    _write(src / "a.txt", "modifié depuis", T0 + 500)
    res = backup.restore(repo, result.set_id, ORIGINAL)
    assert res.restored == 1 and res.kept == 1 and res.identical == 1
    assert (src / "c.txt").read_text(encoding="utf-8") == "C1"
    assert (src / "a.txt").read_text(encoding="utf-8") == "modifié depuis"
    res = backup.restore(repo, result.set_id, ORIGINAL, overwrite=True)
    assert res.restored == 1
    assert (src / "a.txt").read_text(encoding="utf-8") == "A1"


def test_restore_selected_files(env, tmp_path):
    src, dest = env
    result = _backup(src, dest, FULL)
    key = "Documents/sub/b.txt"
    out = tmp_path / "out"
    res = backup.restore(backup.repository_for(dest), result.set_id, FOLDER, str(out), keys=[key])
    assert res.restored == 1
    assert _tree(out) == {"Documents/sub/b.txt": "B1"}


def test_verify_detects_missing_and_damaged(env):
    src, dest = env
    result = _backup(src, dest, FULL)
    repo = backup.repository_for(dest)
    assert backup.verify(repo, result.set_id).ok
    base = result.path / "Documents"
    (base / "a.txt").unlink()
    (base / "c.txt").write_text("tronqué!!", encoding="utf-8")
    check = backup.verify(repo, result.set_id)
    assert len(check.missing) == 1 and len(check.damaged) == 1


def test_dependents_and_broken_chain(env):
    src, dest = env
    full = _backup(src, dest, FULL)
    _write(src / "a.txt", "A2", T0 + 100)
    inc = _backup(src, dest, INCREMENTAL)
    repo = backup.repository_for(dest)
    sets = backup.list_sets(repo)
    assert [s.id for s in backup.dependents(sets, full.set_id)] == [inc.set_id]
    backup.delete_set(repo, full.set_id)
    sets = backup.list_sets(repo)
    assert not backup.chain_ok({s.id: s for s in sets}, sets[0])
    # La chaîne cassée n'est plus utilisée comme base : on repart sur une complète.
    assert backup.plan_backup([str(src)], str(dest), INCREMENTAL).kind == FULL


def test_delete_refuses_unknown_folders(tmp_path):
    (tmp_path / "pas_une_sauvegarde").mkdir()
    with pytest.raises(ValueError):
        backup.delete_set(tmp_path, "pas_une_sauvegarde")


def test_prune_keeps_last_full_chains(env):
    src, dest = env
    repo = backup.repository_for(dest)
    ids = []
    for i, mode in enumerate([FULL, INCREMENTAL, FULL, INCREMENTAL, FULL]):
        _write(src / "a.txt", f"v{i}", T0 + i)
        ids.append(_backup(src, dest, mode).set_id)
        # Des dates de création distinctes (deux sauvegardes peuvent tomber dans la même seconde).
        manifest = repo / ids[-1] / backup.MANIFEST
        meta = json.loads(manifest.read_text(encoding="utf-8"))
        meta["created"] = f"2026-01-0{i + 1}T10:00:00"
        manifest.write_text(json.dumps(meta), encoding="utf-8")
    removed = backup.prune(repo, keep_full=2)
    assert sorted(removed) == sorted(ids[:2])
    assert [s.id for s in backup.list_sets(repo)] == ids[2:]


def test_find_repositories_from_any_level(env):
    src, dest = env
    result = _backup(src, dest, FULL)
    repo = backup.repository_for(dest)
    assert backup.find_repositories(dest) == [repo]
    assert backup.find_repositories(dest / backup.REPO_DIR) == [repo]
    assert backup.find_repositories(repo) == [repo]
    assert backup.find_repositories(result.path) == [repo]
    assert backup.find_repositories(src) == []


def test_restore_script_lists_chain_in_order(env):
    src, dest = env
    full = _backup(src, dest, FULL)
    _write(src / "a.txt", "A2", T0 + 100)
    inc = _backup(src, dest, INCREMENTAL)
    script = (inc.path / backup.SCRIPT).read_text(encoding="utf-8")
    assert script.index(full.set_id) < script.index(f'..\\{inc.set_id}"')


def test_inventory_records_holder(env):
    src, dest = env
    full = _backup(src, dest, FULL)
    with gzip.open(full.path / backup.INVENTORY, "rt", encoding="utf-8") as fh:
        files = json.load(fh)["files"]
    assert all(v[2] == full.set_id for v in files.values())


def test_long_paths(tmp_path):
    src = tmp_path / "src"
    deep = src
    for i in range(12):
        deep = deep / f"dossier_avec_un_nom_assez_long_{i:02d}"
    target = deep / "fichier.txt"
    assert len(str(target)) > 260
    os.makedirs(backup.long_path(deep))
    with open(backup.long_path(target), "w", encoding="utf-8") as fh:
        fh.write("profond")
    dest = tmp_path / "dest"
    dest.mkdir()
    result = _backup(src, dest, FULL)
    assert result.copied == 1 and not result.errors
    out = tmp_path / "out"
    res = backup.restore(backup.repository_for(dest), result.set_id, FOLDER, str(out))
    assert res.restored == 1 and not res.errors
