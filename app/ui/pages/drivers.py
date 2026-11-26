"""Page Pilotes : analyse complète, Windows Update, inventaire et périphériques en erreur."""

from __future__ import annotations

import logging
import threading
import tkinter.font as tkfont
from concurrent.futures import TimeoutError as FutureTimeout

import customtkinter as ctk

from app.core import driver_analysis, drivers, packages
from app.core.driver_analysis import AnalysisReport, DeviceStatus
from app.core.driver_sources import microsoft_catalog
from app.core.driver_sources.microsoft_catalog import CatalogDriver
from app.core.drivers import DriverUpdate, InstallOutcome, InstallReport
from app.core.packages import VendorPackage
from app.core.maintenance import open_device_manager
from app.ui import theme
from app.ui.pages.base import Page
from app.ui.widgets import (Card, PageHeader, ResultDialog, StatusLine, Stepper, Table, TipBox, ask_yes_no,
                            neutral_button, primary_button, show_info, show_warning, success_button)
from app.utils.download import DownloadCancelled
from app.utils.fmt import format_date, human_size
from app.utils.system import open_url, run_command
from app.utils.winapi import launch_and_wait

log = logging.getLogger(__name__)

TABS = ["Analyse complète", "Windows Update", "Pilotes installés", "Périphériques en erreur"]
STEPS = ["Analyser", "Vérifier", "Mettre à jour", "Terminé"]
DETAIL_MAX_LINES = 5     # lignes de description visibles sous la liste par défaut (le reste défile)
DETAIL_LINES_RANGE = (2, 20)
STATUS_TAGS = {
    driver_analysis.PROBLEM: ("error",), driver_analysis.UPDATE: ("warn",),
    driver_analysis.VENDOR_UPDATE: ("warn",), driver_analysis.GENERIC: ("warn",),
    driver_analysis.OPTIONAL: ("info",),
    driver_analysis.OLD: ("muted",), driver_analysis.UNVERIFIED: ("muted",), driver_analysis.VERIFIED: ("ok",),
}
# Un pilote du catalogue à installer, les périphériques qu'il concerne (pour vérifier le résultat) et le nom
# affiché (celui de la ligne : le « modèle » du catalogue liste parfois toutes les cartes prises en charge).
CatalogJob = tuple[CatalogDriver, list[str], str]
UNVERIFIABLE = (driver_analysis.OLD, driver_analysis.UNVERIFIED)


class _Either:
    """Arrêt du téléchargement en cours : « Arrêter tout » ou « Passer ce pilote »."""

    def __init__(self, *events: threading.Event):
        self.events = events

    def is_set(self) -> bool:
        return any(e.is_set() for e in self.events)


class DriversPage(Page):
    page_id = "drivers"

    def __init__(self, master, app):
        super().__init__(master, app)
        self.updates: dict[str, DriverUpdate] = {}
        self.installed: list[drivers.InstalledDriver] = []
        self.report: AnalysisReport | None = None
        self.devices: dict[str, DeviceStatus] = {}
        self.busy = False
        self.cancel = threading.Event()                   # tout arrêter
        self._current_skip: threading.Event | None = None  # passer le pilote en cours d'installation
        self._loaded_installed = False
        self._loaded_problems = False
        self._pending_refresh = False    # actualiser dès que l'opération en cours se termine

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

        # Affichés pendant une installation : un pilote bloqué ne doit jamais bloquer les suivants.
        self.install_controls = ctk.CTkFrame(self, fg_color="transparent")
        self.skip_btn = neutral_button(self.install_controls, "Passer ce pilote", self._skip_current, width=10)
        self.skip_btn.pack(side="left")
        self.stop_btn = neutral_button(self.install_controls, "Tout arrêter", self._stop_all, width=10)
        self.stop_btn.pack(side="left", padx=8)
        ctk.CTkLabel(self.install_controls, text="Installation bloquée ? Passez au pilote suivant ou arrêtez tout.",
                     font=theme.font(12), text_color=theme.MUTED).pack(side="left", padx=4)
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
        if not busy:
            self._show_install_controls(False)
        if not busy and self._pending_refresh:
            self.after(200, self._refresh_after_install)

    # ------------------------------------------------------------------ interruption d'une installation
    def _show_install_controls(self, show: bool) -> None:
        if show:
            self.skip_btn.configure(state="normal")
            self.stop_btn.configure(state="normal")
            self.install_controls.grid(row=4, column=0, sticky="w", pady=(8, 0))
        else:
            self._current_skip = None
            self.install_controls.grid_remove()

    def _skip_current(self) -> None:
        current = self._current_skip
        if current is None or current.is_set():
            return
        if not ask_yes_no(self, "Passer ce pilote",
                          "Interrompre l'installation du pilote en cours et passer au suivant ?\n\n"
                          "À utiliser si l'installation semble bloquée. Windows conserve alors le pilote "
                          "actuel ; redémarrez le PC si le périphérique ne fonctionne plus correctement."):
            return
        if self._current_skip is current:
            current.set()
            self.status.set_text("Interruption du pilote en cours…")

    def _stop_all(self) -> None:
        if not ask_yes_no(self, "Tout arrêter",
                          "Interrompre le pilote en cours et ne pas installer les suivants ?"):
            return
        self.cancel.set()
        if self._current_skip is not None:
            self._current_skip.set()
        self.skip_btn.configure(state="disabled")
        self.stop_btn.configure(state="disabled")
        self.status.set_text("Arrêt de l'installation…")

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

        TipBox(frame, "Chaque pilote est comparé à Windows Update, au Catalogue Microsoft Update (pilotes Intel, "
                      "Realtek, Killer… publiés pour l'identifiant exact de votre matériel), à la liste officielle "
                      "d'Intel (cartes graphiques, Wi-Fi, Bluetooth, Ethernet), à nvidia.com et au "
                      "catalogue des PC Dell, HP et Lenovo. Seules les versions que Windows choisirait lui-même "
                      "sont proposées. Un pilote n'est marqué « À jour (vérifié) » que si l'une de ces sources l'a "
                      "confirmé. La liste s'actualise toute seule après chaque installation.") \
            .grid(row=2, column=0, sticky="ew", pady=(0, 8))

        self.analysis_table = Table(frame, [
            ("name", "Périphérique", 270, "w", True),
            ("cat", "Catégorie", 125, "w", False),
            ("vendor", "Fabricant", 105, "w", False),
            ("version", "Version installée", 125, "w", False),
            ("status", "État", 175, "w", False),
            ("source", "Vérifié par", 170, "w", False),
        ], selectmode="browse", on_select=self._render_detail, height=7)
        self.analysis_table.grid(row=3, column=0, sticky="nsew")

        # Poignée à glisser : agrandit la liste (vers le bas) ou la description (vers le haut).
        self.detail_lines = self._saved_detail_lines()
        self._grip_start: tuple[int, int] | None = None
        grip = self.detail_grip = ctk.CTkFrame(frame, height=12, fg_color="transparent", cursor="sb_v_double_arrow")
        grip.grid(row=4, column=0, sticky="ew")
        handle = ctk.CTkFrame(grip, width=56, height=4, corner_radius=2, fg_color=("gray72", "gray38"),
                              cursor="sb_v_double_arrow")
        handle.place(relx=0.5, rely=0.5, anchor="center")
        for widget in (grip, handle):
            widget.bind("<ButtonPress-1>", self._grip_press)
            widget.bind("<B1-Motion>", self._grip_drag)
            widget.bind("<ButtonRelease-1>", self._grip_release)

        self.detail = Card(frame)
        self.detail.grid(row=5, column=0, sticky="ew")
        self.detail.grid_columnconfigure(0, weight=1)
        self.detail_title = ctk.CTkLabel(self.detail, text="Sélectionnez un périphérique dans la liste",
                                         font=theme.font(14, "bold"), anchor="w")
        self.detail_title.grid(row=0, column=0, sticky="w", padx=16, pady=(12, 0))
        # Description complète, mais limitée à DETAIL_MAX_LINES lignes à l'écran (elle défile au-delà) :
        # une longue explication ne doit jamais réduire la liste des pilotes à quelques lignes.
        self.detail_text = ctk.CTkTextbox(self.detail, font=theme.font(12), text_color=theme.MUTED,
                                          fg_color="transparent", border_width=0, wrap="word", height=24)
        self.detail_text.grid(row=1, column=0, sticky="ew", padx=10, pady=(0, 2))
        self.detail_text.configure(state="disabled")
        self.detail_buttons = ctk.CTkFrame(self.detail, fg_color="transparent", width=1, height=1)
        self.detail_buttons.grid(row=2, column=0, sticky="ew", padx=12, pady=(0, 12))
        self.detail_text.bind("<Configure>", lambda _e: self._fit_detail_height(), add="+")
        return frame

    def _saved_detail_lines(self) -> int:
        try:
            value = int(self.app.settings.get("drivers_detail_lines", DETAIL_MAX_LINES))
        except (TypeError, ValueError):
            value = DETAIL_MAX_LINES
        return max(DETAIL_LINES_RANGE[0], min(DETAIL_LINES_RANGE[1], value))

    def _detail_line_px(self) -> int:
        try:
            return max(8, tkfont.Font(font=self.detail_text._textbox.cget("font")).metrics("linespace"))
        except Exception:
            return 16

    def _grip_press(self, event) -> None:
        self._grip_start = (event.y_root, self.detail_lines)

    def _grip_drag(self, event) -> None:
        if self._grip_start is None:
            return
        start_y, start_lines = self._grip_start
        lines = start_lines + round((start_y - event.y_root) / self._detail_line_px())
        lines = max(DETAIL_LINES_RANGE[0], min(DETAIL_LINES_RANGE[1], lines))
        if lines != self.detail_lines:
            self.detail_lines = lines
            self._fit_detail_height()

    def _grip_release(self, _event) -> None:
        self._grip_start = None
        self.app.settings.set("drivers_detail_lines", self.detail_lines)

    def _set_detail_text(self, text: str) -> None:
        self.detail_text.configure(state="normal")
        self.detail_text.delete("1.0", "end")
        self.detail_text.insert("1.0", text)
        self.detail_text.configure(state="disabled")
        self.detail_text.yview_moveto(0)
        self.after_idle(self._fit_detail_height)

    def _fit_detail_height(self) -> None:
        """Hauteur de la description : celle du texte, sans dépasser DETAIL_MAX_LINES lignes."""
        inner = self.detail_text._textbox
        try:
            count = inner.count("1.0", "end-1c", "displaylines")
            lines = (count[0] if isinstance(count, tuple) else count) or 0
            line_px = tkfont.Font(font=inner.cget("font")).metrics("linespace")
        except Exception:
            return
        if not inner.get("1.0", "end-1c").strip():
            lines = 0
        shown = max(1, min(lines + 1, self.detail_lines))
        scaling = ctk.ScalingTracker.get_widget_scaling(self.detail_text)
        height = round(shown * line_px / scaling) + 8
        if abs(self.detail_text.cget("height") - height) > 1:
            self.detail_text.configure(height=height)

    def start_analysis(self, reuse: bool = False, switch: bool = True) -> None:
        if self.busy:
            return
        if switch:
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
            source = d.checked_source or "—"
            rows.append((d.key, [d.name, d.category, d.vendor.name, d.version, d.status_label, source],
                         [d.name, d.category, d.vendor.name, d.version, driver_analysis.STATUS_ORDER.index(d.status),
                          source],
                         STATUS_TAGS.get(d.status, ())))
        self.analysis_table.set_rows(rows)
        if report.devices:
            first = report.devices[0].key
            self.analysis_table.tree.selection_set([first])
            self.analysis_table.tree.see(first)
        auto = sum(1 for d in report.devices if d.can_auto_install)
        manual = sum(1 for d in report.devices
                     if d.status == driver_analysis.VENDOR_UPDATE and not d.can_auto_install)
        attention = report.count(driver_analysis.PROBLEM, driver_analysis.GENERIC)
        unverified = report.count(*UNVERIFIABLE)
        verified = report.count(driver_analysis.VERIFIED, driver_analysis.OPTIONAL)
        optional = report.count(driver_analysis.OPTIONAL)
        parts = []
        if auto:
            parts.append(f"{auto} à mettre à jour")
        if manual:
            parts.append(f"{manual} à télécharger")
        if attention:
            parts.append(f"{attention} à corriger")
        info = [f"{optional} version(s) générique(s)"] if optional else []
        if unverified:
            info.append(f"{unverified} non vérifiable(s)")
        # L'onglet Windows Update reflète la recherche faite pendant l'analyse.
        if self.app.driver_service.last_results is not None:
            self._fill_updates(self.app.driver_service.last_results)
        if self._loaded_installed and self.tabs.get() == TABS[2]:
            self.load_installed()
        if parts or info:
            summary = " • ".join(parts + info)
        elif verified:
            summary = "✓ Tout est vérifié et à jour"
        else:
            summary = "Aucun pilote à analyser"
        color = theme.WARNING if parts else theme.MUTED if info else theme.SUCCESS
        self.summary_label.configure(text=summary, text_color=color)
        self.stepper.set(1 if parts else len(STEPS))
        note = (" " + " ".join(report.notes)) if report.notes else ""
        if auto:
            self.status.idle(f"Étape 2 : {auto} pilote(s) peuvent être mis à jour automatiquement. Cliquez sur "
                             f"« Tout mettre à jour automatiquement » ou choisissez ligne par ligne.{note}")
        elif parts:
            self.status.idle(f"Étape 2 : sélectionnez les lignes signalées pour voir quoi faire.{note}")
        elif unverified:
            self.status.idle(f"Analyse terminée : {verified} pilote(s) vérifié(s) à jour, {unverified} non "
                             "vérifiable(s) automatiquement : sélectionnez-les pour ouvrir leur page officielle."
                             f"{note}")
        else:
            self.status.idle(f"Analyse terminée : tous les pilotes analysés sont vérifiés et à jour.{note}")
        if notify:
            level = "warning" if parts else "info" if info else "success"
            self.app.notify("Analyse des pilotes terminée", summary + ".", level,
                            action=("Voir", lambda: self.app.show_page("drivers")) if parts or info else None)

    def _selected_device(self) -> DeviceStatus | None:
        ids = self.analysis_table.selected()
        return self.devices.get(ids[0]) if ids else None

    def _render_detail(self) -> None:
        for child in self.detail_buttons.winfo_children():
            child.destroy()
        device = self._selected_device()
        if device is None:
            self.detail_title.configure(text="Sélectionnez un périphérique dans la liste")
            self._set_detail_text("")
            return
        self.detail_title.configure(text=f"{device.status_label}   {device.name}",
                                    text_color=theme.TAG_COLORS.get(STATUS_TAGS.get(device.status, ("",))[0],
                                                                    theme.NEUTRAL_TEXT))
        lines = [device.explanation,
                 f"Fournisseur du pilote : {device.provider or '—'}   •   Version installée : {device.version}   •   "
                 f"Date : {format_date(device.date)}"]
        if device.checked_source:
            when = device.checked_at.strftime("%d/%m/%Y à %H:%M") if device.checked_at else "—"
            line = f"Vérifié par : {device.checked_source}   •   le {when}"
            if device.latest_version and not device.wu_update_ids:
                label = ("Version générique publiée" if device.status == driver_analysis.OPTIONAL
                         else "Dernière version publiée")
                line += f"   •   {label} : {device.latest_version}"
            if device.count > 1:
                line += f"   •   {device.count} composants concernés"
            lines.append(line)
        elif device.status in UNVERIFIABLE:
            lines.append("Non vérifié : " + (device.no_api_note or
                                             "aucune source officielle ne permet de vérifier ce périphérique."))
        best = device.best_link
        if best:
            lines.append(f"« Télécharger » ouvre : {best.label}")
        self._set_detail_text("\n".join(lines))
        state = "disabled" if self.busy else "normal"
        buttons = []
        if device.wu_update_ids:
            buttons.append(success_button(self.detail_buttons, "Installer automatiquement",
                                          lambda: self.start_install(device.wu_update_ids), width=10, state=state))
        elif device.catalog_driver is not None:
            job = (device.catalog_driver, list(device.device_ids), device.name)
            buttons.append(success_button(self.detail_buttons,
                                          f"Télécharger et installer ({device.catalog_driver.size_text})",
                                          lambda: self.install_plan(catalog=[job]), width=10, state=state))
        elif device.package is not None:
            pkg = device.package
            buttons.append(success_button(self.detail_buttons, f"Télécharger et installer ({pkg.size_text})",
                                          lambda: self.install_plan(pkgs=[pkg]), width=10, state=state))
        if best:
            buttons.append(primary_button(self.detail_buttons, "Télécharger  ↗", lambda u=best.url: open_url(u),
                                          width=10))
        for link in device.links[1:5]:
            buttons.append(neutral_button(self.detail_buttons, f"{link.label}  ↗", lambda u=link.url: open_url(u),
                                          width=10))
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
        catalog = self._catalog_jobs(self.report.devices)
        pkgs = self._package_jobs(self.report.devices)
        if not wu_ids and not catalog and not pkgs:
            show_info(self, "Rien à installer",
                      "Aucun pilote ne peut être mis à jour automatiquement.\n\n"
                      "Pour les autres lignes (mise à jour chez le fabricant, non vérifiable), sélectionnez-les : "
                      "le bouton « Télécharger » ouvre la page officielle la plus précise.")
            return
        self.install_plan(wu_ids, catalog, pkgs)

    @staticmethod
    def _package_jobs(devices: list[DeviceStatus]) -> list[VendorPackage]:
        """Un installateur officiel par fichier, même s'il concerne plusieurs lignes (NVIDIA d'abord)."""
        seen: dict[str, VendorPackage] = {}
        for d in devices:
            if d.package is not None:
                seen.setdefault(d.package.key, d.package)
        return sorted(seen.values(), key=lambda pkg: pkg.vendor != "NVIDIA")

    @staticmethod
    def _catalog_jobs(devices: list[DeviceStatus]) -> list[CatalogJob]:
        """Un téléchargement par pilote du catalogue, même s'il concerne plusieurs lignes."""
        jobs: dict[str, CatalogJob] = {}
        for d in devices:
            if d.catalog_driver is not None:
                _drv, ids, _name = jobs.setdefault(d.catalog_driver.update_id, (d.catalog_driver, [], d.name))
                ids.extend(d.device_ids)
        return list(jobs.values())

    # ------------------------------------------------------------------ installation (toutes sources)
    def install_plan(self, wu_ids: list[str] | None = None, catalog: list[CatalogJob] | None = None,
                     pkgs: list[VendorPackage] | None = None) -> None:
        """Installe dans l'ordre : Windows Update, Catalogue Microsoft, puis les installateurs officiels
        (NVIDIA, Intel), qui s'ouvrent l'un après l'autre.

        La liste des pilotes est relue automatiquement à la fin, pour afficher les versions réellement en place.
        """
        wu_ids, catalog, pkgs = list(wu_ids or []), list(catalog or []), list(pkgs or [])
        if self.busy or not (wu_ids or catalog or pkgs):
            return
        if (wu_ids or catalog) and not self.is_admin:
            if ask_yes_no(self, "Droits administrateur requis",
                          "L'installation de pilotes nécessite les droits administrateur.\n\n"
                          "Relancer l'application en tant qu'administrateur ?"):
                self.app.restart_as_admin()
            return
        known = {u.update_id: u.title for u in (self.app.driver_service.last_results or [])}
        known.update({k: u.title for k, u in self.updates.items()})
        items = [f"• {known.get(i, i)}  (Windows Update)" for i in wu_ids]
        items += [f"• {name if drv.version in name else f'{name} : {drv.version}'} — {drv.size_text}  "
                  "(Catalogue Microsoft)" for drv, _ids, name in catalog]
        items += [f"• {pkg.title} — {pkg.size_text}  ({pkg.host})" for pkg in pkgs]
        listing = "\n".join(items[:12]) + ("\n• …" if len(items) > 12 else "")
        extra = ""
        if pkgs:
            vendors = " et ".join(dict.fromkeys(pkg.vendor for pkg in pkgs))
            advice = "\n".join(dict.fromkeys(f"– {pkg.advice}" for pkg in pkgs if pkg.advice))
            extra = (f"\n\nLes installateurs officiels ({vendors}) s'ouvriront à la fin, l'un après l'autre : "
                     f"suivez leurs étapes.\n{advice}")
        if not ask_yes_no(self, "Installer les pilotes",
                          f"Installer {len(items)} pilote(s) ?\n\n{listing}\n\nChaque fichier vient d'un site "
                          "officiel et sa signature est vérifiée avant l'installation. Ne débranchez pas les "
                          f"appareils concernés pendant l'installation.{extra}"):
            return
        self.cancel.clear()
        self._set_busy(True)
        self.stepper.set(2)
        if (wu_ids or catalog or (pkgs and self.is_admin)) and self.restore_var.get():
            self.status.busy("Création d'un point de restauration (sécurité)…")
            self.dispatcher.run(drivers.create_restore_point, "NPone29 Cleaner — avant mise à jour des pilotes",
                                on_done=lambda res: self._after_restore_point(res, wu_ids, catalog, pkgs),
                                on_error=lambda exc: self._after_restore_point(None, wu_ids, catalog, pkgs))
        else:
            self._run_plan(wu_ids, catalog, pkgs)

    def _after_restore_point(self, result, wu_ids: list[str], catalog: list[CatalogJob],
                             pkgs: list[VendorPackage]) -> None:
        if result is None or not result.ok:
            detail = (result.output if result else "") or "erreur inconnue"
            if not ask_yes_no(self, "Point de restauration",
                              "Le point de restauration n'a pas pu être créé (la protection du système est "
                              f"peut-être désactivée).\n\nDétail : {detail[:400]}\n\nContinuer quand même ?"):
                self._set_busy(False)
                self.status.idle("Installation annulée.")
                return
        self._run_plan(wu_ids, catalog, pkgs)

    def _run_plan(self, wu_ids: list[str], catalog: list[CatalogJob], pkgs: list[VendorPackage]) -> None:
        if not (wu_ids or catalog):
            self._set_busy(False)
            self._run_installers(pkgs, None)
            return
        wu_progress = self.dispatcher.wrap(self._on_install_progress)
        progress = self.dispatcher.wrap(self.status.progress)
        busy = self.dispatcher.wrap(self.status.busy)
        service = self.app.driver_service
        cancel = self.cancel
        known = {u.update_id: u.title for u in (service.last_results or [])}
        known.update({k: u.title for k, u in self.updates.items()})
        self._show_install_controls(True)

        def new_skip() -> threading.Event:
            skip = threading.Event()
            if cancel.is_set():
                skip.set()
            self._current_skip = skip
            return skip

        def install_windows_update() -> InstallReport:
            skip = new_skip()
            partial = InstallReport()      # rempli au fur et à mesure par le thread Windows Update
            future = service.install(wu_ids, wu_progress, skip, partial)
            error = ""
            while True:
                try:
                    future.result(timeout=0.5)
                    break
                except FutureTimeout:
                    if skip.is_set():
                        # Installation bloquée : on l'abandonne (l'appel Windows continue seul) et on passe
                        # aux pilotes suivants.
                        service.abandon()
                        break
                except Exception as exc:
                    error = drivers.com_error_message(exc)
                    break
            wu = InstallReport(list(partial.outcomes), partial.reboot_required)
            done = {o.update_id for o in wu.outcomes}
            reason = error or ("Arrêt demandé" if cancel.is_set() else "Passé : installation interrompue")
            wu.outcomes += [InstallOutcome(i, known.get(i, i), False, reason) for i in wu_ids if i not in done]
            return wu

        def work() -> InstallReport:
            report = InstallReport()
            if wu_ids:
                busy("Windows Update : préparation de l'installation…")
                wu = install_windows_update()
                report.outcomes.extend(wu.outcomes)
                report.reboot_required |= wu.reboot_required
            for n, (drv, ids, name) in enumerate(catalog, start=1):
                if cancel.is_set():
                    report.outcomes.append(InstallOutcome(drv.update_id, f"{name} — {drv.version}", False,
                                                          "Arrêt demandé"))
                    continue
                skip = new_skip()
                label = f"Catalogue Microsoft ({n}/{len(catalog)}) : {name}"

                def on_download(done: int, total: int, label=label) -> None:
                    if total:
                        progress(done / total, f"{label} — téléchargement {human_size(done)} / {human_size(total)}")

                busy(f"{label} — téléchargement…")
                try:
                    out = microsoft_catalog.install(drv, ids, on_download, skip,
                                                    phase=lambda text, label=label: busy(f"{label} — {text}"),
                                                    title=f"{name} — {drv.version}")
                    report.outcomes.append(InstallOutcome(drv.update_id, out.title, out.success, out.message))
                    report.reboot_required |= out.reboot_required
                except DownloadCancelled:
                    report.outcomes.append(InstallOutcome(drv.update_id, f"{name} — {drv.version}", False,
                                                          "Arrêt demandé" if cancel.is_set() else "Passé"))
                except Exception as exc:
                    log.exception("Installation du pilote %s impossible", drv.title)
                    report.outcomes.append(InstallOutcome(drv.update_id, f"{name} — {drv.version}", False,
                                                          str(exc)))
            return report

        self.dispatcher.run(work, on_done=lambda report: self._plan_done(report, [] if cancel.is_set() else pkgs),
                            on_error=self._install_error, on_finally=lambda: self._set_busy(False))

    def _on_install_progress(self, index: int, total: int, title: str, phase: str) -> None:
        self.status.progress((index - 1) / max(total, 1),
                             f"Windows Update — {phase} ({index}/{total}) : {title} (bloqué ? « Passer ce pilote »)")

    def _plan_done(self, report: InstallReport, pkgs: list[VendorPackage]) -> None:
        summary = f"{report.succeeded} pilote(s) installé(s), {report.failed} échec(s)."
        for outcome in report.outcomes:
            if outcome.success and outcome.update_id in self.updates:
                self.updates_table.update_row(outcome.update_id, tags=("ok",))
        self.app.notify("Installation des pilotes terminée", summary, "success" if not report.failed else "warning")
        if pkgs:
            # Les installateurs officiels viennent en dernier : ils sont interactifs.
            self.status.idle(f"{summary} Préparation des installateurs officiels…")
            self.after(150, lambda: self._run_installers(pkgs, report))
            return
        self.stepper.set(len(STEPS))
        self.status.idle(f"{summary} Actualisation de la liste des pilotes…")
        self._show_install_result(report)
        self._refresh_after_install()

    def _install_error(self, exc: BaseException) -> None:
        self.status.idle(f"Installation impossible : {exc}")
        self.app.notify("Installation des pilotes impossible", str(exc), "error")
        self._refresh_after_install()   # une partie a peut-être été installée

    def _show_install_result(self, report: InstallReport, extra_stats: list[tuple[str, str]] | None = None,
                             extra_tips: list[str] | None = None, headline: str = "") -> None:
        summary = headline or f"{report.succeeded} pilote(s) installé(s), {report.failed} échec(s)."
        stats = [("Installés", str(report.succeeded)), ("Échecs", str(report.failed))] if report.outcomes else []
        stats += extra_stats or []
        tips = [f"{'✓' if o.success else '✗'} {o.title} — {o.message}" for o in report.outcomes]
        tips += extra_tips or []
        tips.append("La liste des pilotes s'actualise automatiquement pour afficher les versions en place.")
        actions = [("Redémarrer maintenant (recommandé)", self._reboot)] if report.reboot_required else []
        ResultDialog(self, "Installation terminée", summary, stats, tips, actions,
                     level="success" if not report.failed else "warning")

    def _refresh_after_install(self) -> None:
        """Relit les pilotes après une installation pour afficher les versions réellement en place."""
        self._loaded_installed = False
        self._loaded_problems = False
        if self.busy:
            self._pending_refresh = True
            return
        self._pending_refresh = False
        current = self.tabs.get()
        if current == TABS[3]:
            self.load_problems()
        self.start_analysis(switch=False)

    # ------------------------------------------------------------------ installateurs officiels (NVIDIA, Intel)
    def _run_installers(self, pkgs: list[VendorPackage], previous: InstallReport | None) -> None:
        """Télécharge et vérifie chaque installateur, le lance, attend sa fermeture, puis passe au suivant.

        `previous` : bilan des pilotes installés juste avant (« Tout mettre à jour »), repris dans le bilan final.
        La liste des pilotes est actualisée à la fin, pour afficher les versions réellement en place.
        """
        if not pkgs:
            return
        if self.busy:       # l'opération précédente se termine : on réessaie juste après
            self.after(500, lambda: self._run_installers(pkgs, previous))
            return
        report = previous or InstallReport()
        queue = list(pkgs)
        rows = self.report.devices if self.report else []
        concerned = {pkg.key: [(d.key, d.version) for d in rows if d.package and d.package.key == pkg.key]
                     for pkg in queue}
        self.cancel.clear()
        self._set_busy(True)
        self.stepper.set(2)
        self._show_install_controls(True)
        cancel = self.cancel
        progress = self.dispatcher.wrap(self.status.progress)
        busy = self.dispatcher.wrap(self.status.busy)
        notify = self.dispatcher.wrap(self.app.notify)

        def work() -> InstallReport:
            for n, pkg in enumerate(queue, start=1):
                label = f"{pkg.vendor} ({n}/{len(queue)}) : {pkg.title}"
                if cancel.is_set():
                    report.outcomes.append(InstallOutcome(pkg.key, pkg.title, False, "Arrêt demandé"))
                    continue
                skip = threading.Event()
                self._current_skip = skip

                def on_download(done: int, total: int, label=label) -> None:
                    if total:
                        progress(done / total, f"{label} — téléchargement {human_size(done)} / {human_size(total)}")

                busy(f"{label} — téléchargement…")
                try:
                    path = packages.download_verified(pkg, on_download, _Either(cancel, skip))
                except DownloadCancelled:
                    report.outcomes.append(InstallOutcome(pkg.key, pkg.title, False,
                                                          "Arrêt demandé" if cancel.is_set() else "Passé"))
                    continue
                except packages.VerificationError as exc:
                    report.outcomes.append(InstallOutcome(pkg.key, pkg.title, False,
                                                          f"{exc} Fichier supprimé par sécurité : rien n'a été lancé."))
                    continue
                except Exception as exc:
                    log.exception("Téléchargement de %s impossible", pkg.title)
                    report.outcomes.append(InstallOutcome(pkg.key, pkg.title, False,
                                                          f"Téléchargement impossible : {exc}"))
                    continue
                busy(f"{label} — l'installateur est ouvert : suivez ses étapes, la suite reprendra à sa "
                     "fermeture.")
                notify(f"Installateur {pkg.vendor} ouvert",
                       "Suivez ses étapes : la liste des pilotes sera actualisée à sa fermeture.", "info")
                code = launch_and_wait(path)
                try:
                    path.unlink(missing_ok=True)     # parfois plusieurs centaines de Mo, inutile une fois installé
                except OSError:
                    pass
                if code is None:
                    report.outcomes.append(InstallOutcome(pkg.key, pkg.title, False,
                                                          "L'installateur n'a pas pu être lancé (autorisation "
                                                          "refusée ?)."))
                elif code in packages.SUCCESS_CODES:
                    report.reboot_required |= code in packages.REBOOT_CODES
                    report.outcomes.append(InstallOutcome(pkg.key, pkg.title, True,
                                                          f"installé (fichier vérifié, signé par {pkg.publisher})"))
                    packages.remember_install(pkg, concerned.get(pkg.key, []))
                else:
                    report.outcomes.append(InstallOutcome(pkg.key, pkg.title, False,
                                                          f"installateur fermé avant la fin ou en erreur (code "
                                                          f"{code}) : la liste actualisée montre la version en "
                                                          "place."))
            return report

        def done(result: InstallReport) -> None:
            self.stepper.set(len(STEPS))
            summary = f"{result.succeeded} pilote(s) installé(s), {result.failed} échec(s)."
            self.status.idle(f"{summary} Actualisation de la liste des pilotes…")
            self.app.notify("Installation des pilotes terminée", summary,
                            "success" if not result.failed else "warning")
            self._show_install_result(result)
            self._refresh_after_install()

        def finished() -> None:
            self._show_install_controls(False)
            self._set_busy(False)

        self.dispatcher.run(work, on_done=done, on_error=self._install_error, on_finally=finished)

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

    def start_install(self, ids: list[str] | None = None) -> None:
        """Installe des pilotes proposés par Windows Update (onglet Windows Update ou ligne de l'analyse)."""
        if self.busy:
            return
        ids = ids if ids is not None else self.updates_table.checked()
        if not ids:
            self.status.set_text("Cochez au moins un pilote à installer.")
            return
        self.install_plan(wu_ids=ids)

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
