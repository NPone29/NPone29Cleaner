"""Tableau de bord : état du PC et raccourcis vers les actions principales."""

from __future__ import annotations

import customtkinter as ctk

from app.core import sysinfo
from app.ui import theme
from app.ui.pages.base import Page
from app.ui.widgets import Card, PageHeader, neutral_button, primary_button
from app.utils.fmt import format_duration, human_size


class DashboardPage(Page):
    page_id = "dashboard"
    REFRESH_MS = 2000

    def __init__(self, master, app):
        super().__init__(master, app)
        self._after_id = None
        self.grid_columnconfigure((0, 1), weight=1, uniform="col")

        PageHeader(self, "Tableau de bord", "Vue d'ensemble de votre PC et accès rapide à l'entretien.") \
            .grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 14))

        # ---- Entretien guidé (action recommandée)
        hero = ctk.CTkFrame(self, fg_color=theme.ACCENT, corner_radius=14)
        hero.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(0, 12))
        hero.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(hero, text="Entretien guidé", font=theme.font(20, "bold"), text_color="#ffffff",
                     anchor="w").grid(row=0, column=0, sticky="w", padx=22, pady=(18, 0))
        last = self.settings.get("last_maintenance")
        sub_text = ("Nettoyage, pilotes, logiciels et démarrage vérifiés en une seule fois. "
                    "Rien n'est modifié sans votre accord.")
        if last:
            sub_text += f"\nDernier entretien : {last}"
        self.hero_text = ctk.CTkLabel(hero, text=sub_text, font=theme.font(13), text_color="#e0e7ff", anchor="w",
                                      justify="left", wraplength=560)
        self.hero_text.grid(row=1, column=0, sticky="w", padx=22, pady=(2, 18))
        ctk.CTkButton(hero, text="▶  Démarrer", width=150, height=42, corner_radius=10, font=theme.font(15, "bold"),
                      fg_color="#ffffff", hover_color="#e0e7ff", text_color="#1d4ed8",
                      command=lambda: self._goto("assistant", "start")).grid(row=0, column=1, rowspan=2, padx=22)

        # ---- Actions rapides
        actions = Card(self)
        actions.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(0, 12))
        actions.grid_columnconfigure((0, 1, 2, 3), weight=1, uniform="a")
        self._action(actions, 0, "Nettoyer le PC", "Fichiers temporaires, caches, corbeille…",
                     "Analyser", lambda: self._goto("cleaner", "start_scan"))
        self._action(actions, 1, "Pilotes", "Analyse de tous les pilotes et sites officiels.",
                     "Analyser", lambda: self._goto("drivers", "start_analysis"))
        self._action(actions, 2, "Logiciels", "Mises à jour de vos applications.",
                     "Vérifier", lambda: self._goto("software", "start_search"))
        self._action(actions, 3, "Mon PC", "La carte de votre matériel, expliquée simplement.",
                     "Découvrir", lambda: self.app.show_page("hardware"), neutral=True)

        # ---- Système
        self.sys_card = Card(self)
        self.sys_card.grid(row=3, column=0, sticky="nsew", padx=(0, 7))
        ctk.CTkLabel(self.sys_card, text="Système", font=theme.font(16, "bold")).pack(anchor="w", padx=18, pady=(16, 8))
        self.sys_rows = ctk.CTkFrame(self.sys_card, fg_color="transparent")
        self.sys_rows.pack(fill="x", padx=18, pady=(0, 16))
        self.sys_rows.grid_columnconfigure(1, weight=1)

        # ---- Utilisation en direct
        usage = Card(self)
        usage.grid(row=3, column=1, sticky="nsew", padx=(7, 0))
        ctk.CTkLabel(usage, text="Utilisation", font=theme.font(16, "bold")).pack(anchor="w", padx=18, pady=(16, 8))
        self.cpu_label, self.cpu_bar = self._meter(usage, "Processeur")
        self.ram_label, self.ram_bar = self._meter(usage, "Mémoire vive")
        ctk.CTkLabel(usage, text="Disques", font=theme.font(14, "bold")).pack(anchor="w", padx=18, pady=(12, 4))
        self.disk_frame = ctk.CTkFrame(usage, fg_color="transparent")
        self.disk_frame.pack(fill="x", padx=18, pady=(0, 16))

        self.grid_rowconfigure(4, weight=1)
        self._load_static()

    # ------------------------------------------------------------------ construction
    def _action(self, parent, col, title, text, button, command, neutral: bool = False) -> None:
        box = ctk.CTkFrame(parent, fg_color="transparent")
        box.grid(row=0, column=col, sticky="nsew", padx=16, pady=14)
        ctk.CTkLabel(box, text=title, font=theme.font(15, "bold"), anchor="w").pack(anchor="w")
        ctk.CTkLabel(box, text=text, font=theme.font(12), text_color=theme.MUTED, anchor="w",
                     wraplength=190, justify="left", height=36).pack(anchor="w", pady=(2, 8))
        (neutral_button if neutral else primary_button)(box, button, command, width=120).pack(anchor="w")

    def _meter(self, parent, title):
        frame = ctk.CTkFrame(parent, fg_color="transparent")
        frame.pack(fill="x", padx=18, pady=4)
        top = ctk.CTkFrame(frame, fg_color="transparent")
        top.pack(fill="x")
        ctk.CTkLabel(top, text=title, font=theme.font(13)).pack(side="left")
        value = ctk.CTkLabel(top, text="—", font=theme.font(13, "bold"))
        value.pack(side="right")
        bar = ctk.CTkProgressBar(frame, height=10)
        bar.set(0)
        bar.pack(fill="x", pady=(4, 0))
        return value, bar

    def _goto(self, page_id: str, method: str) -> None:
        page = self.app.show_page(page_id)
        getattr(page, method)()

    # ------------------------------------------------------------------ données
    def _load_static(self) -> None:
        self.dispatcher.run(sysinfo.get_system_info, on_done=self._show_static)

    def _show_static(self, info: sysinfo.SystemInfo) -> None:
        for child in self.sys_rows.winfo_children():
            child.destroy()
        model = " ".join(x for x in (info.manufacturer, info.model) if x and "o.e.m" not in x.lower())
        rows = [
            ("Windows", info.os_name),
            ("Nom du PC", info.computer_name),
            ("Modèle", model or "—"),
            ("Processeur", f"{info.cpu_name}\n{info.cpu_cores} cœurs / {info.cpu_threads} threads"),
            ("Mémoire", human_size(info.ram_total)),
            ("Allumé depuis", format_duration(info.uptime)),
        ]
        for r, (key, value) in enumerate(rows):
            ctk.CTkLabel(self.sys_rows, text=key, font=theme.font(13), text_color=theme.MUTED,
                         anchor="nw").grid(row=r, column=0, sticky="nw", pady=4, padx=(0, 16))
            ctk.CTkLabel(self.sys_rows, text=value, font=theme.font(13), anchor="w", justify="left",
                         wraplength=330).grid(row=r, column=1, sticky="w", pady=4)

        for child in self.disk_frame.winfo_children():
            child.destroy()
        for disk in info.disks:
            line = ctk.CTkFrame(self.disk_frame, fg_color="transparent")
            line.pack(fill="x", pady=4)
            head = ctk.CTkFrame(line, fg_color="transparent")
            head.pack(fill="x")
            ctk.CTkLabel(head, text=disk.mount, font=theme.font(13, "bold")).pack(side="left")
            ctk.CTkLabel(head, text=f"{human_size(disk.free)} libres sur {human_size(disk.total)}",
                         font=theme.font(12), text_color=theme.MUTED).pack(side="right")
            bar = ctk.CTkProgressBar(line, height=10,
                                     progress_color=theme.DANGER if disk.percent > 90 else theme.ACCENT)
            bar.set(disk.percent / 100)
            bar.pack(fill="x", pady=(4, 0))

    def _refresh_usage(self) -> None:
        cpu, ram = sysinfo.live_usage()
        self.cpu_label.configure(text=f"{cpu:.0f} %")
        self.cpu_bar.set(cpu / 100)
        self.ram_label.configure(text=f"{ram:.0f} %")
        self.ram_bar.set(ram / 100)
        self._after_id = self.after(self.REFRESH_MS, self._refresh_usage)

    def on_show(self) -> None:
        if self._after_id is None:
            self._refresh_usage()

    def on_hide(self) -> None:
        if self._after_id is not None:
            self.after_cancel(self._after_id)
            self._after_id = None
