"""Classe de base commune à toutes les pages."""

from __future__ import annotations

from typing import TYPE_CHECKING

import customtkinter as ctk

if TYPE_CHECKING:
    from app.ui.app import MainWindow


class Page(ctk.CTkFrame):
    page_id = ""
    title = ""

    def __init__(self, master, app: "MainWindow"):
        super().__init__(master, fg_color="transparent")
        self.app = app
        self.dispatcher = app.dispatcher
        self.settings = app.settings

    @property
    def is_admin(self) -> bool:
        return self.app.admin

    def on_show(self) -> None:
        """Appelée à chaque affichage de la page."""

    def on_hide(self) -> None:
        """Appelée quand on quitte la page."""
