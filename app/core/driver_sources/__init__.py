"""Sources officielles de pilotes : vérification des versions et liens de téléchargement.

Pour ajouter un fabricant : écrire une sous-classe de ``DriverSource`` (voir base.py) et
l'ajouter à ``SOURCES`` ci-dessous.
"""

from __future__ import annotations

import logging

from app.core.driver_sources.base import (LINK_ORDER, PREFILLED, DeviceRef, DownloadLink, DriverSource, SourceRun,
                                          SystemContext, VendorCheck)
from app.core.driver_sources.boards import board_sources
from app.core.driver_sources.chipmakers import AmdSource, IntelSource, OtherVendorSource
from app.core.driver_sources.dell import DellSource
from app.core.driver_sources.hp import HpSource
from app.core.driver_sources.lenovo import LenovoSource
from app.core.driver_sources.nvidia_source import NvidiaSource
from app.core.vendors import microsoft_catalog_url, parse_ids
from app.utils.download import is_allowed_url

log = logging.getLogger(__name__)

# Ordre = priorité des liens d'une même nature et ordre des explications.
SOURCES: list[DriverSource] = [
    NvidiaSource(), DellSource(), HpSource(), LenovoSource(),
    *board_sources(),
    IntelSource(), AmdSource(), OtherVendorSource(),
]
MICROSOFT_CATALOG_HOSTS = ("www.catalog.update.microsoft.com",)


def check_all(ctx: SystemContext, devices: list[DeviceRef], progress=None,
              sources: list[DriverSource] | None = None) -> SourceRun:
    """Interroge chaque source officielle concernée (réseau). Une source en panne n'arrête pas les autres."""
    run = SourceRun()
    for source in sources if sources is not None else SOURCES:
        if not source.has_public_api:
            continue
        if not (source.applies_to_system(ctx) or any(source.applies_to_device(d) for d in devices)):
            continue
        try:
            source.check(ctx, devices, run, progress)
        except Exception as exc:
            log.warning("Source %s indisponible : %s", source.name, exc, exc_info=True)
            run.notes.append(f"Catalogue {source.name} indisponible pour le moment (connexion Internet ?) : "
                             "les pilotes concernés n'ont pas pu être vérifiés.")
    return run


def links_for(ctx: SystemContext, dev: DeviceRef, checks: list[VendorCheck],
              sources: list[DriverSource] | None = None) -> list[DownloadLink]:
    """Tous les liens officiels utiles, du plus précis au moins précis, sans doublon."""
    links: list[DownloadLink] = []
    for source in sources if sources is not None else SOURCES:
        if source.concerns(ctx, dev):
            links.extend(source.links(ctx, dev, checks))
    _bus, ven, chip = parse_ids(dev.device_id)
    if ven and chip:
        catalog = DownloadLink("Catalogue Microsoft (recherche par ID matériel)",
                               microsoft_catalog_url(dev.device_id), PREFILLED)
        if is_allowed_url(catalog.url, MICROSOFT_CATALOG_HOSTS):
            links.append(catalog)
    seen: set[str] = set()
    unique = []
    for link in sorted(links, key=lambda l: LINK_ORDER.index(l.kind)):
        if link.url not in seen:
            seen.add(link.url)
            unique.append(link)
    return unique


def no_api_notes(ctx: SystemContext, dev: DeviceRef, consulted: list[str] | None = None,
                 sources: list[DriverSource] | None = None) -> list[str]:
    """Pourquoi ce pilote n'a pas pu être vérifié automatiquement (une phrase par source)."""
    notes: list[str] = []
    ordered = sources if sources is not None else SOURCES
    # Le fabricant de la puce d'abord, puis celui du PC / de la carte mère.
    ordered = sorted(ordered, key=lambda s: not s.applies_to_device(dev))
    for source in ordered:
        if not source.concerns(ctx, dev):
            continue
        note = source.note_for(ctx, dev, consulted or [])
        if note and note not in notes:
            notes.append(note)
    return notes


__all__ = ["SOURCES", "check_all", "links_for", "no_api_notes", "DeviceRef", "DownloadLink", "SourceRun",
           "SystemContext", "VendorCheck"]
