"""Fenêtre principale : barre latérale de navigation + zone de contenu."""

from __future__ import annotations

import logging
import os
import sys
import threading
import tkinter.font as tkfont
import traceback

import customtkinter as ctk

from app.config import APP_NAME, APP_VERSION
from app.core import app_update
from app.core.app_update import Release
from app.core.drivers import WindowsUpdateDriverService
from app.ui import theme
from app.ui.pages.assistant import AssistantPage
from app.ui.pages.backup import BackupPage
from app.ui.pages.base import Page
from app.ui.pages.bigfiles import BigFilesPage
from app.ui.pages.cleaner import CleanerPage
from app.ui.pages.dashboard import DashboardPage
from app.ui.pages.drivers import DriversPage
from app.ui.pages.hardware import HardwarePage
from app.ui.pages.software import SoftwarePage
from app.ui.pages.startup import StartupPage
from app.ui.pages.tools import ToolsPage
from app.ui.pages.uninstall import UninstallPage
from app.ui.tasks import UiDispatcher
from app.ui.widgets import (ResultDialog, Toast, ask_yes_no, install_fast_scrolling, neutral_button,
                            show_error, show_info, show_warning, success_button)
from app.utils import notify as winnotify
from app.utils.paths import resource_path
from app.utils.settings import Settings
from app.utils.system import is_admin, open_url, relaunch_as_admin

log = logging.getLogger(__name__)

# (id, libellé, glyphe Segoe Fluent/MDL2, classe de page)
NAVIGATION: list[tuple[str, str, str, type[Page]]] = [
    ("dashboard", "Tableau de bord", "", DashboardPage),
    ("assistant", "Entretien guidé", "", AssistantPage),
    ("hardware", "Mon PC", "", HardwarePage),
    ("backup", "Sauvegarde", "", BackupPage),
    ("cleaner", "Nettoyage", "", CleanerPage),
    ("drivers", "Pilotes", "", DriversPage),
    ("software", "Logiciels", "", SoftwarePage),
    ("startup", "Démarrage", "", StartupPage),
    ("uninstall", "Désinstallation", "", UninstallPage),
    ("bigfiles", "Gros fichiers", "", BigFilesPage),
    ("tools", "Outils", "", ToolsPage),
]

APPEARANCES = {"Sombre": "dark", "Clair": "light", "Système": "system"}


class UpdateBanner(ctk.CTkFrame):
    """Bandeau « Nouvelle version disponible » en haut de la fenêtre."""

    TEXT_COLOR = ("#1e3a8a", "#bfdbfe")

    def __init__(self, master, window: "MainWindow", release: Release):
        super().__init__(master, fg_color=("#e8f0fe", "#1c2a45"), corner_radius=10)
        self.window = window
        self.release = release
        self.label = ctk.CTkLabel(self, text="", font=theme.font(12), text_color=self.TEXT_COLOR, anchor="w",
                                  justify="left", wraplength=560)
        self.label.pack(side="left", padx=14, pady=10, fill="x", expand=True)
        self.buttons = ctk.CTkFrame(self, fg_color="transparent")
        self.buttons.pack(side="right", padx=10, pady=8)
        self.bar = ctk.CTkProgressBar(self, width=160, height=8)
        self._downloading = False
        self.show_available()

    def _set_buttons(self, *buttons: tuple[str, object, str]) -> None:
        for child in self.buttons.winfo_children():
            child.destroy()
        for text, command, kind in buttons:
            make = success_button if kind == "primary" else neutral_button
            make(self.buttons, text, command, width=10).pack(side="left", padx=4)

    def show_available(self) -> None:
        r = self.release
        installable = app_update.can_install(r)
        beta = " (bêta)" if r.prerelease else ""
        self._downloading = False
        self.bar.pack_forget()
        self.label.configure(text=f"Nouvelle version disponible : {r.version}{beta} (vous avez la {APP_VERSION}).")
        main = ("Installer maintenant", lambda: self.window.install_update(r), "primary") if installable else \
            ("Télécharger", lambda: self.window.install_update(r), "primary")
        self._set_buttons(main, ("Nouveautés", lambda: open_url(r.page_url), ""),
                          ("Ignorer cette version", self.window.skip_update, ""))

    def show_progress(self, done: int, total: int) -> None:
        pct = f" {done * 100 // total} %" if total else ""
        self.label.configure(text=f"Téléchargement de la version {self.release.version}…{pct}")
        if not self._downloading:
            self._downloading = True
            self.bar.pack(side="right", padx=(6, 4))     # à gauche des boutons
            self._set_buttons(("Annuler", self.window.cancel_update, ""))
        self.bar.set(done / total if total else 0)

    def show_installing(self) -> None:
        self._downloading = False
        self.bar.pack_forget()
        self._set_buttons()
        self.label.configure(text=f"Installation de la version {self.release.version} : l'application va se "
                                  "fermer puis se rouvrir toute seule…")


class NavItem(ctk.CTkFrame):
    """Entrée de la barre latérale : icône (police Windows) + libellé, survol et état actif."""

    def __init__(self, master, glyph: str, label: str, icon_family: str, command):
        super().__init__(master, height=38, corner_radius=8, fg_color="transparent")
        self.pack_propagate(False)
        self._command = command
        self._active = False
        self.marker = ctk.CTkFrame(self, width=4, height=20, corner_radius=2, fg_color="transparent")
        self.marker.pack(side="left", padx=(4, 0))
        self.icon = ctk.CTkLabel(self, text=glyph, width=24, text_color=theme.MUTED,
                                 font=(icon_family, 16) if icon_family else theme.font(14))
        self.icon.pack(side="left", padx=(10, 10))
        self.text = ctk.CTkLabel(self, text=label, font=theme.font(14), anchor="w", text_color=theme.NEUTRAL_TEXT)
        self.text.pack(side="left", fill="x", expand=True)
        for widget in (self, self.icon, self.text):
            widget.bind("<Button-1>", lambda _e: self._command())
            widget.bind("<Enter>", lambda _e: self._hover(True))
            widget.bind("<Leave>", lambda _e: self._hover(False))
        self.configure(cursor="hand2")

    def _hover(self, inside: bool) -> None:
        if not self._active:
            self.configure(fg_color=theme.SIDEBAR_HOVER if inside else "transparent")

    def set_active(self, active: bool) -> None:
        self._active = active
        self.configure(fg_color=theme.SIDEBAR_ACTIVE if active else "transparent")
        self.marker.configure(fg_color=theme.ACCENT if active else "transparent")
        self.icon.configure(text_color=theme.ACCENT if active else theme.MUTED)
        self.text.configure(font=theme.font(14, "bold" if active else "normal"))


class MainWindow(ctk.CTk):
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or Settings()
        ctk.set_appearance_mode(self.settings.get("appearance", "dark"))
        ctk.set_default_color_theme("blue")
        super().__init__(fg_color=theme.WINDOW_BG)

        self.title(f"{APP_NAME} {APP_VERSION}")
        self.geometry("1200x780")
        self.minsize(1000, 680)
        icon = resource_path("assets/icon.ico")
        if icon.exists():
            try:
                self.iconbitmap(default=str(icon))
            except Exception:
                log.debug("Icône non chargée", exc_info=True)

        self.admin = is_admin()
        self.dispatcher = UiDispatcher(self)
        self.driver_service = WindowsUpdateDriverService()
        self.icon_family = self._icon_font_family()

        theme.apply_treeview_style(self)
        install_fast_scrolling()

        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self._build_sidebar()

        self.content = ctk.CTkFrame(self, fg_color="transparent")
        self.content.grid(row=0, column=1, sticky="nsew")
        self.content.grid_columnconfigure(0, weight=1)
        self.content.grid_rowconfigure(1, weight=1)
        # Bandeaux en haut de la page (mode limité, nouvelle version), affichés seulement s'il y en a.
        self.banner_area = ctk.CTkFrame(self.content, fg_color="transparent")
        self.update_banner: UpdateBanner | None = None
        self._update_cancel: threading.Event | None = None
        if not self.admin:
            self._build_admin_banner()

        self.page_host = ctk.CTkFrame(self.content, fg_color="transparent")
        self.page_host.grid(row=1, column=0, sticky="nsew", padx=28, pady=(18, 20))
        self.page_host.grid_columnconfigure(0, weight=1)
        self.page_host.grid_rowconfigure(0, weight=1)

        self.pages: dict[str, Page] = {}
        self.current: str | None = None
        self.toasts: list[Toast] = []
        self.notifier: winnotify.WindowsNotifier | None = None
        self.show_page("dashboard")
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        if not self.settings.get("welcome_done", False):
            self.after(700, self._show_welcome)
        if "--selftest" not in sys.argv:
            self.after(4000, self.check_update)

    # ------------------------------------------------------------------ construction
    def _icon_font_family(self) -> str:
        families = set(tkfont.families(self))
        for name in ("Segoe Fluent Icons", "Segoe MDL2 Assets"):
            if name in families:
                return name
        return ""

    def _build_sidebar(self) -> None:
        bar = ctk.CTkFrame(self, width=232, corner_radius=0, fg_color=theme.SIDEBAR_BG)
        bar.grid(row=0, column=0, sticky="nsw")
        bar.grid_propagate(False)
        bar.grid_columnconfigure(0, weight=1)

        brand = ctk.CTkFrame(bar, fg_color="transparent")
        brand.grid(row=0, column=0, sticky="ew", padx=20, pady=(20, 16))
        ctk.CTkLabel(brand, text="NPone29", font=theme.font(22, "bold"), anchor="w").pack(anchor="w")
        ctk.CTkLabel(brand, text="Cleaner", font=theme.font(15), text_color=theme.ACCENT,
                     anchor="w").pack(anchor="w", pady=(0, 0))

        self.nav_buttons: dict[str, NavItem] = {}
        for row, (page_id, label, glyph, _cls) in enumerate(NAVIGATION, start=1):
            item = NavItem(bar, glyph if self.icon_family else "", label, self.icon_family,
                           command=lambda p=page_id: self.show_page(p))
            item.grid(row=row, column=0, sticky="ew", padx=12, pady=1)
            self.nav_buttons[page_id] = item

        bar.grid_rowconfigure(len(NAVIGATION) + 1, weight=1)

        bottom = ctk.CTkFrame(bar, fg_color="transparent")
        bottom.grid(row=len(NAVIGATION) + 2, column=0, sticky="ew", padx=16, pady=(0, 18))
        status = "Administrateur" if self.admin else "Utilisateur standard"
        color = theme.SUCCESS if self.admin else theme.WARNING
        ctk.CTkLabel(bottom, text=f"● {status}", font=theme.font(12), text_color=color,
                     anchor="w").pack(anchor="w", pady=(0, 8))
        ctk.CTkLabel(bottom, text="Thème", font=theme.font(12), text_color=theme.MUTED,
                     anchor="w").pack(anchor="w")
        current = {v: k for k, v in APPEARANCES.items()}.get(self.settings.get("appearance"), "Sombre")
        seg = ctk.CTkSegmentedButton(bottom, values=list(APPEARANCES), command=self._change_appearance,
                                     font=theme.font(12))
        seg.set(current)
        seg.pack(fill="x", pady=(4, 10))
        ctk.CTkLabel(bottom, text=f"Version {APP_VERSION} • gratuit", font=theme.font(11),
                     text_color=theme.MUTED, anchor="w").pack(anchor="w")

    def _show_banner_area(self) -> None:
        self.banner_area.grid(row=0, column=0, sticky="ew", padx=28, pady=(18, 0))

    def _build_admin_banner(self) -> None:
        self._show_banner_area()
        banner = ctk.CTkFrame(self.banner_area, fg_color=("#fff4e5", "#3a2e17"), corner_radius=10)
        banner.pack(fill="x", pady=(0, 6))
        ctk.CTkLabel(
            banner, text="Mode limité : certaines fonctions (pilotes, fichiers système, démarrage global) "
                         "nécessitent les droits administrateur.",
            font=theme.font(12), text_color=("#7c4a03", "#fcd9a0"), anchor="w", justify="left", wraplength=640,
        ).pack(side="left", padx=14, pady=10, fill="x", expand=True)
        ctk.CTkButton(banner, text="Relancer en administrateur", width=200, height=32,
                      fg_color=theme.WARNING, hover_color=("#9a3412", "#d97706"), text_color="#ffffff",
                      font=theme.font(12, "bold"), command=self.restart_as_admin).pack(side="right", padx=12, pady=8)

    # ------------------------------------------------------------------ navigation
    def get_page(self, page_id: str) -> Page:
        """Renvoie la page (en la créant au besoin) sans l'afficher."""
        page = self.pages.get(page_id)
        if page is None:
            cls = next(c for pid, _l, _g, c in NAVIGATION if pid == page_id)
            page = cls(self.page_host, self)
            self.pages[page_id] = page
        return page

    def show_page(self, page_id: str) -> Page:
        if self.current == page_id:
            return self.pages[page_id]
        if self.current and self.current in self.pages:
            self.pages[self.current].on_hide()
            self.pages[self.current].grid_remove()
        page = self.get_page(page_id)
        page.grid(row=0, column=0, sticky="nsew")
        self.current = page_id
        for pid, item in self.nav_buttons.items():
            item.set_active(pid == page_id)
        page.on_show()
        return page

    def _change_appearance(self, label: str) -> None:
        mode = APPEARANCES.get(label, "dark")
        ctk.set_appearance_mode(mode)
        self.settings.set("appearance", mode)
        # Laisse CustomTkinter appliquer le mode avant de recolorer les tableaux ttk et les dessins.
        self.after(50, self._after_theme_change)

    def _after_theme_change(self) -> None:
        theme.apply_treeview_style(self)
        for page in self.pages.values():
            callback = getattr(page, "on_theme_change", None)
            if callback:
                callback()

    # ------------------------------------------------------------------ notifications
    def _hwnd(self) -> int:
        try:
            return int(self.wm_frame(), 16)
        except (ValueError, TypeError):
            return 0

    def notify(self, title: str, message: str, level: str = "success",
               action: tuple[str, object] | None = None) -> None:
        """Prévient l'utilisateur qu'une tâche est terminée.

        Toujours : une bannière dans l'application + un son.
        Si la fenêtre n'est pas au premier plan : notification Windows + clignotement de la barre des tâches.
        """
        log.info("Notification : %s — %s", title, message)
        toast = Toast(self.content, title, message, level, action=action, on_close=self._toast_closed)
        self.toasts.append(toast)
        self._layout_toasts()
        winnotify.play_sound(level)
        hwnd = self._hwnd()
        if hwnd and not winnotify.is_foreground(hwnd):
            if self.notifier is None:
                self.notifier = winnotify.WindowsNotifier(hwnd, resource_path("assets/icon.ico"), APP_NAME)
            self.notifier.show(title, message, level)
            winnotify.flash_taskbar(hwnd)

    def _toast_closed(self, toast: Toast) -> None:
        if toast in self.toasts:
            self.toasts.remove(toast)
        self.after(10, self._layout_toasts)

    def _layout_toasts(self) -> None:
        y = 16
        for toast in self.toasts[-3:]:
            try:
                toast.place(relx=1.0, x=-24, y=y, anchor="ne")
                toast.lift()
                toast.update_idletasks()
                y += toast.winfo_reqheight() / ctk.ScalingTracker.get_widget_scaling(toast) + 10
            except Exception:
                continue

    def _show_welcome(self) -> None:
        self.settings.set("welcome_done", True)
        ResultDialog(
            self, "Bienvenue", "Bienvenue dans NPone29 Cleaner !",
            stats=[("1. Entretien guidé", "tout vérifier en quelques clics"),
                   ("2. Mon PC", "comprendre votre matériel"),
                   ("3. Pilotes et logiciels", "à jour, depuis les sources officielles")],
            tips=["Rien n'est jamais supprimé ni installé sans votre accord.",
                  "Chaque page explique ce qu'elle fait : laissez-vous guider par les étapes en haut."],
            actions=[("▶  Lancer l'entretien guidé (recommandé)", lambda: self.show_page("assistant").start()),
                     ("Découvrir la carte de mon PC", lambda: self.show_page("hardware"))],
            level="info", close_text="Explorer par moi-même", actions_title="Par où commencer ?")

    # ------------------------------------------------------------------ mises à jour de l'application
    def check_update(self, manual: bool = False) -> None:
        """Cherche une nouvelle version (au démarrage, ou depuis la page Outils avec `manual`)."""
        if not manual and not self.settings.get("update_check", True):
            return

        def done(release: Release | None) -> None:
            if release is None:
                if manual:
                    show_info(self, "Mises à jour", f"Vous avez la dernière version de {APP_NAME} ({APP_VERSION}).")
                return
            if not manual and release.version == self.settings.get("update_skip", ""):
                return
            self._show_update_banner(release)

        def failed(exc: BaseException) -> None:
            log.info("Recherche de mise à jour impossible : %s", exc)
            if manual:
                show_warning(self, "Mises à jour", str(exc))

        self.dispatcher.run(app_update.check_for_update, on_done=done, on_error=failed)

    def _show_update_banner(self, release: Release) -> None:
        if self.update_banner is not None:
            if self._update_cancel is not None:
                return     # téléchargement en cours
            self.update_banner.destroy()
        self._show_banner_area()
        self.update_banner = UpdateBanner(self.banner_area, self, release)
        self.update_banner.pack(fill="x", pady=(0, 6))

    def _hide_update_banner(self) -> None:
        if self.update_banner is not None:
            self.update_banner.destroy()
            self.update_banner = None
        if not self.banner_area.winfo_children():
            self.banner_area.grid_forget()

    def skip_update(self) -> None:
        if self.update_banner is not None:
            self.settings.set("update_skip", self.update_banner.release.version)
            self.settings.save()
        self._hide_update_banner()
        self.notify("Version ignorée", "Elle ne sera plus proposée au démarrage. Vous pourrez toujours l'installer "
                                       "depuis la page Outils.", "info")

    def _busy_pages(self) -> list[str]:
        labels = {pid: label for pid, label, _g, _c in NAVIGATION}
        return [labels.get(pid, pid) for pid, page in self.pages.items()
                if getattr(page, "busy", False) or getattr(page, "running", False)]

    def install_update(self, release: Release) -> None:
        kind = app_update.install_kind()
        if not app_update.can_install(release, kind):
            if kind == "portable":
                show_info(self, "Nouvelle version",
                          f"La page de la version {release.version} va s'ouvrir.\n\nTéléchargez « "
                          f"{release.setup.name if release.setup else app_update.PORTABLE_NAME} » : l'installateur "
                          "permet ensuite les mises à jour automatiques.")
            open_url(release.page_url)
            return
        busy = self._busy_pages()
        if busy:
            show_warning(self, "Opération en cours",
                         f"Attendez la fin de l'opération en cours ({', '.join(busy)}) avant de mettre à jour "
                         "l'application.")
            return
        notes = app_update.short_notes(release.notes)
        if not ask_yes_no(self, "Mettre à jour NPone29 Cleaner",
                          f"Installer la version {release.version} (vous avez la {APP_VERSION}) ?\n\n"
                          + (f"Nouveautés :\n{notes}\n\n" if notes else "")
                          + "L'application va se fermer, s'installer puis se rouvrir toute seule. "
                            "Vos réglages et vos sauvegardes sont conservés."):
            return
        self._update_cancel = cancel = threading.Event()
        progress = self.dispatcher.wrap(self._update_progress)
        if self.update_banner is None:
            self._show_update_banner(release)
        self.update_banner.show_progress(0, 0)
        self.dispatcher.run(app_update.download_installer, release, progress, cancel,
                            on_done=self._launch_update, on_error=self._update_failed)

    def _update_progress(self, done: int, total: int) -> None:
        if self.update_banner is not None and self._update_cancel is not None:
            self.update_banner.show_progress(done, total)

    def cancel_update(self) -> None:
        if self._update_cancel is not None:
            self._update_cancel.set()

    def _launch_update(self, path) -> None:
        self._update_cancel = None
        if self.update_banner is not None:
            self.update_banner.show_installing()
        try:
            app_update.launch_installer(path)
        except OSError as exc:
            self._update_failed(exc)
            return
        self.after(1500, self._exit_for_update)

    def _exit_for_update(self) -> None:
        """Ferme l'application tout de suite : l'installateur attend sa fin pour remplacer le .exe."""
        self.on_close()
        logging.shutdown()
        os._exit(0)     # sans attendre un éventuel appel Windows Update bloqué en arrière-plan

    def _update_failed(self, exc: BaseException) -> None:
        from app.utils.download import DownloadCancelled

        self._update_cancel = None
        if self.update_banner is not None:
            self.update_banner.show_available()
        if isinstance(exc, DownloadCancelled):
            return
        log.warning("Mise à jour de l'application impossible : %s", exc)
        show_warning(self, "Mise à jour impossible", f"{exc}\n\nRien n'a été modifié : vous pouvez réessayer plus tard.")

    # ------------------------------------------------------------------ divers
    def restart_as_admin(self) -> None:
        if relaunch_as_admin():
            self.on_close()
        else:
            show_error(self, "Élévation refusée", "Impossible d'obtenir les droits administrateur.")

    def report_callback_exception(self, exc, val, tb) -> None:  # noqa: N802 - API Tkinter
        log.error("Erreur non gérée :\n%s", "".join(traceback.format_exception(exc, val, tb)))
        try:
            show_error(self, "Erreur inattendue", f"{val}\n\nLes détails ont été enregistrés dans le journal.")
        except Exception:
            pass

    def on_close(self) -> None:
        self.cancel_update()
        for page in self.pages.values():
            try:
                page.on_hide()
            except Exception:
                pass
        self.driver_service.shutdown()
        if self.notifier:
            self.notifier.remove()
        self.settings.save()
        self.destroy()
