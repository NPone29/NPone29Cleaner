"""Page Pilotes : analyse complète, Windows Update, inventaire et périphériques en erreur."""

from __future__ import annotations

import os
import threading

import customtkinter as ctk

from app.core import driver_analysis, drivers, nvidia
from app.core.driver_analysis import AnalysisReport, DeviceStatus
from app.core.drivers import DriverUpdate, InstallReport
from app.core.maintenance import open_device_manager
from app.ui import theme
from app.ui.pages.base import Page
from app.ui.widgets import (Card, PageHeader, ResultDialog, StatusLine, Stepper, Table, TipBox, ask_yes_no,
                            neutral_button, primary_button, show_info, show_warning, success_button)
from app.utils.download import DownloadCancelled, download_file, verify_signature
from app.utils.fmt import format_date, human_size
from app.utils.system import open_url, run_command

TABS = ["Analyse complète", "Windows Update", "Pilotes installés", "Périphériques en erreur"]
STEPS = ["Analyser", "Vérifier", "Mettre à jour", "Terminé"]
STATUS_TAGS = {
    driver_analysis.PROBLEM: ("error",), driver_analysis.UPDATE: ("warn",),
    driver_analysis.VENDOR_UPDATE: ("warn",), driver_analysis.GENERIC: ("warn",),
    driver_analysis.OLD: ("muted",), driver_analysis.OK: ("ok",),
}


class DriversPage(Page):
    page_id = "drivers"

    def __init__(self, master, app):
        super().__init__(master, app)
        self.updates: dict[str, DriverUpdate] = {}
        self.installed: list[drivers.InstalledDriver] = []
        self.report: AnalysisReport | None = None
        self.devices: dict[str, DeviceStatus] = {}
        self.busy = False
        self.cancel = threading.Event()
        self._loaded_installed = False
        self._loaded_problems = False
        self._after_install = None

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        PageHeader(self, "Pilotes",
                   "Les pilotes permettent à Windows de dialoguer avec votre matériel. L'application les compare "
                   "aux sources officielles et vous guide pour les mettre à jour.") \
            .grid(row=0, column=0, sticky="ew", pady=(0, 10))

        self.tabs = ctk.CTkSegmentedButton(self, values=TABS, command=self._switch_tab, font=theme.font(13),
                                           height=34)
        self.tabs.grid(row=1, column=0, sticky="w", pady=(0, 12))

        self.frames = {
            TABS[0]: self._build_analysis_tab(),
            TABS[1]: self._build_updates_tab(),
            TABS[2]: self._build_installed_tab(),
            TABS[3]: self._build_problems_tab(),
        }
        self.status = StatusLine(self)
        self.status.grid(row=3, column=0, sticky="ew", pady=(10, 0))
        self.tabs.set(TABS[0])
        self._switch_tab(TABS[0])
        self.status.set_text("Cliquez sur « Analyser tous les pilotes » pour commencer.")

        self.dispatcher.run(drivers.get_hardware_vendors, on_done=self._show_vendor_tools)

    # ------------------------------------------------------------------ onglets
    def _switch_tab(self, name: str) -> None:
        self.tabs.set(name)
        for tab, frame in self.frames.items():
            if tab == name:
                frame.grid(row=2, column=0, sticky="nsew")
            else:
                frame.grid_remove()
        if name == TABS[2] and not self._loaded_installed:
            self.load_installed()
        elif name == TABS[3] and not self._loaded_problems:
            self.load_problems()

    def _set_busy(self, busy: bool) -> None:
        self.busy = busy
        state = "disabled" if busy else "normal"
        for btn in (self.analyse_btn, self.update_all_btn, self.search_btn, self.install_btn):
            btn.configure(state=state)
        self._render_detail()

    # ================================================================== ANALYSE COMPLÈTE
    def _build_analysis_tab(self) -> ctk.CTkFrame:
        frame = ctk.CTkFrame(self, fg_color="transparent")
        frame.grid_columnconfigure(0, weight=1)
        frame.grid_rowconfigure(3, weight=1)

        self.stepper = Stepper(frame, STEPS)
        self.stepper.grid(row=0, column=0, sticky="w", pady=(0, 10))

        bar = ctk.CTkFrame(frame, fg_color="transparent")
        bar.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        self.analyse_btn = primary_button(bar, "Analyser tous les pilotes", self.start_analysis, width=200)
        self.analyse_btn.pack(side="left")
        self.update_all_btn = success_button(bar, "Tout mettre à jour automatiquement", self.update_all, width=10)
        self.update_all_btn.pack(side="left", padx=8)
        self.show_all_var = ctk.BooleanVar(value=False)
        ctk.CTkSwitch(bar, text="Tous les périphériques", variable=self.show_all_var, font=theme.font(12),
                      command=lambda: self.start_analysis(reuse=True) if self.report else None) \
            .pack(side="left", padx=10)
        self.summary_label = ctk.CTkLabel(bar, text="", font=theme.font(13, "bold"))
        self.summary_label.pack(side="right")

        TipBox(frame, "L'analyse compare chaque pilote avec Windows Update et, pour les cartes NVIDIA, avec nvidia.com. "
                      "Cliquez sur une ligne pour savoir quoi faire : installation automatique quand c'est possible, "
                      "sinon lien direct vers la page officielle du fabricant.") \
            .grid(row=2, column=0, sticky="ew", pady=(0, 8))

        self.analysis_table = Table(frame, [
            ("name", "Périphérique", 300, "w", True),
            ("cat", "Catégorie", 140, "w", False),
            ("vendor", "Fabricant", 120, "w", False),
            ("version", "Version installée", 140, "w", False),
            ("status", "État", 150, "w", False),
        ], selectmode="browse", on_select=self._render_detail, height=7)
        self.analysis_table.grid(row=3, column=0, sticky="nsew")

        self.detail = Card(frame)
        self.detail.grid(row=4, column=0, sticky="ew", pady=(8, 0))
        self.detail.grid_columnconfigure(0, weight=1)
        self.detail_title = ctk.CTkLabel(self.detail, text="Sélectionnez un périphérique dans la liste",
                                         font=theme.font(14, "bold"), anchor="w")
        self.detail_title.grid(row=0, column=0, sticky="w", padx=16, pady=(12, 0))
        self.detail_text = ctk.CTkLabel(self.detail, text="", font=theme.font(12), text_color=theme.MUTED,
                                        anchor="w", justify="left", wraplength=860)
        self.detail_text.grid(row=1, column=0, sticky="ew", padx=16, pady=(2, 6))
        self.detail_buttons = ctk.CTkFrame(self.detail, fg_color="transparent", width=1, height=1)
        self.detail_buttons.grid(row=2, column=0, sticky="ew", padx=12, pady=(0, 12))
        self.detail.bind("<Configure>", self._rewrap_detail, add="+")
        return frame

    def _rewrap_detail(self, event) -> None:
        scaling = ctk.ScalingTracker.get_widget_scaling(self.detail)
        self.detail_text.configure(wraplength=max(300, int(event.width / scaling) - 40))

    def start_analysis(self, reuse: bool = False) -> None:
        if self.busy:
            return
        self._switch_tab(TABS[0])
        self._set_busy(True)
        self.stepper.set(0)
        self.analysis_table.clear()
        self.status.busy("Analyse des pilotes…")
        progress = self.dispatcher.wrap(lambda text: self.status.set_text(text))
        self.dispatcher.run(driver_analysis.run_full_analysis, self.app.driver_service, progress,
                            self.show_all_var.get(), reuse,
                            on_done=self.show_analysis, on_error=self._analysis_error,
                            on_finally=lambda: self._set_busy(False))

    def _analysis_error(self, exc: BaseException) -> None:
        self.status.idle(f"Analyse impossible : {exc}")
        self.app.notify("Analyse des pilotes impossible", str(exc), "error")

    def show_analysis(self, report: AnalysisReport, notify: bool = True) -> None:
        """Affiche un rapport (aussi appelé par l'entretien guidé)."""
        self.report = report
        self.devices = {d.key: d for d in report.devices}
        rows = []
        for d in report.devices:
            rows.append((d.key, [d.name, d.category, d.vendor.name, d.version, d.status_label],
                         [d.name, d.category, d.vendor.name, d.version, driver_analysis.STATUS_ORDER.index(d.status)],
                         STATUS_TAGS.get(d.status, ())))
        self.analysis_table.set_rows(rows)
        if report.devices:
            first = report.devices[0].key
            self.analysis_table.tree.selection_set([first])
            self.analysis_table.tree.see(first)
        self.wu_auto = sum(1 for d in report.devices if d.wu_update_ids)
        auto = sum(1 for d in report.devices if d.can_auto_install)
        attention = sum(1 for d in report.devices if d.status in (driver_analysis.PROBLEM, driver_analysis.GENERIC))
        to_check = report.count(driver_analysis.OLD)
        parts = []
        if auto:
            parts.append(f"{auto} à mettre à jour")
        if attention:
            parts.append(f"{attention} à corriger")
        if to_check:
            parts.append(f"{to_check} à vérifier")
        summary = " • ".join(parts) if parts else "✓ Tout est à jour"
        self.summary_label.configure(text=summary, text_color=theme.WARNING if parts else theme.SUCCESS)
        self.stepper.set(1 if parts else len(STEPS))
        note = (" " + " ".join(report.notes)) if report.notes else ""
        if auto:
            self.status.idle(f"Étape 2 : {auto} pilote(s) peuvent être mis à jour automatiquement. Cliquez sur "
                             f"« Tout mettre à jour automatiquement » ou choisissez ligne par ligne.{note}")
        elif parts:
            self.status.idle(f"Étape 2 : sélectionnez les lignes signalées pour voir quoi faire.{note}")
        else:
            self.status.idle(f"Analyse terminée : aucun pilote à mettre à jour.{note}")
        if notify:
            self.app.notify("Analyse des pilotes terminée", summary + ".",
                            "warning" if parts else "success",
                            action=("Voir", lambda: self.app.show_page("drivers")) if parts else None)

    def _selected_device(self) -> DeviceStatus | None:
        ids = self.analysis_table.selected()
        return self.devices.get(ids[0]) if ids else None

    def _render_detail(self) -> None:
        for child in self.detail_buttons.winfo_children():
            child.destroy()
        device = self._selected_device()
        if device is None:
            self.detail_title.configure(text="Sélectionnez un périphérique dans la liste")
            self.detail_text.configure(text="")
            return
        self.detail_title.configure(text=f"{device.status_label}   {device.name}",
                                    text_color=theme.TAG_COLORS.get(STATUS_TAGS.get(device.status, ("",))[0],
                                                                    theme.NEUTRAL_TEXT))
        info = f"Fournisseur du pilote : {device.provider or '—'}   •   Version : {device.version}   •   " \
               f"Date : {format_date(device.date)}"
        self.detail_text.configure(text=f"{device.explanation}\n{info}")
        state = "disabled" if self.busy else "normal"
        buttons = []
        if device.wu_update_ids:
            buttons.append(success_button(self.detail_buttons, "Installer automatiquement",
                                          lambda: self.start_install(device.wu_update_ids), width=10, state=state))
        elif device.nvidia_driver and device.nvidia_driver.download_url:
            nv = device.nvidia_driver
            buttons.append(success_button(self.detail_buttons, f"Télécharger et installer ({nv.size_text})",
                                          lambda: self.install_nvidia(nv), width=10, state=state))
        for label, url in device.links[:4]:
            buttons.append(neutral_button(self.detail_buttons, f"{label}  ↗", lambda u=url: open_url(u), width=10))
        # Trois boutons par ligne pour ne jamais déborder.
        for i, button in enumerate(buttons):
            button.grid(row=i // 3, column=i % 3, sticky="w", padx=4, pady=3)

    def update_all(self) -> None:
        if self.busy:
            return
        if self.report is None:
            self.start_analysis()
            return
        wu_ids = [uid for d in self.report.devices for uid in d.wu_update_ids]
        nv = next((d.nvidia_driver for d in self.report.devices if d.nvidia_driver), None)
        if not wu_ids and not nv:
            show_info(self, "Rien à installer",
                      "Aucun pilote ne peut être mis à jour automatiquement.\n\n"
                      "Pour les lignes « à vérifier », sélectionnez-les : des liens vers les pages officielles "
                      "vous sont proposés.")
            return
        if wu_ids:
            self.start_install(wu_ids, on_complete=(lambda: self.install_nvidia(nv)) if nv else None)
        else:
            self.install_nvidia(nv)

    # ------------------------------------------------------------------ installation NVIDIA
    def install_nvidia(self, nv: nvidia.NvidiaDriver | None) -> None:
        if nv is None or self.busy:
            return
        if not ask_yes_no(self, "Pilote NVIDIA",
                          f"Télécharger le pilote NVIDIA {nv.version} ({nv.size_text}) depuis le site officiel "
                          "nvidia.com puis lancer son installation ?\n\n"
                          "L'installateur de NVIDIA s'ouvrira : choisissez « Installation express ». "
                          "L'écran peut clignoter quelques secondes pendant l'installation."):
            return
        self.cancel.clear()
        self._set_busy(True)
        self.stepper.set(2)
        self.status.progress(0, "Téléchargement du pilote NVIDIA…")

        def on_progress(done: int, total: int) -> None:
            if total:
                self.status.progress(done / total, f"Téléchargement du pilote NVIDIA : {human_size(done)} / "
                                                   f"{human_size(total)}")

        progress = self.dispatcher.wrap(on_progress)

        def work():
            path = download_file(nv.download_url, nvidia.ALLOWED_DOWNLOAD_HOSTS, progress, self.cancel)
            ok, detail = verify_signature(path, "NVIDIA Corporation")
            return path, ok, detail

        def done(result) -> None:
            path, ok, detail = result
            if not ok:
                self.status.idle("Signature numérique invalide : installation annulée par sécurité.")
                show_warning(self, "Signature invalide",
                             f"Le fichier téléchargé n'est pas signé par NVIDIA ({detail}).\n"
                             "Par sécurité, il ne sera pas lancé.")
                path.unlink(missing_ok=True)
                return
            try:
                os.startfile(str(path))  # l'installateur NVIDIA demande lui-même les droits admin
            except OSError as exc:
                self.status.idle(f"Impossible de lancer l'installateur : {exc}")
                return
            self.stepper.set(len(STEPS))
            self.status.idle("L'installateur NVIDIA est ouvert : suivez ses étapes.")
            self.app.notify("Pilote NVIDIA téléchargé", "L'installateur est ouvert : suivez ses étapes.", "success")
            ResultDialog(self, "Pilote NVIDIA", "L'installateur NVIDIA est lancé",
                         stats=[("Version", nv.version), ("Fichier vérifié", "signé par NVIDIA Corporation")],
                         tips=["Choisissez « Installation express » si on vous le demande.",
                               "L'écran peut devenir noir ou clignoter quelques secondes : c'est normal.",
                               "Une fois terminé, redémarrez le PC puis relancez l'analyse pour vérifier."],
                         actions=[("Relancer l'analyse des pilotes", self.start_analysis)])

        def error(exc: BaseException) -> None:
            if isinstance(exc, DownloadCancelled):
                self.status.idle("Téléchargement annulé.")
            else:
                self.status.idle(f"Téléchargement impossible : {exc}")
                self.app.notify("Téléchargement impossible", str(exc), "error")

        self.dispatcher.run(work, on_done=done, on_error=error, on_finally=lambda: self._set_busy(False))

    # ================================================================== WINDOWS UPDATE
    def _build_updates_tab(self) -> ctk.CTkFrame:
        frame = ctk.CTkFrame(self, fg_color="transparent")
        frame.grid_columnconfigure(0, weight=1)
        frame.grid_rowconfigure(1, weight=1)

        bar = ctk.CTkFrame(frame, fg_color="transparent")
        bar.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        self.search_btn = primary_button(bar, "Rechercher", self.start_search, width=130)
        self.search_btn.pack(side="left")
        self.install_btn = success_button(bar, "Installer la sélection", lambda: self.start_install(), width=190)
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
        self.vendor_buttons = ctk.CTkFrame(self.vendor_card, fg_color="transparent", width=1, height=1)
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

    def start_search(self) -> None:
        if self.busy:
            return
        self._switch_tab(TABS[1])
        self._set_busy(True)
        self.updates_table.clear()
        self.status.busy("Recherche de pilotes sur Windows Update… (cela peut prendre une à deux minutes)")
        self.dispatcher.watch(self.app.driver_service.search(), on_done=self._search_done,
                              on_error=self._search_error, on_finally=lambda: self._set_busy(False))

    def _search_done(self, updates: list[DriverUpdate]) -> None:
        self._fill_updates(updates)
        if updates:
            rec = sum(1 for u in updates if not u.optional)
            self.status.idle(f"{len(updates)} pilote(s) disponible(s), dont {rec} recommandé(s). "
                             "Cochez ceux à installer.")
            self.app.notify("Recherche terminée", f"{len(updates)} pilote(s) disponible(s) sur Windows Update.",
                            "warning")
        else:
            self.status.idle("✓ Vos pilotes sont à jour selon Windows Update.")
            self.app.notify("Recherche terminée", "Vos pilotes sont à jour selon Windows Update.", "success")

    def _fill_updates(self, updates: list[DriverUpdate]) -> None:
        self.updates = {u.update_id: u for u in updates}
        rows = []
        for u in updates:
            kind = "Optionnel" if u.optional else "Recommandé"
            rows.append((u.update_id,
                         [u.title, u.manufacturer, u.driver_class, format_date(u.version_date),
                          human_size(u.size) if u.size else "—", kind],
                         [u.title, u.manufacturer, u.driver_class, u.version_date, u.size, kind],
                         ("muted",) if u.optional else ()))
        self.updates_table.set_rows(rows, checked=[u.update_id for u in updates if not u.optional])

    def _search_error(self, exc: BaseException) -> None:
        self.status.idle(f"Recherche impossible : {exc}")
        self.app.notify("Windows Update indisponible", str(exc), "error")

    def start_install(self, ids: list[str] | None = None, on_complete=None) -> None:
        if self.busy:
            return
        ids = ids if ids is not None else self.updates_table.checked()
        if not ids:
            self.status.set_text("Cochez au moins un pilote à installer.")
            return
        if not self.is_admin:
            if ask_yes_no(self, "Droits administrateur requis",
                          "L'installation de pilotes nécessite les droits administrateur.\n\n"
                          "Relancer l'application en tant qu'administrateur ?"):
                self.app.restart_as_admin()
            return
        known = {u.update_id: u.title for u in (self.app.driver_service.last_results or [])}
        known.update({k: u.title for k, u in self.updates.items()})
        names = "\n".join(f"• {known.get(i, i)}" for i in ids[:10]) + ("\n• …" if len(ids) > 10 else "")
        if not ask_yes_no(self, "Installer les pilotes",
                          f"Installer {len(ids)} pilote(s) depuis Windows Update ?\n\n{names}\n\n"
                          "Ne débranchez pas l'appareil concerné pendant l'installation."):
            return

        self._after_install = on_complete
        self._set_busy(True)
        self.stepper.set(2)
        if self.restore_var.get():
            self.status.busy("Création d'un point de restauration (sécurité)…")
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
        summary = f"{report.succeeded} pilote(s) installé(s), {report.failed} échec(s)."
        self.status.idle(summary)
        self.stepper.set(len(STEPS))
        for outcome in report.outcomes:
            if outcome.success:
                self.updates_table.update_row(outcome.update_id, tags=("ok",))
        self.app.notify("Installation des pilotes terminée", summary, "success" if not report.failed else "warning")
        stats = [("Installés", str(report.succeeded)), ("Échecs", str(report.failed))]
        tips = [f"{'✓' if o.success else '✗'} {o.title} — {o.message}" for o in report.outcomes]
        actions = []
        if report.reboot_required:
            actions.append(("Redémarrer maintenant (recommandé)", self._reboot))
        follow = self._after_install
        self._after_install = None
        if follow:
            actions.insert(0, ("Continuer : pilote de la carte graphique", follow))
        actions.append(("Relancer l'analyse des pilotes", self.start_analysis))
        ResultDialog(self, "Installation terminée", summary, stats, tips, actions,
                     level="success" if not report.failed else "warning")

    def _reboot(self) -> None:
        if ask_yes_no(self, "Redémarrer", "Enregistrez votre travail. Redémarrer le PC dans 10 secondes ?"):
            run_command(["shutdown", "/r", "/t", "10", "/c",
                         "Redémarrage pour terminer l'installation des pilotes"])

    # ================================================================== PILOTES INSTALLÉS
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

    # ================================================================== PÉRIPHÉRIQUES EN ERREUR
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
        ctk.CTkLabel(frame, text="Astuce : un périphérique « sans pilote » est souvent corrigé par l'onglet "
                                 "« Analyse complète ».",
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
            rows.append((str(i), [d.name, d.error_text, d.error_code,
                                  d.hardware_ids[0] if d.hardware_ids else d.device_id], None, tag))
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
