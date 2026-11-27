"""Mises à jour des logiciels installés via winget (gestionnaire de paquets officiel de Windows)."""

from __future__ import annotations

import logging
import re
import shutil
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path

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


# Garde-fou seulement : l'installateur est arrêté de force à l'échéance, ce qui peut laisser le logiciel à moitié
# mis à jour. Les grosses mises à jour (Visual Studio, Office…) peuvent durer bien plus de 20 min sur un PC lent ;
# un blocage réel est signalé bien avant (IDLE_WARNING) et l'utilisateur peut passer au suivant.
UPGRADE_TIMEOUT = 2 * 60 * 60
IDLE_WARNING = 90           # secondes sans aucune activité avant de signaler un blocage possible

_URL = re.compile(r"https?://[^\s\"'<>]+")
_NOISE = set("█▒░▓-\\|/ ")
# Processus sans intérêt pour l'utilisateur dans l'arborescence de winget.
_HELPERS = {"winget.exe", "conhost.exe", "windowspackagemanagerserver.exe", "appinstallercli.exe"}


@dataclass
class LiveStatus:
    """Instantané d'une mise à jour en cours, rafraîchi chaque seconde."""
    elapsed: float = 0.0               # secondes depuis le début de cette mise à jour
    message: str = ""                  # dernier message de winget (dans la langue de Windows)
    messages: tuple[str, ...] = ()     # tous les messages reçus jusque-là
    downloading: bool = False
    downloaded: int = 0                # octets déjà téléchargés
    total: int = 0                     # taille de l'installateur, 0 si inconnue
    speed: float = 0.0                 # octets par seconde
    installer: str = ""                # programme d'installation lancé par winget (ex. « Firefox Setup.exe »)
    cpu: float = 0.0                   # utilisation du processeur par winget et l'installateur, en %
    idle: float = 0.0                  # secondes sans aucune activité (sortie, téléchargement, processeur, disque)
    windows: tuple[tuple[int, str], ...] = ()   # fenêtres ouvertes par l'installateur : (handle, titre)

    @property
    def fraction(self) -> float | None:
        """Avancement du téléchargement (0 à 1), None si la taille est inconnue."""
        if not self.total:
            return None
        return max(0.0, min(1.0, self.downloaded / self.total))

    @property
    def stalled(self) -> bool:
        return self.idle >= IDLE_WARNING


def output_messages(text: str) -> list[str]:
    """Lignes utiles de la sortie winget (sans barres de progression), adresses web réduites au site."""
    messages = []
    for line in _clean_lines(text):
        line = line.strip()
        if not line or set(line) <= _NOISE:
            continue
        messages.append(_URL.sub(lambda m: _host(m.group(0)), line))
    return messages


def download_url(text: str) -> str | None:
    """Adresse de l'installateur en cours de téléchargement (dernière adresse affichée par winget)."""
    urls = _URL.findall(text)
    return urls[-1].rstrip(".,;)") if urls else None


def _host(url: str) -> str:
    from urllib.parse import urlsplit
    return urlsplit(url).hostname or url


def remote_size(url: str, timeout: float = 10) -> int:
    """Taille du fichier à l'adresse `url` (sans le télécharger), 0 si le serveur ne la donne pas."""
    import urllib.request

    from app.utils.download import USER_AGENT

    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Range": "bytes=0-0"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:   # noqa: S310 - https de winget
            content_range = response.headers.get("Content-Range", "")
            if "/" in content_range and content_range.rsplit("/", 1)[1].isdigit():
                return int(content_range.rsplit("/", 1)[1])
            length = response.headers.get("Content-Length", "")
            return int(length) if response.status == 200 and length.isdigit() else 0
    except Exception:
        log.debug("Taille inconnue pour %s", url, exc_info=True)
        return 0


def winget_download_dir() -> Path:
    """Dossier où winget télécharge les installateurs : %TEMP%\\WinGet\\<identifiant>.<version>."""
    return Path(tempfile.gettempdir()) / "WinGet"


def folder_size(path: Path, since: float = 0.0) -> int:
    """Taille des fichiers de `path` modifiés depuis `since` (horodatage), sous-dossiers compris."""
    total = 0
    try:
        for entry in path.rglob("*"):
            try:
                if entry.is_file():
                    st = entry.stat()
                    if st.st_mtime >= since:
                        total += st.st_size
            except OSError:
                pass
    except OSError:
        pass
    return total


def process_windows(pids: set[int]) -> list[tuple[int, str]]:
    """Fenêtres visibles (avec un titre) appartenant aux processus `pids`."""
    try:
        import win32gui
        import win32process
    except ImportError:
        return []
    found: list[tuple[int, str]] = []

    def visit(hwnd, _):
        try:
            if win32gui.IsWindowVisible(hwnd) and win32gui.GetWindowText(hwnd).strip():
                if win32process.GetWindowThreadProcessId(hwnd)[1] in pids:
                    found.append((hwnd, win32gui.GetWindowText(hwnd).strip()))
        except Exception:
            pass
        return True

    try:
        win32gui.EnumWindows(visit, None)
    except Exception:
        log.debug("Liste des fenêtres impossible", exc_info=True)
    return found


def bring_to_front(hwnd: int) -> bool:
    """Affiche une fenêtre d'installateur passée derrière les autres."""
    try:
        import win32con
        import win32gui
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        win32gui.SetForegroundWindow(hwnd)
        return True
    except Exception:
        log.debug("Impossible d'afficher la fenêtre %s", hwnd, exc_info=True)
        return False


class UpgradeWatcher:
    """Suit une mise à jour winget en direct pour montrer qu'elle avance (ou qu'elle semble bloquée).

    Activité = nouvelle sortie de winget, octets téléchargés, temps processeur ou lectures/écritures disque
    de winget, de l'installateur qu'il a lancé ou d'un msiexec démarré depuis (les .msi s'installent dans
    le service Windows Installer, hors de l'arborescence de winget).
    """

    CPU_EPSILON = 0.05            # secondes de processeur en dessous desquelles on considère qu'il ne fait rien
    IO_EPSILON = 64 * 1024        # octets lus/écrits idem

    def __init__(self, update: SoftwareUpdate, clock=time.monotonic, size_probe=remote_size,
                 download_dir: Path | None = None):
        self.update = update
        self.clock = clock
        self.size_probe = size_probe
        self.download_dir = download_dir or winget_download_dir()
        self.started = clock()
        self.started_wall = time.time()
        self.last_activity = self.started
        self._output_len = 0
        self._downloaded = 0
        self._download_seen_at = self.started
        self._speed = 0.0
        self._usage: dict[int, tuple[float, int]] = {}   # pid -> (temps processeur, octets lus+écrits)
        self._last_tick = self.started
        self._url: str | None = None
        self._total = 0
        self._probe: threading.Thread | None = None

    # -------------------------------------------------------------- téléchargement
    def _package_dirs(self) -> list[Path]:
        prefix = f"{self.update.id}.".lower()
        try:
            return [p for p in self.download_dir.iterdir()
                    if p.is_dir() and p.name.lower().startswith(prefix)
                    and p.stat().st_mtime >= self.started_wall - 5]
        except OSError:
            return []

    def _start_probe(self, url: str) -> None:
        if url == self._url:
            return
        self._url, self._total = url, 0

        def probe() -> None:
            size = self.size_probe(url)
            if self._url == url:
                self._total = size

        self._probe = threading.Thread(target=probe, daemon=True, name="taille-installateur")
        self._probe.start()

    # -------------------------------------------------------------- processus
    def _processes(self, pid: int | None) -> list:
        try:
            import psutil
        except ImportError:
            return []
        procs = []
        if pid:
            try:
                root = psutil.Process(pid)
                procs = [root, *root.children(recursive=True)]
            except psutil.Error:
                procs = []
        known = {p.pid for p in procs}
        for p in psutil.process_iter(["name", "create_time"]):
            try:
                if (p.pid not in known and (p.info["name"] or "").lower() == "msiexec.exe"
                        and (p.info["create_time"] or 0) >= self.started_wall - 2):
                    procs.append(p)
            except psutil.Error:
                pass
        return procs

    def _usage_delta(self, procs) -> tuple[float, int, str]:
        """(secondes de processeur, octets lus/écrits) consommés depuis l'appel précédent + nom de l'installateur."""
        import psutil

        cpu_delta, io_delta, installer, newest = 0.0, 0, "", -1.0
        usage: dict[int, tuple[float, int]] = {}
        for p in procs:
            try:
                with p.oneshot():
                    times = p.cpu_times()
                    cpu = times.user + times.system
                    try:
                        io = p.io_counters()
                        rw = io.read_bytes + io.write_bytes
                    except (psutil.Error, AttributeError):
                        rw = 0
                    name = p.name()
                    created = p.create_time()
            except psutil.Error:
                continue
            usage[p.pid] = (cpu, rw)
            before = self._usage.get(p.pid)
            if before is not None:
                cpu_delta += max(0.0, cpu - before[0])
                io_delta += max(0, rw - before[1])
            else:   # nouveau processus : son démarrage compte comme une activité
                cpu_delta += self.CPU_EPSILON
            if name.lower() not in _HELPERS and created > newest:
                installer, newest = name, created   # le plus récent : l'installateur réel
        self._usage = usage
        return cpu_delta, io_delta, installer

    # -------------------------------------------------------------- instantané
    def snapshot(self, pid: int | None, output: str) -> LiveStatus:
        now = self.clock()
        interval = max(1e-6, now - self._last_tick)
        self._last_tick = now
        active = False

        if len(output) != self._output_len:
            self._output_len = len(output)
            active = True
        messages = output_messages(output)
        message = messages[-1] if messages else ""

        url = download_url(output)
        if url:
            self._start_probe(url)
        downloaded = sum(folder_size(p, self.started_wall - 5) for p in self._package_dirs())
        if downloaded > self._downloaded:
            self._speed = (downloaded - self._downloaded) / max(1e-6, now - self._download_seen_at)
            self._download_seen_at = now
            active = True
        elif now - self._download_seen_at > 3:
            self._speed = 0.0
        self._downloaded = max(self._downloaded, downloaded)
        # Téléchargement en cours : la dernière ligne de winget est celle de l'adresse.
        downloading = bool(messages) and bool(_URL.search(_last_raw_line(output)))

        procs = self._processes(pid)
        cpu = io = 0.0
        installer = ""
        if procs:
            cpu, io, installer = self._usage_delta(procs)
            if cpu >= self.CPU_EPSILON or io >= self.IO_EPSILON:
                active = True
        if active:
            self.last_activity = now
        windows = process_windows({p.pid for p in procs}) if procs else []
        try:
            import psutil
            cores = psutil.cpu_count() or 1
        except ImportError:
            cores = 1
        return LiveStatus(
            elapsed=now - self.started, message=message, messages=tuple(messages), downloading=downloading,
            downloaded=self._downloaded, total=self._total if self._total >= self._downloaded else 0,
            speed=self._speed, installer=installer, cpu=min(100.0, cpu / interval / cores * 100),
            idle=now - self.last_activity, windows=tuple(windows))


def clock_text(seconds: float) -> str:
    """75 -> « 1 min 15 s »."""
    seconds = max(0, int(seconds))
    hours, rest = divmod(seconds, 3600)
    minutes, sec = divmod(rest, 60)
    if hours:
        return f"{hours} h {minutes:02d} min"
    if minutes:
        return f"{minutes} min {sec:02d} s"
    return f"{sec} s"


def describe(status: LiveStatus) -> tuple[str, str, str]:
    """Textes affichés pendant la mise à jour : (étape, activité, niveau « ok »/« wait »/« warning »)."""
    from app.utils.fmt import human_size

    step = status.message or "Démarrage de winget…"
    if status.downloading or (status.downloaded and status.fraction is not None and status.fraction < 1):
        parts = []
        if status.downloaded and status.total:
            parts.append(f"{human_size(status.downloaded)} sur {human_size(status.total)} "
                         f"({status.fraction * 100:.0f} %)")
        elif status.downloaded:
            parts.append(f"{human_size(status.downloaded)} reçus")
        elif status.total:   # Windows ne donne la taille du fichier en cours d'écriture qu'avec retard
            parts.append(f"{human_size(status.total)} à télécharger")
        if status.speed >= 1024 and (status.fraction is None or status.fraction < 1):
            parts.append(f"{human_size(status.speed)}/s")
        if parts:
            step = f"{step} — {', '.join(parts)}"

    who = f"« {status.installer} »" if status.installer else "winget"
    shown = f" Fenêtre ouverte : « {status.windows[0][1]} »." if status.windows else ""
    if status.stalled:
        advice = (" Elle attend peut-être votre réponse : cliquez sur « Afficher la fenêtre »." if status.windows
                  else " L'installation est peut-être bloquée : patientez encore un peu ou cliquez sur "
                       "« Passer celui-ci ».")
        return step, f"Aucune activité depuis {clock_text(status.idle)}.{advice}", "warning"
    if status.idle >= 15:
        return step, f"En attente de {who} (aucune activité depuis {clock_text(status.idle)}).{shown}", "wait"
    cpu = f", processeur {status.cpu:.0f} %" if status.cpu >= 1 else ""
    return step, f"Actif : {who} travaille{cpu}.{shown}", "ok"


def _last_raw_line(text: str) -> str:
    lines = [l.strip() for l in _clean_lines(text) if l.strip() and not set(l.strip()) <= _NOISE]
    return lines[-1] if lines else ""


def upgrade(update: SoftwareUpdate, cancel=None, timeout: float = UPGRADE_TIMEOUT,
            on_status=None) -> CommandResult:
    """Met à jour un logiciel en mode silencieux.

    `cancel` (threading.Event) interrompt la mise à jour en cours (installateur bloqué, par exemple).
    `on_status(LiveStatus)` reçoit chaque seconde l'état en direct (appelé depuis le thread de travail).
    """
    args = ["winget", "upgrade", "--id", update.id, "--exact", "--silent",
            "--accept-package-agreements", *_COMMON_FLAGS]
    if update.source:
        args += ["--source", update.source]
    on_tick = None
    if on_status is not None:
        watcher = UpgradeWatcher(update)

        def on_tick(pid: int, output: str) -> None:
            on_status(watcher.snapshot(pid, output))

    result = run_cancellable(args, timeout=timeout, cancel=cancel, on_tick=on_tick)
    # Nettoie la sortie (barres de progression) pour l'affichage dans le journal.
    lines = [l for l in _clean_lines(result.stdout) if l.strip() and not set(l.strip()) <= set("█▒-\\|/ ")]
    return CommandResult(result.returncode, "\n".join(lines), result.stderr)
