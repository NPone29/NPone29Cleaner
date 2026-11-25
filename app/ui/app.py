"""Fenêtre principale : barre latérale de navigation + zone de contenu."""

from __future__ import annotations

import logging
import tkinter.font as tkfont
import traceback

import customtkinter as ctk

from app.config import APP_NAME, APP_VERSION
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
from app.ui.widgets import ResultDialog, Toast, show_error
from app.utils import notify as winnotify
from app.utils.paths import resource_path
from app.utils.settings import Settings
from app.utils.system import is_admin, relaunch_as_admin

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

        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self._build_sidebar()

        self.content = ctk.CTkFrame(self, fg_color="transparent")
        self.content.grid(row=0, column=1, sticky="nsew")
        self.content.grid_columnconfigure(0, weight=1)
        self.content.grid_rowconfigure(1, weight=1)
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

    def _build_admin_banner(self) -> None:
        banner = ctk.CTkFrame(self.content, fg_color=("#fff4e5", "#3a2e17"), corner_radius=10)
        banner.grid(row=0, column=0, sticky="ew", padx=28, pady=(18, 0))
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
