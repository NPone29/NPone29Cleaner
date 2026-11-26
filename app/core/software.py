"""Mises à jour des logiciels installés via winget (gestionnaire de paquets officiel de Windows)."""

from __future__ import annotations

import logging
import re
import shutil
from dataclasses import dataclass

from app.utils.system import CommandResult, run_cancellable, run_command

log = logging.getLogger(__name__)

_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
_COMMON_FLAGS = ["--accept-source-agreements", "--disable-interactivity"]


@dataclass
class SoftwareUpdate:
    name: str
    id: str
    version: str
    available: str
    source: str


def winget_path() -> str | None:
    return shutil.which("winget")


def _clean_lines(text: str) -> list[str]:
    """Retire les animations de progression (\\r, séquences ANSI) de la sortie winget."""
    lines = []
    for raw in text.replace("\r\n", "\n").split("\n"):
        if "\r" in raw:
            raw = raw.rsplit("\r", 1)[-1]
        raw = _ANSI.sub("", raw).replace("\b", "")
        lines.append(raw.rstrip())
    return lines


def _column_starts(header: str, expected: int) -> list[int]:
    starts = [m.start() for m in re.finditer(r"\S+", header)]
    if len(starts) != expected:
        # En-têtes de plusieurs mots selon la langue : colonnes séparées par 2 espaces ou plus.
        starts = [m.start() for m in re.finditer(r"\S+(?: \S+)*", header)]
    return starts


def parse_winget_table(text: str, expected_columns: int = 5) -> list[list[str]]:
    """Analyse un tableau texte winget (indépendant de la langue) et renvoie les lignes."""
    lines = _clean_lines(text)
    sep_index = None
    for i, line in enumerate(lines):
        stripped = line.strip()
        if i > 0 and len(stripped) >= 10 and set(stripped) == {"-"}:
            sep_index = i
            break
    if sep_index is None:
        return []

    starts = _column_starts(lines[sep_index - 1], expected_columns)
    if len(starts) < 2:
        return []

    rows: list[list[str]] = []
    for line in lines[sep_index + 1:]:
        # Fin du tableau : ligne vide ou phrase de résumé (plus courte que la dernière colonne).
        if not line.strip() or len(line) <= starts[-1]:
            break
        cells = []
        for idx, start in enumerate(starts):
            end = starts[idx + 1] if idx + 1 < len(starts) else len(line)
            cells.append(line[start:end].strip())
        rows.append(cells)
    return rows


def parse_upgrade_output(text: str) -> list[SoftwareUpdate]:
    updates = []
    for cells in parse_winget_table(text, expected_columns=5):
        if len(cells) < 5 or not cells[1]:
            continue
        name, pkg_id, version, available, source = cells[:5]
        updates.append(SoftwareUpdate(name.rstrip("…"), pkg_id, version, available, source))
    return updates


def list_upgrades() -> list[SoftwareUpdate]:
    """Logiciels pour lesquels une nouvelle version est disponible."""
    if not winget_path():
        raise RuntimeError(
            "winget est introuvable. Installez « Programme d'installation d'application » "
            "depuis le Microsoft Store."
        )
    result = run_command(["winget", "upgrade", *_COMMON_FLAGS], timeout=300)
    updates = parse_upgrade_output(result.stdout)
    if not updates and result.returncode not in (0,) and not result.stdout.strip():
        raise RuntimeError(result.output or f"winget a échoué (code {result.returncode}).")
    log.info("winget : %d mise(s) à jour disponible(s)", len(updates))
    return updates


UPGRADE_TIMEOUT = 20 * 60   # au-delà, l'installateur est considéré comme bloqué


def upgrade(update: SoftwareUpdate, cancel=None, timeout: float = UPGRADE_TIMEOUT) -> CommandResult:
    """Met à jour un logiciel en mode silencieux.

    `cancel` (threading.Event) interrompt la mise à jour en cours (installateur bloqué, par exemple).
    """
    args = ["winget", "upgrade", "--id", update.id, "--exact", "--silent",
            "--accept-package-agreements", *_COMMON_FLAGS]
    if update.source:
        args += ["--source", update.source]
    result = run_cancellable(args, timeout=timeout, cancel=cancel)
    # Nettoie la sortie (barres de progression) pour l'affichage dans le journal.
    lines = [l for l in _clean_lines(result.stdout) if l.strip() and not set(l.strip()) <= set("█▒-\\|/ ")]
    return CommandResult(result.returncode, "\n".join(lines), result.stderr)
