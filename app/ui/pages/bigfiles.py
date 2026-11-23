"""Page Gros fichiers : trouver ce qui prend de la place sur le disque."""

from __future__ import annotations

import os
import threading
from pathlib import Path
from tkinter import filedialog

import customtkinter as ctk

from app.core.bigfiles import FoundFile, find_large_files
from app.ui import theme
from app.ui.pages.base import Page
from app.ui.widgets import (PageHeader, StatusLine, Table, ask_yes_no, danger_button, neutral_button,
                            primary_button)
from app.utils import winapi
from app.utils.fmt import format_date, human_size
from app.utils.system import reveal_in_explorer

SIZES_MB = ["50", "100", "250", "500", "1000"]


class BigFilesPage(Page):
    page_id = "bigfiles"

    def __init__(self, master, app):
        super().__init__(master, app)
        self.files: list[FoundFile] = []
        self.cancel = threading.Event()
        self.busy = False
        self.folder = ctk.StringVar(value=str(Path.home()))

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(3, weight=1)

        PageHeader(self, "Gros fichiers",
                   "Repérez les fichiers les plus volumineux. Ceux que vous supprimez partent dans la corbeille "
                   "(récupérables).").grid(row=0, column=0, sticky="ew", pady=(0, 14))

        top = ctk.CTkFrame(self, fg_color="transparent")
        top.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        ctk.CTkLabel(top, text="Dossier :", font=theme.font(13)).pack(side="left")
        ctk.CTkEntry(top, textvariable=self.folder, height=34).pack(side="left", fill="x", expand=True, padx=8)
        neutral_button(top, "Parcourir…", self._browse, width=100).pack(side="left")
        neutral_button(top, "Disque C:", lambda: self.folder.set("C:\\"), width=90).pack(side="left", padx=(6, 0))

        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.grid(row=2, column=0, sticky="ew", pady=(0, 10))
        ctk.CTkLabel(bar, text="Taille minimale (Mo) :", font=theme.font(13)).pack(side="left")
        self.min_size = ctk.CTkOptionMenu(bar, values=SIZES_MB, width=90, height=32)
        self.min_size.set(str(self.settings.get("bigfiles_min_mb", 100)))
        self.min_size.pack(side="left", padx=8)
        self.scan_btn = primary_button(bar, "Rechercher", self._scan, width=120)
        self.scan_btn.pack(side="left", padx=8)
        self.cancel_btn = neutral_button(bar, "Arrêter", self.cancel.set, width=90)
        danger_button(bar, "Mettre à la corbeille", self._delete, width=10).pack(side="right")
        neutral_button(bar, "Ouvrir l'emplacement", self._reveal, width=10).pack(side="right", padx=8)

        self.table = Table(self, [
            ("name", "Fichier", 260, "w", True),
            ("size", "Taille", 100, "e", False),
            ("date", "Modifié le", 100, "center", False),
            ("folder", "Dossier", 420, "w", True),
        ], on_double_click=lambda _iid: self._reveal())
        self.table.grid(row=3, column=0, sticky="nsew")

        self.status = StatusLine(self)
        self.status.grid(row=4, column=0, sticky="ew", pady=(10, 0))
        self.status.set_text("Choisissez un dossier puis lancez la recherche.")

    def _browse(self) -> None:
        folder = filedialog.askdirectory(parent=self.winfo_toplevel(), initialdir=self.folder.get())
        if folder:
            self.folder.set(os.path.normpath(folder))

    def _set_busy(self, busy: bool) -> None:
        self.busy = busy
        self.scan_btn.configure(state="disabled" if busy else "normal")
        if busy:
            self.cancel_btn.pack(side="left")
        else:
            self.cancel_btn.pack_forget()

    def _scan(self) -> None:
        root = self.folder.get().strip()
        if self.busy or not root:
            return
        if not os.path.isdir(root):
            self.status.set_text("Ce dossier n'existe pas.")
            return
        min_mb = int(self.min_size.get())
        self.settings.set("bigfiles_min_mb", min_mb)
        self.cancel.clear()
        self._set_busy(True)
        self.table.clear()
        self.status.busy("Recherche en cours…")
        progress = self.dispatcher.wrap(
            lambda n, path: self.status.set_text(f"{n} dossiers analysés… {path[-70:]}") if path else None)
        self.dispatcher.run(find_large_files, root, min_mb * 1024 * 1024, 300, self.cancel, progress,
                            on_done=self._scan_done, on_error=lambda e: self.status.idle(f"Erreur : {e}"),
                            on_finally=lambda: self._set_busy(False))

    def _scan_done(self, files: list[FoundFile]) -> None:
        self.files = files
        self._render()
        total = sum(f.size for f in files)
        self.status.idle(f"{len(files)} fichier(s) trouvé(s), {human_size(total)} au total.")

    def _render(self) -> None:
        rows = [(str(i), [f.name, human_size(f.size), format_date(f.modified), f.folder],
                 [f.name, f.size, f.modified, f.folder], ()) for i, f in enumerate(self.files)]
        self.table.set_rows(rows)

    def _selected_files(self) -> list[FoundFile]:
        return [self.files[int(i)] for i in self.table.selected()]

    def _reveal(self) -> None:
        files = self._selected_files()
        if files:
            reveal_in_explorer(files[0].path)

    def _delete(self) -> None:
        files = self._selected_files()
        if not files:
            self.status.set_text("Sélectionnez un ou plusieurs fichiers.")
            return
        total = sum(f.size for f in files)
        names = "\n".join(f"• {f.name} ({human_size(f.size)})" for f in files[:10])
        if len(files) > 10:
            names += "\n• …"
        if not ask_yes_no(self, "Mettre à la corbeille",
                          f"Envoyer {len(files)} fichier(s) à la corbeille ({human_size(total)}) ?\n\n{names}"):
            return
        removed = 0
        for f in files:
            if winapi.send_to_recycle_bin(f.path):
                removed += 1
        self.files = [f for f in self.files if os.path.exists(f.path)]
        self._render()
        self.status.set_text(f"{removed} fichier(s) envoyé(s) à la corbeille. "
                             "Videz la corbeille (page Nettoyage) pour libérer l'espace.")
