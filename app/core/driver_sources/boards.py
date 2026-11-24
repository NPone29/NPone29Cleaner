"""Fabricants de cartes mères et de PC : pages de support (pré-remplies quand c'est possible).

Aucune de ces sources ne vérifie de version (pas d'API publique) : elles donnent seulement
les liens. Dell, HP et Lenovo ont en plus un catalogue vérifiable (voir dell.py, hp.py, lenovo.py) ;
ici, ils ne fournissent que leur page de support générale.
"""

from __future__ import annotations

import re
from urllib.parse import quote

from app.core.driver_sources.base import GENERAL, PREFILLED, SEARCH, DeviceRef, DownloadLink, DriverSource, SystemContext
from app.core.vendors import BoardMaker, board_drivers_search_url, board_maker, vendor_for

# Marques de PC complets (portables, PC de bureau de marque) : c'est le modèle du PC qui compte.
PREBUILT_MAKERS = {"Dell", "HP", "Lenovo", "Acer", "Samsung", "Microsoft"}
# Ces marques ont un catalogue vérifiable : leur absence de note « pas d'API » est voulue.
CATALOG_MAKERS = {"Dell", "HP", "Lenovo"}
_GPU_MAKERS = {"NVIDIA", "AMD"}


def pc_target(ctx: SystemContext) -> tuple[BoardMaker | None, str, bool]:
    """(fabricant, modèle à rechercher, PC de marque ?) — la carte mère pour un PC assemblé."""
    system = board_maker(ctx.system_maker)
    if system and system.name in PREBUILT_MAKERS:
        return system, ctx.model, True
    board = board_maker(ctx.board_maker) or system
    return board, ctx.board_product or ctx.model, False


class BoardSupportSource(DriverSource):
    maker = ""   # BoardMaker.name

    def __init__(self, maker: str, domain: str) -> None:
        self.maker = maker
        self.name = f"Support {maker}"
        self.allowed_hosts = (f".{domain}",)

    def applies_to_system(self, ctx: SystemContext) -> bool:
        board, _product, _prebuilt = pc_target(ctx)
        return bool(board and board.name == self.maker)

    def concerns(self, ctx: SystemContext, dev: DeviceRef) -> bool:
        if not self.applies_to_system(ctx):
            return False
        _board, _product, prebuilt = pc_target(ctx)
        if prebuilt:
            return True   # sur un PC de marque, tous les pilotes sont publiés par la marque
        vendor = vendor_for(dev.device_id, dev.vendor_name)
        # PC assemblé : la carte graphique dédiée (et sa sortie audio HDMI) dépend de son propre fabricant,
        # pas de la carte mère. 1002 = cartes Radeon (1022, le chipset AMD, reste sur la page carte mère).
        _bus, ven, _chip = dev.ids
        is_gpu = dev.device_class.upper() == "DISPLAY" or ven == "1002" or vendor.name == "NVIDIA"
        return not (is_gpu and vendor.name in _GPU_MAKERS)

    def note_for(self, ctx: SystemContext, dev: DeviceRef, consulted: list[str]) -> str:
        if self.maker in CATALOG_MAKERS or not self.concerns(ctx, dev):
            return ""
        _board, _product, prebuilt = pc_target(ctx)
        what = "votre PC" if prebuilt else "votre carte mère"
        return (f"{self.maker} ne propose pas de service public pour vérifier les versions : "
                f"comparez avec la page support de {what}.")

    def prefilled(self, product: str, prebuilt: bool) -> list[DownloadLink]:
        return []

    def links(self, ctx: SystemContext, dev: DeviceRef, checks) -> list[DownloadLink]:
        if not self.concerns(ctx, dev):
            return []
        board, product, prebuilt = pc_target(ctx)
        out = self.prefilled(product, prebuilt) if product else []
        what = "du PC" if prebuilt else "de la carte mère"
        out.append(DownloadLink(f"Support {self.maker} (pilotes {what})", board.support_url, GENERAL))
        if product:
            out.append(DownloadLink(f"Rechercher {product} sur {board.domain}",
                                    board_drivers_search_url(board, product), SEARCH))
        return [link for link in out if self.safe(link)]


class AsusSource(BoardSupportSource):
    def __init__(self) -> None:
        super().__init__("ASUS", "asus.com")

    def prefilled(self, product: str, prebuilt: bool) -> list[DownloadLink]:
        url = f"https://www.asus.com/fr/supportonly/{quote(product.strip(), safe='')}/helpdesk_download/"
        return [DownloadLink(f"Pilotes ASUS pour {product}", url, PREFILLED)]


class MsiSource(BoardSupportSource):
    def __init__(self) -> None:
        super().__init__("MSI", "msi.com")

    @staticmethod
    def slug(product: str) -> str:
        """« MAG B550 TOMAHAWK (MS-7C91) » -> « MAG-B550-TOMAHAWK »."""
        text = re.sub(r"\(MS-[^)]*\)", "", product).strip()
        return "-".join(text.split())

    def prefilled(self, product: str, prebuilt: bool) -> list[DownloadLink]:
        if prebuilt:
            return []
        slug = self.slug(product)
        if not slug:
            return []
        url = f"https://fr.msi.com/Motherboard/{quote(slug, safe='-')}/support"
        return [DownloadLink(f"Pilotes MSI pour {product}", url, PREFILLED)]


def board_sources() -> list[BoardSupportSource]:
    sources: list[BoardSupportSource] = [AsusSource(), MsiSource()]
    for name, domain in [("Gigabyte", "gigabyte.com"), ("ASRock", "asrock.com"), ("Acer", "acer.com"),
                         ("Dell", "dell.com"), ("HP", "hp.com"), ("Lenovo", "lenovo.com"),
                         ("Biostar", "biostar.com.tw"), ("Samsung", "samsung.com"), ("Microsoft", "microsoft.com")]:
        sources.append(BoardSupportSource(name, domain))
    return sources
