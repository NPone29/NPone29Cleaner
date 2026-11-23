"""Page Démarrage : programmes lancés automatiquement avec Windows."""

from __future__ import annotations

import customtkinter as ctk

from app.core import startup
from app.core.startup import StartupEntry
from app.ui import theme
from app.ui.pages.base import Page
from app.ui.widgets import PageHeader, StatusLine, Table, neutral_button, show_warning
from app.utils.system import reveal_in_explorer


class StartupPage(Page):
    page_id = "startup"

    def __init__(self, master, app):
        super().__init__(master, app)
        self.entries: list[StartupEntry] = []
        self._loaded = False

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        PageHeader(self, "Démarrage",
                   "Désactivez les programmes inutiles au démarrage pour que Windows s'allume plus vite. "
                   "Rien n'est supprimé : vous pouvez les réactiver à tout moment.") \
            .grid(row=0, column=0, sticky="ew", pady=(0, 14))

        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.grid(row=1, column=0, sticky="ew", pady=(0, 10))
        self.disable_btn = ctk.CTkButton(bar, text="Désactiver", width=120, height=36, font=theme.font(13, "bold"),
                                         fg_color=theme.DANGER, hover_color=theme.DANGER_HOVER,
                                         command=lambda: self._set_selected(False))
        self.disable_btn.pack(side="left")
        self.enable_btn = ctk.CTkButton(bar, text="Activer", width=120, height=36, font=theme.font(13, "bold"),
                                        fg_color=theme.SUCCESS, hover_color=theme.SUCCESS_HOVER,
                                        command=lambda: self._set_selected(True))
        self.enable_btn.pack(side="left", padx=8)
        neutral_button(bar, "Ouvrir l'emplacement", self._reveal, width=10).pack(side="left", padx=(8, 0))
        neutral_button(bar, "Actualiser", self.load, width=100).pack(side="right")

        self.table = Table(self, [
            ("name", "Programme", 220, "w", True),
            ("state", "État", 90, "center", False),
            ("location", "Emplacement", 230, "w", False),
            ("command", "Commande", 380, "w", True),
        ], on_double_click=lambda iid: self._toggle(iid))
        self.table.grid(row=2, column=0, sticky="nsew")

        self.status = StatusLine(self)
        self.status.grid(row=3, column=0, sticky="ew", pady=(10, 0))

    def on_show(self) -> None:
        if not self._loaded:
            self.load()

    def load(self) -> None:
        self._loaded = True
        self.status.busy("Lecture des programmes de démarrage…")
        self.dispatcher.run(startup.list_startup_entries, on_done=self._loaded_entries,
                            on_error=lambda e: self.status.idle(f"Erreur : {e}"))

    def _loaded_entries(self, entries: list[StartupEntry]) -> None:
        self.entries = entries
        self._render()
        enabled = sum(1 for e in entries if e.enabled)
        self.status.idle(f"{len(entries)} programme(s) au démarrage, dont {enabled} actif(s). "
                         "Double-cliquez sur une ligne pour l'activer/désactiver.")

    def _render(self) -> None:
        rows = []
        for i, e in enumerate(self.entries):
            state = "Activé" if e.enabled else "Désactivé"
            rows.append((str(i), [e.name, state, e.location, e.command], None, () if e.enabled else ("muted",)))
        self.table.set_rows(rows)

    def _set_selected(self, enabled: bool) -> None:
        ids = self.table.selected()
        if not ids:
            self.status.set_text("Sélectionnez un ou plusieurs programmes dans la liste.")
            return
        errors = []
        for iid in ids:
            entry = self.entries[int(iid)]
            if entry.enabled == enabled:
                continue
            try:
                startup.set_enabled(entry, enabled)
            except PermissionError:
                errors.append(entry.name)
            except OSError as exc:
                errors.append(f"{entry.name} ({exc})")
        self._render()
        self.table.tree.selection_set([i for i in ids if self.table.tree.exists(i)])
        if errors:
            show_warning(self, "Droits administrateur requis",
                         "Ces programmes sont enregistrés pour tous les utilisateurs ; relancez l'application "
                         "en administrateur pour les modifier :\n\n" + "\n".join(f"• {n}" for n in errors))
        self.status.set_text("Modifications enregistrées. Elles prendront effet au prochain démarrage.")

    def _toggle(self, iid: str) -> None:
        self.table.tree.selection_set([iid])
        self._set_selected(not self.entries[int(iid)].enabled)

    def _reveal(self) -> None:
        ids = self.table.selected()
        if not ids:
            return
        entry = self.entries[int(ids[0])]
        reveal_in_explorer(entry.file_path or entry.executable)
