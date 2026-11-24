"""Fabricants de puces sans service public de vérification : Intel, AMD et tous les autres.

Ces sources ne vérifient rien (``has_public_api = False``) : elles donnent les pages officielles
et l'interface explique pourquoi la version ne peut pas être vérifiée automatiquement.
"""

from __future__ import annotations

from urllib.parse import urlparse

from app.core.driver_sources.base import GENERAL, SEARCH, DeviceRef, DownloadLink, DriverSource, SystemContext
from app.core.vendors import AMD, INTEL, Vendor, vendor_for, web_search_url
from app.utils.download import is_allowed_url


def device_vendor(dev: DeviceRef) -> Vendor:
    return vendor_for(dev.device_id, dev.vendor_name)


class IntelSource(DriverSource):
    name = "Intel"
    allowed_hosts = (".intel.com", ".intel.fr")
    no_api_note = ("Intel ne propose pas de service public pour vérifier les versions : son outil officiel "
                   "Intel Driver & Support Assistant détecte le pilote exact.")

    def applies_to_device(self, dev: DeviceRef) -> bool:
        return device_vendor(dev).name == INTEL.name

    def links(self, ctx: SystemContext, dev: DeviceRef, checks) -> list[DownloadLink]:
        if not self.applies_to_device(dev):
            return []
        out = [DownloadLink("Intel Driver & Support Assistant (outil officiel)", INTEL.support_url, GENERAL),
               DownloadLink("Rechercher sur intel.com", web_search_url(dev.name, INTEL), SEARCH)]
        return [link for link in out if self.safe(link)]


class AmdSource(DriverSource):
    name = "AMD"
    allowed_hosts = (".amd.com",)
    no_api_note = ("AMD ne propose pas de service public pour vérifier les versions : sa page de pilotes "
                   "propose un outil de détection automatique qui identifie le bon pilote.")

    def applies_to_device(self, dev: DeviceRef) -> bool:
        return device_vendor(dev).name == AMD.name

    def links(self, ctx: SystemContext, dev: DeviceRef, checks) -> list[DownloadLink]:
        if not self.applies_to_device(dev):
            return []
        out = [DownloadLink("Pilotes AMD et outil de détection automatique", AMD.support_url, GENERAL),
               DownloadLink("Rechercher sur amd.com", web_search_url(dev.name, AMD), SEARCH)]
        return [link for link in out if self.safe(link)]


class OtherVendorSource(DriverSource):
    """Tous les autres fabricants connus (Realtek, Broadcom, Logitech…) : site officiel de vendors.py."""
    name = "Autres fabricants"
    HANDLED_ELSEWHERE = {"NVIDIA", INTEL.name, AMD.name, "Inconnu"}

    def applies_to_device(self, dev: DeviceRef) -> bool:
        vendor = device_vendor(dev)
        return vendor.name not in self.HANDLED_ELSEWHERE and bool(vendor.support_url or vendor.domain)

    def note_for(self, ctx: SystemContext, dev: DeviceRef, consulted: list[str]) -> str:
        if not self.applies_to_device(dev):
            return ""
        vendor = device_vendor(dev)
        if vendor.via_oem:
            return (f"{vendor.name} ne propose pas de service public pour vérifier les versions, et ses pilotes "
                    "sont généralement fournis par le fabricant de votre PC ou de votre carte mère.")
        return f"{vendor.name} ne propose pas de service public pour vérifier les versions de ses pilotes."

    def links(self, ctx: SystemContext, dev: DeviceRef, checks) -> list[DownloadLink]:
        if not self.applies_to_device(dev):
            return []
        vendor = device_vendor(dev)
        out = []
        if vendor.support_url:
            # Adresse fixe, tenue à jour à la main dans vendors.py : on n'autorise que son propre domaine.
            host = urlparse(vendor.support_url).hostname or ""
            link = DownloadLink(f"Site officiel {vendor.name}", vendor.support_url, GENERAL)
            if host and is_allowed_url(link.url, (host,)):
                out.append(link)
        if vendor.domain:
            link = DownloadLink(f"Rechercher sur {vendor.domain}", web_search_url(dev.name, vendor), SEARCH)
            if self.safe(link):
                out.append(link)
        return out
