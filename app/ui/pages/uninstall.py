"""Page Désinstallation : liste des programmes installés."""

from __future__ import annotations

import customtkinter as ctk

from app.core import uninstaller
from app.core.uninstaller import InstalledProgram
from app.ui import theme
from app.ui.pages.base import Page
from app.ui.widgets import (PageHeader, StatusLine, Table, ask_yes_no, danger_button, neutral_button,
                            show_error)
from app.utils.fmt import format_date, human_size
from app.utils.system import reveal_in_explorer


class UninstallPage(Page):
    page_id = "uninstall"

    def __init__(self, master, app):
        super().__init__(master, app)
        self.programs: list[InstalledProgram] = []
        self._loaded = False

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        PageHeader(self, "Désinstallation",
                   "Supprimez les programmes dont vous ne vous servez plus. Le désinstalleur officiel "
                   "de chaque programme est utilisé.").grid(row=0, column=0, sticky="ew", pady=(0, 14))

        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.grid(row=1, column=0, sticky="ew", pady=(0, 10))
        self.search = ctk.CTkEntry(bar, placeholder_text="Rechercher un programme…", width=300, height=34)
        self.search.pack(side="left")
        self.search.bind("<KeyRelease>", lambda _e: self._render())
        danger_button(bar, "Désinstaller", self._uninstall, width=130).pack(side="left", padx=10)
        neutral_button(bar, "Ouvrir le dossier", self._reveal, width=10).pack(side="left")
        neutral_button(bar, "Actualiser", self.load, width=100).pack(side="right")
        self.count = ctk.CTkLabel(bar, text="", font=theme.font(12), text_color=theme.MUTED)
        self.count.pack(side="right", padx=10)

        self.table = Table(self, [
            ("name", "Programme", 320, "w", True),
            ("publisher", "Éditeur", 200, "w", True),
            ("version", "Version", 120, "w", False),
            ("size", "Taille", 90, "e", False),
            ("date", "Installé le", 100, "center", False),
        ], selectmode="browse", on_double_click=lambda _iid: self._uninstall())
        self.table.grid(row=2, column=0, sticky="nsew")

        self.status = StatusLine(self)
        self.status.grid(row=3, column=0, sticky="ew", pady=(10, 0))

    def on_show(self) -> None:
        if not self._loaded:
            self.load()

    def load(self) -> None:
        self._loaded = True
        self.status.busy("Lecture des programmes installés…")
        self.dispatcher.run(uninstaller.list_installed_programs, on_done=self._loaded_programs,
                            on_error=lambda e: self.status.idle(f"Erreur : {e}"))

    def _loaded_programs(self, programs: list[InstalledProgram]) -> None:
        self.programs = programs
        self._render()
        total = sum(p.size_bytes for p in programs)
        self.status.idle(f"{len(programs)} programmes installés ({human_size(total)} déclarés).")

    def _render(self) -> None:
        query = self.search.get().strip().lower()
        rows = []
        for i, p in enumerate(self.programs):
            if query and query not in f"{p.name} {p.publisher}".lower():
                continue
            rows.append((str(i),
                         [p.name, p.publisher, p.version, human_size(p.size_bytes) if p.size_bytes else "—",
                          format_date(p.install_date)],
                         [p.name, p.publisher, p.version, p.size_bytes, p.install_date], ()))
        self.table.set_rows(rows)
        self.count.configure(text=f"{len(rows)} affiché(s)")

    def _selected(self) -> InstalledProgram | None:
        ids = self.table.selected()
        return self.programs[int(ids[0])] if ids else None

    def _uninstall(self) -> None:
        program = self._selected()
        if program is None:
            self.status.set_text("Sélectionnez un programme dans la liste.")
            return
        if not ask_yes_no(self, "Désinstaller",
                          f"Lancer la désinstallation de « {program.name} » ?\n\n"
                          "Le désinstalleur du programme va s'ouvrir : suivez ses instructions."):
            return
        try:
            uninstaller.uninstall(program)
        except Exception as exc:
            show_error(self, "Désinstallation impossible", str(exc))
            return
        self.status.set_text(f"Désinstalleur de « {program.name} » lancé. Cliquez sur « Actualiser » une fois terminé.")

    def _reveal(self) -> None:
        program = self._selected()
        if program and program.install_location:
            reveal_in_explorer(program.install_location)
        elif program:
            self.status.set_text("Emplacement d'installation inconnu pour ce programme.")
