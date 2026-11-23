"""Téléchargement sécurisé d'installateurs officiels + vérification de leur signature numérique."""

from __future__ import annotations

import logging
import os
import threading
import urllib.request
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

from app.config import APP_ID
from app.utils.system import run_powershell

log = logging.getLogger(__name__)

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) NPone29Cleaner"


class DownloadCancelled(Exception):
    pass


def downloads_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home())
    path = Path(base) / APP_ID / "downloads"
    path.mkdir(parents=True, exist_ok=True)
    return path


def is_allowed_url(url: str, allowed_suffixes: tuple[str, ...]) -> bool:
    """N'autorise que le HTTPS vers les domaines officiels attendus (ex. '.nvidia.com')."""
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    return parsed.scheme == "https" and any(host == s.lstrip(".") or host.endswith(s) for s in allowed_suffixes)


def download_file(
    url: str,
    allowed_suffixes: tuple[str, ...],
    progress: Callable[[int, int], None] | None = None,
    cancel: threading.Event | None = None,
) -> Path:
    """Télécharge `url` dans le dossier de téléchargements de l'application et renvoie le chemin."""
    if not is_allowed_url(url, allowed_suffixes):
        raise ValueError(f"Adresse de téléchargement refusée : {url}")
    filename = os.path.basename(urlparse(url).path) or "installateur.exe"
    dest = downloads_dir() / filename
    partial = dest.with_suffix(dest.suffix + ".part")

    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310 - domaine vérifié
        final_url = response.geturl()
        if not is_allowed_url(final_url, allowed_suffixes):
            raise ValueError(f"Redirection vers une adresse non autorisée : {final_url}")
        total = int(response.headers.get("Content-Length") or 0)
        done = 0
        with open(partial, "wb") as fh:
            while True:
                if cancel is not None and cancel.is_set():
                    fh.close()
                    partial.unlink(missing_ok=True)
                    raise DownloadCancelled()
                chunk = response.read(1024 * 256)
                if not chunk:
                    break
                fh.write(chunk)
                done += len(chunk)
                if progress:
                    progress(done, total)
    if total and done != total:
        partial.unlink(missing_ok=True)
        raise IOError("Téléchargement incomplet.")
    partial.replace(dest)
    log.info("Téléchargé : %s (%d octets)", dest, done)
    return dest


def verify_signature(path: Path, expected_publisher: str) -> tuple[bool, str]:
    """Vérifie la signature Authenticode : valide ET émise pour l'éditeur attendu."""
    safe = str(path).replace("'", "''")
    script = (f"$s = Get-AuthenticodeSignature -LiteralPath '{safe}'; "
              "Write-Output $s.Status; Write-Output $s.SignerCertificate.Subject")
    result = run_powershell(script, timeout=120)
    lines = [l.strip() for l in result.stdout.splitlines() if l.strip()]
    status = lines[0] if lines else "Unknown"
    subject = lines[1] if len(lines) > 1 else ""
    ok = status == "Valid" and expected_publisher.lower() in subject.lower()
    return ok, f"{status} — {subject}" if subject else status
