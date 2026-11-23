"""Page Outils : actions de maintenance et réparation de Windows."""

from __future__ import annotations

import customtkinter as ctk

from app.core.drivers import create_restore_point
from app.core.maintenance import ACTIONS, MaintenanceAction
from app.ui import theme
from app.ui.pages.base import Page
from app.ui.widgets import Card, PageHeader, StatusLine, ask_yes_no, neutral_button, show_info, show_warning
from app.utils.paths import log_dir
from app.utils.system import CommandResult, open_path


class ToolsPage(Page):
    page_id = "tools"

    def __init__(self, master, app):
        super().__init__(master, app)
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        PageHeader(self, "Outils", "Réparations et raccourcis utiles au quotidien.") \
            .grid(row=0, column=0, sticky="ew", pady=(0, 14))

        grid = ctk.CTkScrollableFrame(self, fg_color="transparent")
        grid.grid(row=1, column=0, sticky="nsew")
        grid.grid_columnconfigure((0, 1, 2), weight=1, uniform="tools")

        actions: list[MaintenanceAction] = [
            MaintenanceAction("restore_point", "Créer un point de restauration",
                              "Sauvegarde l'état du système pour pouvoir revenir en arrière.",
                              lambda: create_restore_point("NPone29 Cleaner — point manuel"),
                              admin=True, background=True),
            *ACTIONS,
            MaintenanceAction("logs", "Journal de l'application",
                              "Ouvre le dossier des journaux (utile pour signaler un problème).",
                              lambda: open_path(log_dir())),
        ]
        for index, action in enumerate(actions):
            self._tile(grid, index, action)

        self.status = StatusLine(self)
        self.status.grid(row=2, column=0, sticky="ew", pady=(10, 0))

    def _tile(self, parent, index: int, action: MaintenanceAction) -> None:
        card = Card(parent)
        card.grid(row=index // 3, column=index % 3, sticky="nsew", padx=6, pady=6)
        title = ctk.CTkLabel(card, text=action.title, font=theme.font(14, "bold"), anchor="w", wraplength=220,
                             justify="left")
        title.pack(anchor="w", padx=16, pady=(14, 2))
        if action.admin:
            ctk.CTkLabel(card, text="ADMINISTRATEUR", font=theme.font(10, "bold"), text_color=theme.WARNING,
                         anchor="w").pack(anchor="w", padx=16)
        desc = ctk.CTkLabel(card, text=action.description, font=theme.font(12), text_color=theme.MUTED, anchor="nw",
                            wraplength=220, justify="left")
        desc.pack(anchor="nw", padx=16, fill="x", expand=True)
        neutral_button(card, "Lancer", lambda a=action: self._run(a), width=100).pack(anchor="sw", padx=16, pady=(8, 14))

        def rewrap(event, labels=(title, desc)) -> None:
            # Le retour à la ligne suit la largeur réelle de la tuile (valeur en pixels non mis à l'échelle).
            scaling = ctk.ScalingTracker.get_widget_scaling(card)
            width = max(120, int(event.width / scaling) - 36)
            for label in labels:
                label.configure(wraplength=width)

        card.bind("<Configure>", rewrap, add="+")

    def _run(self, action: MaintenanceAction) -> None:
        if action.admin and not self.is_admin and action.background:
            if ask_yes_no(self, "Droits administrateur requis",
                          f"« {action.title} » nécessite les droits administrateur.\n\n"
                          "Relancer l'application en tant qu'administrateur ?"):
                self.app.restart_as_admin()
            return
        if action.id == "network" and not ask_yes_no(
                self, "Réinitialiser le réseau",
                "Cette opération réinitialise la configuration réseau (un redémarrage sera nécessaire). Continuer ?"):
            return
        if not action.background:
            try:
                action.run()
                self.status.set_text(f"« {action.title} » lancé.")
            except Exception as exc:
                show_warning(self, action.title, f"Impossible de lancer l'outil : {exc}")
            return

        self.status.busy(f"{action.title}…")

        def done(result) -> None:
            if isinstance(result, CommandResult) and not result.ok:
                self.status.idle(f"{action.title} : échec.")
                show_warning(self, action.title, result.output or "L'opération a échoué.")
            else:
                self.status.idle(f"✓ {action.title} : terminé.")
                if action.id == "restore_point":
                    show_info(self, action.title, "Point de restauration créé avec succès.")

        self.dispatcher.run(action.run, on_done=done, on_error=lambda e: self.status.idle(f"Erreur : {e}"))
