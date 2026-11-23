"""Page Pilotes : mises à jour (Windows Update), inventaire et périphériques en erreur."""

from __future__ import annotations

import customtkinter as ctk

from app.core import drivers
from app.core.drivers import DriverUpdate, InstallReport
from app.core.maintenance import open_device_manager
from app.ui import theme
from app.ui.pages.base import Page
from app.ui.widgets import (Card, PageHeader, StatusLine, Table, ask_yes_no, neutral_button, primary_button,
                            show_info, show_warning, success_button)
from app.utils.fmt import format_date, human_size
from app.utils.system import open_url, run_command

TABS = ["Mises à jour", "Pilotes installés", "Périphériques en erreur"]


class DriversPage(Page):
    page_id = "drivers"

    def __init__(self, master, app):
        super().__init__(master, app)
        self.updates: dict[str, DriverUpdate] = {}
        self.installed: list[drivers.InstalledDriver] = []
        self.busy = False
        self._loaded_installed = False
        self._loaded_problems = False

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        PageHeader(self, "Pilotes",
                   "Les mises à jour proviennent de Windows Update : pilotes officiels, signés et testés "
                   "par Microsoft et les fabricants.").grid(row=0, column=0, sticky="ew", pady=(0, 12))

        self.tabs = ctk.CTkSegmentedButton(self, values=TABS, command=self._switch_tab, font=theme.font(13),
                                           height=34)
        self.tabs.grid(row=1, column=0, sticky="w", pady=(0, 12))

        self.frames = {
            TABS[0]: self._build_updates_tab(),
            TABS[1]: self._build_installed_tab(),
            TABS[2]: self._build_problems_tab(),
        }
        self.status = StatusLine(self)
        self.status.grid(row=3, column=0, sticky="ew", pady=(10, 0))
        self.tabs.set(TABS[0])
        self._switch_tab(TABS[0])
        self.status.set_text("Cliquez sur « Rechercher » pour trouver les pilotes à mettre à jour.")

        self.dispatcher.run(drivers.get_hardware_vendors, on_done=self._show_vendor_tools)

    # ------------------------------------------------------------------ onglets
    def _switch_tab(self, name: str) -> None:
        for tab, frame in self.frames.items():
            if tab == name:
                frame.grid(row=2, column=0, sticky="nsew")
            else:
                frame.grid_remove()
        if name == TABS[1] and not self._loaded_installed:
            self.load_installed()
        elif name == TABS[2] and not self._loaded_problems:
            self.load_problems()

    # ------------------------------------------------------------------ mises à jour
    def _build_updates_tab(self) -> ctk.CTkFrame:
        frame = ctk.CTkFrame(self, fg_color="transparent")
        frame.grid_columnconfigure(0, weight=1)
        frame.grid_rowconfigure(1, weight=1)

        bar = ctk.CTkFrame(frame, fg_color="transparent")
        bar.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        self.search_btn = primary_button(bar, "Rechercher", self.start_search, width=130)
        self.search_btn.pack(side="left")
        self.install_btn = success_button(bar, "Installer la sélection", self.start_install, width=190)
        self.install_btn.pack(side="left", padx=8)
        self.restore_var = ctk.BooleanVar(value=bool(self.settings.get("create_restore_point", True)))
        ctk.CTkCheckBox(bar, text="Créer un point de restauration avant", variable=self.restore_var,
                        font=theme.font(12), checkbox_width=18, checkbox_height=18,
                        command=lambda: self.settings.set("create_restore_point", self.restore_var.get())) \
            .pack(side="left", padx=(12, 0))

        self.updates_table = Table(frame, [
            ("title", "Pilote", 380, "w", True),
            ("maker", "Fabricant", 140, "w", False),
            ("cls", "Catégorie", 120, "w", False),
            ("date", "Date", 95, "center", False),
            ("size", "Taille", 85, "e", False),
            ("kind", "Type", 105, "center", False),
        ], checkable=True, height=9)
        self.updates_table.grid(row=1, column=0, sticky="nsew")

        self.vendor_card = Card(frame)
        self.vendor_card.grid(row=2, column=0, sticky="ew", pady=(10, 0))
        ctk.CTkLabel(self.vendor_card, text="Outils officiels de vos fabricants",
                     font=theme.font(14, "bold")).pack(anchor="w", padx=16, pady=(12, 0))
        ctk.CTkLabel(self.vendor_card,
                     text="Pour les cartes graphiques, les pilotes des fabricants sont souvent plus récents "
                          "que ceux de Windows Update.",
                     font=theme.font(12), text_color=theme.MUTED, wraplength=820, justify="left") \
            .pack(anchor="w", padx=16, pady=(2, 6))
        self.vendor_buttons = ctk.CTkFrame(self.vendor_card, fg_color="transparent")
        self.vendor_buttons.pack(fill="x", padx=12, pady=(0, 12))
        ctk.CTkLabel(self.vendor_buttons, text="Détection du matériel…", font=theme.font(12),
                     text_color=theme.MUTED).pack(side="left", padx=4)
        return frame

    def _show_vendor_tools(self, vendors: dict[str, str]) -> None:
        for child in self.vendor_buttons.winfo_children():
            child.destroy()
        tools = drivers.recommended_vendor_tools(vendors)
        if not tools:
            ctk.CTkLabel(self.vendor_buttons, text="Aucun outil spécifique détecté.", font=theme.font(12),
                         text_color=theme.MUTED).pack(side="left", padx=4)
            return
        for tool in tools:
            neutral_button(self.vendor_buttons, f"{tool.vendor} — {tool.label}  ↗",
                           lambda u=tool.url: open_url(u), width=10).pack(side="left", padx=4)

    def _set_busy(self, busy: bool) -> None:
        self.busy = busy
        state = "disabled" if busy else "normal"
        self.search_btn.configure(state=state)
        self.install_btn.configure(state=state)

    def start_search(self) -> None:
        if self.busy:
            return
        self.tabs.set(TABS[0])
        self._switch_tab(TABS[0])
        self._set_busy(True)
        self.updates_table.clear()
        self.status.busy("Recherche de pilotes sur Windows Update… (cela peut prendre une à deux minutes)")
        self.dispatcher.watch(self.app.driver_service.search(), on_done=self._search_done,
                              on_error=self._search_error, on_finally=lambda: self._set_busy(False))

    def _search_done(self, updates: list[DriverUpdate]) -> None:
        self.updates = {u.update_id: u for u in updates}
        rows = []
        for u in updates:
            kind = "Optionnel" if u.optional else "Recommandé"
            rows.append((u.update_id,
                         [u.title, u.manufacturer, u.driver_class, format_date(u.version_date),
                          human_size(u.size) if u.size else "—", kind],
                         [u.title, u.manufacturer, u.driver_class, u.version_date, u.size, kind],
                         ("muted",) if u.optional else ()))
        # Les pilotes recommandés sont cochés par défaut, les optionnels non.
        self.updates_table.set_rows(rows, checked=[u.update_id for u in updates if not u.optional])
        if updates:
            rec = sum(1 for u in updates if not u.optional)
            self.status.idle(f"{len(updates)} pilote(s) disponible(s), dont {rec} recommandé(s). "
                             "Cochez ceux à installer.")
        else:
            self.status.idle("✓ Vos pilotes sont à jour selon Windows Update.")

    def _search_error(self, exc: BaseException) -> None:
        self.status.idle(f"Recherche impossible : {exc}")

    def start_install(self) -> None:
        if self.busy:
            return
        ids = self.updates_table.checked()
        if not ids:
            self.status.set_text("Cochez au moins un pilote à installer.")
            return
        if not self.is_admin:
            if ask_yes_no(self, "Droits administrateur requis",
                          "L'installation de pilotes nécessite les droits administrateur.\n\n"
                          "Relancer l'application en tant qu'administrateur ?"):
                self.app.restart_as_admin()
            return
        names = "\n".join(f"• {self.updates[i].title}" for i in ids[:10]) + ("\n• …" if len(ids) > 10 else "")
        if not ask_yes_no(self, "Installer les pilotes",
                          f"Installer {len(ids)} pilote(s) ?\n\n{names}\n\n"
                          "Ne débranchez pas l'appareil concerné pendant l'installation."):
            return

        self._set_busy(True)
        if self.restore_var.get():
            self.status.busy("Création d'un point de restauration…")
            self.dispatcher.run(drivers.create_restore_point, "NPone29 Cleaner — avant mise à jour des pilotes",
                                on_done=lambda res: self._after_restore_point(res, ids),
                                on_error=lambda exc: self._after_restore_point(None, ids))
        else:
            self._install(ids)

    def _after_restore_point(self, result, ids: list[str]) -> None:
        if result is None or not result.ok:
            detail = (result.output if result else "") or "erreur inconnue"
            if not ask_yes_no(self, "Point de restauration",
                              "Le point de restauration n'a pas pu être créé (la protection du système est "
                              f"peut-être désactivée).\n\nDétail : {detail[:400]}\n\nContinuer quand même ?"):
                self._set_busy(False)
                self.status.idle("Installation annulée.")
                return
        self._install(ids)

    def _install(self, ids: list[str]) -> None:
        progress = self.dispatcher.wrap(self._on_install_progress)
        self.status.busy("Préparation de l'installation…")
        self.dispatcher.watch(self.app.driver_service.install(ids, progress), on_done=self._install_done,
                              on_error=self._search_error, on_finally=lambda: self._set_busy(False))

    def _on_install_progress(self, index: int, total: int, title: str, phase: str) -> None:
        self.status.progress((index - 1) / max(total, 1), f"{phase} ({index}/{total}) : {title}")

    def _install_done(self, report: InstallReport) -> None:
        lines = [f"{'✓' if o.success else '✗'} {o.title}\n     {o.message}" for o in report.outcomes]
        summary = f"{report.succeeded} pilote(s) installé(s), {report.failed} échec(s)."
        self.status.idle(summary)
        for outcome in report.outcomes:
            if outcome.success:
                self.updates_table.update_row(outcome.update_id, tags=("ok",))
        (show_info if not report.failed else show_warning)(self, "Installation terminée",
                                                           summary + "\n\n" + "\n".join(lines))
        if report.reboot_required and ask_yes_no(
                self, "Redémarrage nécessaire",
                "Un redémarrage est nécessaire pour terminer l'installation.\n\nRedémarrer maintenant ?"):
            run_command(["shutdown", "/r", "/t", "10", "/c", "Redémarrage pour terminer l'installation des pilotes"])

    # ------------------------------------------------------------------ pilotes installés
    def _build_installed_tab(self) -> ctk.CTkFrame:
        frame = ctk.CTkFrame(self, fg_color="transparent")
        frame.grid_columnconfigure(0, weight=1)
        frame.grid_rowconfigure(1, weight=1)
        bar = ctk.CTkFrame(frame, fg_color="transparent")
        bar.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        self.filter_entry = ctk.CTkEntry(bar, placeholder_text="Rechercher un périphérique…", width=280, height=34)
        self.filter_entry.pack(side="left")
        self.filter_entry.bind("<KeyRelease>", lambda _e: self._render_installed())
        self.third_party_var = ctk.BooleanVar(value=bool(self.settings.get("drivers_third_party_only", True)))
        ctk.CTkSwitch(bar, text="Masquer les pilotes génériques Microsoft", variable=self.third_party_var,
                      font=theme.font(12), command=self._toggle_third_party).pack(side="left", padx=14)
        neutral_button(bar, "Actualiser", self.load_installed, width=100).pack(side="right")
        self.installed_count = ctk.CTkLabel(bar, text="", font=theme.font(12), text_color=theme.MUTED)
        self.installed_count.pack(side="right", padx=10)

        self.installed_table = Table(frame, [
            ("name", "Périphérique", 300, "w", True),
            ("cls", "Classe", 110, "w", False),
            ("provider", "Fournisseur", 150, "w", False),
            ("version", "Version", 130, "w", False),
            ("date", "Date", 95, "center", False),
            ("signed", "Signé", 60, "center", False),
        ], selectmode="browse")
        self.installed_table.grid(row=1, column=0, sticky="nsew")
        ctk.CTkLabel(frame, text="Une date ancienne ne signifie pas forcément qu'un pilote est obsolète : "
                                 "beaucoup de pilotes génériques restent valables des années.",
                     font=theme.font(11), text_color=theme.MUTED).grid(row=2, column=0, sticky="w", pady=(6, 0))
        return frame

    def _toggle_third_party(self) -> None:
        self.settings.set("drivers_third_party_only", self.third_party_var.get())
        self._render_installed()

    def load_installed(self) -> None:
        self._loaded_installed = True
        self.status.busy("Lecture des pilotes installés…")
        self.dispatcher.run(drivers.list_installed_drivers, on_done=self._installed_loaded,
                            on_error=lambda e: self.status.idle(f"Lecture impossible : {e}"))

    def _installed_loaded(self, items: list[drivers.InstalledDriver]) -> None:
        self.installed = items
        self._render_installed()
        self.status.idle(f"{len(items)} pilotes installés.")

    def _render_installed(self) -> None:
        query = self.filter_entry.get().strip().lower()
        only_third = self.third_party_var.get()
        rows = []
        for i, d in enumerate(self.installed):
            if only_third and d.is_microsoft:
                continue
            if query and query not in f"{d.device_name} {d.provider} {d.device_class} {d.manufacturer}".lower():
                continue
            rows.append((str(i),
                         [d.device_name, d.device_class, d.provider, d.version, format_date(d.date),
                          "Oui" if d.is_signed else "Non"],
                         [d.device_name, d.device_class, d.provider, d.version, d.date, d.is_signed],
                         () if d.is_signed else ("warn",)))
        self.installed_table.set_rows(rows)
        self.installed_count.configure(text=f"{len(rows)} affiché(s)")

    # ------------------------------------------------------------------ périphériques en erreur
    def _build_problems_tab(self) -> ctk.CTkFrame:
        frame = ctk.CTkFrame(self, fg_color="transparent")
        frame.grid_columnconfigure(0, weight=1)
        frame.grid_rowconfigure(1, weight=1)
        bar = ctk.CTkFrame(frame, fg_color="transparent")
        bar.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        neutral_button(bar, "Actualiser", self.load_problems, width=100).pack(side="left")
        neutral_button(bar, "Relancer la détection du matériel", self._rescan, width=10).pack(side="left", padx=8)
        neutral_button(bar, "Gestionnaire de périphériques", open_device_manager, width=10).pack(side="left")

        self.problems_table = Table(frame, [
            ("name", "Périphérique", 300, "w", True),
            ("problem", "Problème", 280, "w", True),
            ("code", "Code", 60, "center", False),
            ("hwid", "Identifiant matériel", 260, "w", True),
        ], selectmode="browse")
        self.problems_table.grid(row=1, column=0, sticky="nsew")
        ctk.CTkLabel(frame, text="Astuce : un périphérique « sans pilote » est souvent corrigé par la recherche "
                                 "Windows Update de l'onglet « Mises à jour ».",
                     font=theme.font(11), text_color=theme.MUTED).grid(row=2, column=0, sticky="w", pady=(6, 0))
        return frame

    def load_problems(self) -> None:
        self._loaded_problems = True
        self.status.busy("Recherche des périphériques en erreur…")
        self.dispatcher.run(drivers.list_problem_devices, on_done=self._problems_loaded,
                            on_error=lambda e: self.status.idle(f"Lecture impossible : {e}"))

    def _problems_loaded(self, items: list[drivers.ProblemDevice]) -> None:
        rows = []
        for i, d in enumerate(items):
            tag = ("muted",) if d.error_code in (22, 45) else ("error",)
            rows.append((str(i), [d.name, d.error_text, d.error_code, d.hardware_ids[0] if d.hardware_ids else d.device_id],
                         None, tag))
        self.problems_table.set_rows(rows)
        self.status.idle(f"{len(items)} périphérique(s) en erreur." if items
                         else "✓ Aucun périphérique en erreur.")

    def _rescan(self) -> None:
        if not self.is_admin:
            show_warning(self, "Droits administrateur requis",
                         "La détection du matériel nécessite les droits administrateur.")
            return
        self.status.busy("Détection du matériel…")
        self.dispatcher.run(drivers.rescan_hardware, on_done=lambda _r: self.load_problems(),
                            on_error=lambda e: self.status.idle(str(e)))
