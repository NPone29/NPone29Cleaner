"""NVIDIA : vérification automatique sur nvidia.com (service public de la page « Pilotes »)."""

from __future__ import annotations

import logging
from datetime import datetime

from app.core import nvidia
from app.core.driver_sources.base import (DOWNLOAD, EXACT, GENERAL, DeviceRef, DownloadLink, DriverSource,
                                          SourceRun, SystemContext, VendorCheck, version_parts)

log = logging.getLogger(__name__)


class NvidiaSource(DriverSource):
    name = "NVIDIA"
    has_public_api = True
    allowed_hosts = (".nvidia.com",)

    def applies_to_device(self, dev: DeviceRef) -> bool:
        return dev.vendor_name == "NVIDIA" and dev.device_class.upper() == "DISPLAY"

    def check(self, ctx: SystemContext, devices: list[DeviceRef], run: SourceRun, progress=None) -> None:
        gpus = [d for d in devices if self.applies_to_device(d)]
        done: dict[str, nvidia.NvidiaDriver | None] = {}
        for dev in gpus:
            if dev.name not in done:
                if progress:
                    progress(f"Vérification sur nvidia.com : {dev.name}…")
                try:
                    done[dev.name] = nvidia.latest_driver(dev.name)
                except Exception as exc:
                    log.warning("Vérification NVIDIA impossible : %s", exc)
                    run.notes.append("Vérification sur nvidia.com impossible (connexion Internet ?).")
                    return
            latest = done[dev.name]
            if latest is None:
                continue
            run.add(dev.key, self.make_check(dev.version, latest))
        if done:
            run.verified_by.append(self.name)

    @staticmethod
    def make_check(windows_version: str, latest: nvidia.NvidiaDriver, now: datetime | None = None) -> VendorCheck:
        installed = nvidia.windows_to_nvidia_version(windows_version)
        comparable = version_parts(latest.version) is not None and version_parts(installed) is not None
        return VendorCheck(
            source="nvidia.com", latest_version=latest.version,
            update_available=comparable and nvidia.is_newer(latest.version, installed),
            checked_at=now or datetime.now(), title=latest.name, release_date=latest.release_date,
            page_url=latest.details_url, download_url=latest.download_url, extra=latest, comparable=comparable,
        )

    def note_for(self, ctx: SystemContext, dev: DeviceRef, consulted: list[str]) -> str:
        if self.name in consulted and self.applies_to_device(dev):
            return ("Cette carte n'a pas été trouvée dans la liste officielle des produits NVIDIA : "
                    "choisissez-la à la main sur la page des pilotes NVIDIA.")
        return ""

    def links(self, ctx: SystemContext, dev: DeviceRef, checks: list[VendorCheck]) -> list[DownloadLink]:
        if not self.applies_to_device(dev):
            return []
        out = []
        for c in checks:
            if c.source == "nvidia.com":
                if c.download_url:
                    out.append(DownloadLink(f"Télécharger le pilote NVIDIA {c.latest_version}", c.download_url,
                                            DOWNLOAD))
                if c.page_url:
                    out.append(DownloadLink(f"Page du pilote NVIDIA {c.latest_version}", c.page_url, EXACT))
        out.append(DownloadLink("Pilotes NVIDIA (site officiel)", nvidia.DRIVERS_PAGE, GENERAL))
        return [link for link in out if self.safe(link)]
