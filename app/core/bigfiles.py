"""Recherche des plus gros fichiers d'un dossier, pour libérer de l'espace à la main."""

from __future__ import annotations

import heapq
import os
import threading
from dataclasses import dataclass
from datetime import datetime
from typing import Callable

# Dossiers système qu'il ne faut jamais proposer de supprimer.
_SKIP_DIR_NAMES = {"$recycle.bin", "system volume information", "windows", "$windows.~bt", "$windows.~ws",
                   "winsxs", "$sysreset", "recovery"}
_SKIP_FILE_NAMES = {"pagefile.sys", "hiberfil.sys", "swapfile.sys", "dumpstack.log.tmp"}


@dataclass
class FoundFile:
    path: str
    size: int
    modified: datetime

    @property
    def name(self) -> str:
        return os.path.basename(self.path)

    @property
    def folder(self) -> str:
        return os.path.dirname(self.path)


def find_large_files(
    root: str,
    min_size: int = 100 * 1024 * 1024,
    limit: int = 200,
    cancel: threading.Event | None = None,
    progress: Callable[[int, str], None] | None = None,
) -> list[FoundFile]:
    """Renvoie les `limit` plus gros fichiers (>= min_size) sous `root`, du plus gros au plus petit."""
    heap: list[tuple[int, str, float]] = []
    stack = [root]
    scanned_dirs = 0
    while stack:
        if cancel is not None and cancel.is_set():
            break
        current = stack.pop()
        scanned_dirs += 1
        if progress and scanned_dirs % 250 == 0:
            progress(scanned_dirs, current)
        try:
            with os.scandir(current) as it:
                entries = list(it)
        except OSError:
            continue
        for entry in entries:
            try:
                if entry.is_symlink() or getattr(entry, "is_junction", lambda: False)():
                    continue
                if entry.is_dir(follow_symlinks=False):
                    if entry.name.lower() not in _SKIP_DIR_NAMES:
                        stack.append(entry.path)
                    continue
                if entry.name.lower() in _SKIP_FILE_NAMES:
                    continue
                st = entry.stat(follow_symlinks=False)
                if st.st_size < min_size:
                    continue
                item = (st.st_size, entry.path, st.st_mtime)
                if len(heap) < limit:
                    heapq.heappush(heap, item)
                elif item[0] > heap[0][0]:
                    heapq.heapreplace(heap, item)
            except OSError:
                continue
    if progress:
        progress(scanned_dirs, "")
    return [FoundFile(path, size, datetime.fromtimestamp(mtime))
            for size, path, mtime in sorted(heap, reverse=True)]
