"""Page Logiciels : mise à jour des applications installées via winget."""

from __future__ import annotations

import threading

import customtkinter as ctk

from app.core import software
from app.core.software import SoftwareUpdate
from app.ui import theme
from app.ui.pages.base import Page
from app.ui.widgets import (PageHeader, ResultDialog, StatusLine, Stepper, Table, ask_yes_no, neutral_button,
                            primary_button, success_button)
from app.utils.system import open_url

STEPS = ["Rechercher", "Choisir", "Mettre à jour", "Terminé"]


class SoftwarePage(Page):
    page_id = "software"

    def __init__(self, master, app):
        super().__init__(master, app)
        self.updates: dict[str, SoftwareUpdate] = {}
        self.busy = False
        self.cancel = threading.Event()

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(3, weight=3)
        self.grid_rowconfigure(4, weight=1)

        PageHeader(self, "Logiciels",
                   "Mettez à jour vos applications en un clic grâce à winget, le gestionnaire de paquets "
                   "officiel de Windows (sources vérifiées).").grid(row=0, column=0, sticky="ew", pady=(0, 10))

        self.stepper = Stepper(self, STEPS)
        self.stepper.grid(row=1, column=0, sticky="w", pady=(0, 12))

        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.grid(row=2, column=0, sticky="ew", pady=(0, 10))
        self.search_btn = primary_button(bar, "Rechercher", self.start_search, width=130)
        self.search_btn.pack(side="left")
        self.update_btn = success_button(bar, "Mettre à jour la sélection", self.start_upgrade, width=210)
        self.update_btn.pack(side="left", padx=8)
        self.cancel_btn = neutral_button(bar, "Arrêter après l'actuel", self._cancel, width=10)

        self.table = Table(self, [
            ("name", "Logiciel", 300, "w", True),
            ("id", "Identifiant", 230, "w", True),
            ("version", "Version installée", 140, "w", False),
            ("available", "Nouvelle version", 140, "w", False),
            ("status", "État", 150, "w", False),
        ], checkable=True)
        self.table.grid(row=3, column=0, sticky="nsew")

        self.log = ctk.CTkTextbox(self, height=110, font=("Consolas", 11), fg_color=theme.CARD,
                                  border_width=1, border_color=theme.CARD_BORDER, corner_radius=10)
        self.log.grid(row=4, column=0, sticky="nsew", pady=(10, 0))
        self.log.insert("end", "Le journal des mises à jour s'affichera ici.\n")
        self.log.configure(state="disabled")

        self.status = StatusLine(self)
        self.status.grid(row=5, column=0, sticky="ew", pady=(10, 0))

        if software.winget_path():
            self.status.set_text("Cliquez sur « Rechercher » pour lister les logiciels à mettre à jour.")
        else:
            self.status.set_text("winget est introuvable : installez « Programme d'installation d'application » "
                                 "depuis le Microsoft Store.")
            neutral_button(bar, "Installer winget ↗",
                           lambda: open_url("https://apps.microsoft.com/detail/9NBLGGH4NNS1"), width=10) \
                .pack(side="right")

    def _write_log(self, text: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", text.rstrip() + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _set_busy(self, busy: bool, upgrading: bool = False) -> None:
        self.busy = busy
        state = "disabled" if busy else "normal"
        self.search_btn.configure(state=state)
        self.update_btn.configure(state=state)
        if busy and upgrading:
            self.cancel_btn.pack(side="left")
        else:
            self.cancel_btn.pack_forget()

    def _cancel(self) -> None:
        self.cancel.set()
        self.status.set_text("Arrêt après la mise à jour en cours…")

    # ------------------------------------------------------------------ recherche
    def start_search(self) -> None:
        if self.busy:
            return
        self._set_busy(True)
        self.stepper.set(0)
        self.table.clear()
        self.status.busy("Recherche des mises à jour avec winget…")
        self.dispatcher.run(software.list_upgrades, on_done=self.show_updates,
                            on_error=lambda e: self.status.idle(f"Recherche impossible : {e}"),
                            on_finally=lambda: self._set_busy(False))

    def show_updates(self, updates: list[SoftwareUpdate], notify: bool = True) -> None:
        """Affiche la liste (aussi appelée par l'entretien guidé)."""
        self.updates = {u.id: u for u in updates}
        rows = [(u.id, [u.name, u.id, u.version, u.available, "Disponible"], None, ()) for u in updates]
        self.table.set_rows(rows, checked=[u.id for u in updates])
        if updates:
            self.stepper.set(1)
            self.status.idle(f"Étape 2 : {len(updates)} mise(s) à jour disponible(s). Décochez celles que vous ne "
                             "voulez pas, puis cliquez sur « Mettre à jour la sélection ».")
        else:
            self.stepper.set(len(STEPS))
            self.status.idle("✓ Tous vos logiciels sont à jour.")
        if notify:
            self.app.notify("Recherche terminée",
                            f"{len(updates)} logiciel(s) à mettre à jour." if updates else "Tous vos logiciels sont à jour.",
                            "warning" if updates else "success",
                            action=("Voir", lambda: self.app.show_page("software")) if updates else None)

    # ------------------------------------------------------------------ mise à jour
    def start_upgrade(self) -> None:
        if self.busy:
            return
        ids = self.table.checked()
        if not ids:
            self.status.set_text("Cochez au moins un logiciel.")
            return
        if not ask_yes_no(self, "Mettre à jour",
                          f"Mettre à jour {len(ids)} logiciel(s) ?\n\n"
                          "Fermez les applications concernées avant de continuer. "
                          "Certaines installations peuvent demander une confirmation Windows (UAC)."):
            return
        self.cancel.clear()
        self._set_busy(True, upgrading=True)
        self.stepper.set(2)
        items = [self.updates[i] for i in ids]
        progress = self.dispatcher.wrap(self._on_progress)

        def work() -> tuple[int, int]:
            ok = fail = 0
            for index, item in enumerate(items):
                if self.cancel.is_set():
                    break
                progress(index, len(items), item, None)
                result = software.upgrade(item)
                progress(index + 1, len(items), item, result)
                if result.ok:
                    ok += 1
                else:
                    fail += 1
            return ok, fail

        self.dispatcher.run(work, on_done=self._upgrade_done,
                            on_error=lambda e: self.status.idle(f"Erreur : {e}"),
                            on_finally=lambda: self._set_busy(False))

    def _on_progress(self, done: int, total: int, item: SoftwareUpdate, result) -> None:
        row = [item.name, item.id, item.version, item.available]
        if result is None:
            self.status.progress(done / total, f"Mise à jour ({done + 1}/{total}) : {item.name}…")
            self.table.update_row(item.id, row + ["En cours…"])
            self._write_log(f"▶ {item.name} ({item.version} → {item.available})")
            return
        if result.ok:
            self.table.update_row(item.id, row + ["✓ À jour"], tags=("ok",))
            self.table.set_checked([item.id], False)
            self._write_log("   ✓ Terminé")
        else:
            self.table.update_row(item.id, row + [f"✗ Échec ({result.returncode})"], tags=("error",))
            last = [l for l in result.output.splitlines() if l.strip()][-3:]
            for line in last:
                self._write_log(f"   {line}")

    def _upgrade_done(self, counts: tuple[int, int]) -> None:
        ok, fail = counts
        self.status.idle(f"Terminé : {ok} mis à jour, {fail} échec(s).")
        self.stepper.set(len(STEPS))
        self.app.notify("Mises à jour terminées", f"{ok} logiciel(s) mis à jour, {fail} échec(s).",
                        "success" if not fail else "warning")
        tips = []
        if fail:
            tips.append("Un échec vient souvent d'un logiciel ouvert pendant la mise à jour : fermez-le et réessayez.")
            tips.append("Certains logiciels (ex. Microsoft Store, jeux) se mettent à jour par eux-mêmes.")
        ResultDialog(self, "Mises à jour terminées", f"{ok} logiciel(s) mis à jour",
                     [("Réussies", str(ok)), ("Échecs", str(fail))], tips,
                     actions=[("Relancer la recherche", self.start_search),
                              ("Vérifier les pilotes", lambda: self.app.show_page("drivers").start_analysis())],
                     level="success" if not fail else "warning")
