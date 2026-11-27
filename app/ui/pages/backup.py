"""Page Sauvegarde : copier ses fichiers sur un autre disque et les retrouver si le PC tombe en panne."""

from __future__ import annotations

import os
import shutil
import threading
from datetime import datetime
from pathlib import Path
from tkinter import filedialog

import customtkinter as ctk
import psutil

from app.core import backup, backup_schedule
from app.core.backup import (DIFFERENTIAL, FOLDER, FULL, INCREMENTAL, KIND_LABELS, ORIGINAL, BackupPlan,
                             BackupResult, BackupSet, DestinationCheck, Progress, RestoreResult, VerifyResult)
from app.core.backup_schedule import FREQUENCIES, FULL_EVERY, BackupLock, TaskInfo
from app.ui import theme
from app.ui.pages.base import Page
from app.ui.widgets import (LEVEL_COLORS, Card, PageHeader, ResultDialog, StatusLine, Table, TipBox, ask_yes_no,
                            danger_button, neutral_button, primary_button, show_error, show_info, show_warning)
from app.utils.fmt import human_size
from app.utils.system import open_path, reveal_in_explorer

TABS = ["Sauvegarder", "Restaurer"]
MODES = [FULL, DIFFERENTIAL, INCREMENTAL]
MODE_HELP = {
    FULL: "Copie tous les fichiers à chaque fois. La plus simple à restaurer, mais la plus longue et celle qui "
          "prend le plus de place.",
    DIFFERENTIAL: "Copie ce qui a changé depuis la dernière sauvegarde complète. Pour restaurer, il suffit de "
                  "la complète et de la différentielle choisie. Bon compromis.",
    INCREMENTAL: "Copie uniquement ce qui a changé depuis la sauvegarde précédente, quelle qu'elle soit. "
                 "La plus rapide et la plus légère ; la restauration utilise toute la chaîne de sauvegardes "
                 "(l'application s'en charge).",
}
KEEP_CHOICES = {"Tout garder": 0, "Les 2 dernières": 2, "Les 3 dernières": 3, "Les 5 dernières": 5,
                "Les 10 dernières": 10}
MAX_FILE_ROWS = 500


def _duration(seconds: float | None) -> str:
    if seconds is None:
        return "—"
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds} s"
    minutes, sec = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes} min {sec:02d} s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours} h {minutes:02d} min"


def _count(n: int) -> str:
    return f"{n:,}".replace(",", " ")


def _ago(when: datetime) -> str:
    delta = datetime.now() - when
    if delta.days >= 2:
        return f"il y a {delta.days} jours"
    if delta.days == 1:
        return "hier"
    hours = delta.seconds // 3600
    if hours:
        return f"il y a {hours} h"
    return f"il y a {max(1, delta.seconds // 60)} min"


def _progress_text(p: Progress) -> str:
    if p.phase == "scan":
        return f"Analyse des dossiers… {_count(p.done_files)} fichiers — {p.current[-70:]}"
    verb = {"copy": "Copie", "restore": "Restauration", "verify": "Vérification"}.get(p.phase, "")
    text = f"{verb} : {_count(p.done_files)} / {_count(p.total_files)} fichiers"
    if p.total_bytes:
        text += f" — {human_size(p.done_bytes)} / {human_size(p.total_bytes)}"
    if p.speed:
        text += f" — {human_size(p.speed)}/s"
    if p.remaining is not None and p.done_bytes:
        text += f" — reste ≈ {_duration(p.remaining)}"
    return text


class BackupPage(Page):
    page_id = "backup"

    def __init__(self, master, app):
        super().__init__(master, app)
        self.cancel = threading.Event()
        self.busy = False
        self.size_labels: dict[str, ctk.CTkLabel] = {}
        self.sizes: dict[str, tuple[int, int] | None] = {}   # chemin -> (fichiers, octets), None = illisible
        self._copying = False
        self.source_vars: dict[str, ctk.BooleanVar] = {}
        self.custom: list[str] = list(self.settings.get("backup_custom", []) or [])
        self.dest_check: DestinationCheck | None = None
        self._check_after: str | None = None
        self._sizes_started = False
        self._lock = BackupLock()      # partagé avec la sauvegarde automatique : jamais deux à la fois
        # Sauvegarde automatique
        self._task: TaskInfo | None = None
        self._task_checked = False
        self._auto_busy = False
        # Restauration
        self.repo: Path | None = None
        self.sets: list[BackupSet] = []
        self.inventory: dict[str, list] = {}
        self.inventory_set: BackupSet | None = None
        self._search_after: str | None = None
        self._file_keys: list[str] = []

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)
        PageHeader(self, "Sauvegarde",
                   "Copiez vos fichiers sur un autre disque pour les retrouver si votre PC tombe en panne. "
                   "Les fichiers restent lisibles tels quels sur le disque de sauvegarde.") \
            .grid(row=0, column=0, sticky="ew", pady=(0, 10))

        self.tabs = ctk.CTkSegmentedButton(self, values=TABS, command=self._switch_tab, font=theme.font(13),
                                           height=34)
        self.tabs.grid(row=1, column=0, sticky="w", pady=(0, 12))
        self.frames = {TABS[0]: self._build_backup_tab(), TABS[1]: self._build_restore_tab()}

        bottom = ctk.CTkFrame(self, fg_color="transparent")
        bottom.grid(row=3, column=0, sticky="ew", pady=(10, 0))
        bottom.grid_columnconfigure(0, weight=1)
        self.status = StatusLine(bottom)
        self.status.grid(row=0, column=0, sticky="ew")
        self.cancel_btn = neutral_button(bottom, "Arrêter", self._cancel, width=90)
        self.tabs.set(TABS[0])
        self._switch_tab(TABS[0])
        self._refresh_last_backup()

    # ================================================================== onglets
    def _switch_tab(self, name: str) -> None:
        self.tabs.set(name)
        for tab, frame in self.frames.items():
            if tab == name:
                frame.grid(row=2, column=0, sticky="nsew")
            else:
                frame.grid_remove()
        if name == TABS[1] and self.repo is None and not self.busy:
            self._open_default_repo()

    def on_show(self) -> None:
        if not self._sizes_started:
            self._sizes_started = True
            self._compute_sizes()
        self._refresh_drives()
        self._schedule_check()
        self._refresh_last_backup()
        self._refresh_auto(query=True)

    def _set_busy(self, busy: bool) -> None:
        self.busy = busy
        state = "disabled" if busy else "normal"
        for btn in (self.start_btn, self.restore_all_btn, self.restore_sel_btn, self.verify_btn, self.delete_btn,
                    self.open_repo_btn):
            btn.configure(state=state)
        if busy:
            self.cancel_btn.grid(row=0, column=1, padx=(12, 0))
        else:
            self.cancel_btn.grid_remove()

    def _cancel(self) -> None:
        self.cancel.set()
        self.status.set_text("Arrêt en cours…")

    # ================================================================== onglet Sauvegarder
    def _build_backup_tab(self) -> ctk.CTkFrame:
        tab = ctk.CTkScrollableFrame(self, fg_color="transparent")
        tab.grid_columnconfigure(0, weight=1)

        self.last_tip = TipBox(tab, "")
        self.last_tip.grid(row=0, column=0, sticky="ew", pady=(0, 10))

        # --- 1. Quoi ?
        what = Card(tab)
        what.grid(row=1, column=0, sticky="ew", pady=(0, 10))
        what.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(what, text="1.  Que sauvegarder ?", font=theme.font(15, "bold"), anchor="w") \
            .grid(row=0, column=0, sticky="w", padx=16, pady=(14, 2))
        ctk.CTkLabel(what, text="Vos dossiers personnels sont proposés. Ajoutez tout autre dossier important "
                                "(projets, comptabilité, sauvegardes de jeux…).",
                     font=theme.font(12), text_color=theme.MUTED, anchor="w", justify="left", wraplength=760) \
            .grid(row=1, column=0, sticky="w", padx=16, pady=(0, 8))
        self.sources_box = ctk.CTkFrame(what, fg_color="transparent")
        self.sources_box.grid(row=2, column=0, sticky="ew", padx=12)
        self.sources_box.grid_columnconfigure(0, weight=1)
        line = ctk.CTkFrame(what, fg_color="transparent")
        line.grid(row=3, column=0, sticky="ew", padx=16, pady=(6, 14))
        neutral_button(line, "+  Ajouter un dossier…", self._add_folder, width=10).pack(side="left")
        self.total_label = ctk.CTkLabel(line, text="", font=theme.font(13, "bold"))
        self.total_label.pack(side="right")
        self._build_sources()

        # --- 2. Où ?
        where = Card(tab)
        where.grid(row=2, column=0, sticky="ew", pady=(0, 10))
        where.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(where, text="2.  Où enregistrer la sauvegarde ?", font=theme.font(15, "bold"), anchor="w") \
            .grid(row=0, column=0, sticky="w", padx=16, pady=(14, 2))
        ctk.CTkLabel(where, text="Choisissez un AUTRE disque que celui du PC : disque dur externe, clé USB ou "
                                 "dossier réseau (NAS). Sinon, une panne emporterait aussi la sauvegarde.",
                     font=theme.font(12), text_color=theme.MUTED, anchor="w", justify="left", wraplength=760) \
            .grid(row=1, column=0, sticky="w", padx=16, pady=(0, 8))
        self.drives_box = ctk.CTkFrame(where, fg_color="transparent")
        self.drives_box.grid(row=2, column=0, sticky="ew", padx=16)
        entry_line = ctk.CTkFrame(where, fg_color="transparent")
        entry_line.grid(row=3, column=0, sticky="ew", padx=16, pady=(8, 4))
        self.destination = ctk.StringVar(value=self.settings.get("backup_destination", "") or "")
        entry = ctk.CTkEntry(entry_line, textvariable=self.destination, height=34,
                             placeholder_text="Ex. E:\\  ou  \\\\NAS\\Sauvegardes")
        entry.pack(side="left", fill="x", expand=True)
        neutral_button(entry_line, "Parcourir…", self._browse_destination, width=100).pack(side="left", padx=(8, 0))
        self.destination.trace_add("write", lambda *_a: self._schedule_check())
        self.dest_label = ctk.CTkLabel(where, text="", font=theme.font(12, "bold"), anchor="w", justify="left",
                                       wraplength=760)
        self.dest_label.grid(row=4, column=0, sticky="w", padx=16, pady=(2, 0))
        self.dest_warn = ctk.CTkLabel(where, text="", font=theme.font(12), text_color=theme.WARNING, anchor="w",
                                      justify="left", wraplength=760)
        self.dest_warn.grid(row=5, column=0, sticky="w", padx=16, pady=(0, 14))

        # --- 3. Comment ?
        how = Card(tab)
        how.grid(row=3, column=0, sticky="ew", pady=(0, 10))
        how.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(how, text="3.  Quel type de sauvegarde ?", font=theme.font(15, "bold"), anchor="w") \
            .grid(row=0, column=0, sticky="w", padx=16, pady=(14, 8))
        self.mode = ctk.CTkSegmentedButton(how, values=[KIND_LABELS[m] for m in MODES], font=theme.font(13),
                                           height=32, command=lambda _v: self._mode_changed())
        mode = self.settings.get("backup_mode", INCREMENTAL)
        self.mode.set(KIND_LABELS.get(mode, KIND_LABELS[INCREMENTAL]))
        self.mode.grid(row=1, column=0, sticky="w", padx=16)
        self.mode_help = ctk.CTkLabel(how, text="", font=theme.font(12), text_color=theme.MUTED, anchor="w",
                                      justify="left", wraplength=760)
        self.mode_help.grid(row=2, column=0, sticky="w", padx=16, pady=(6, 6))
        keep_line = ctk.CTkFrame(how, fg_color="transparent")
        keep_line.grid(row=3, column=0, sticky="w", padx=16, pady=(0, 14))
        ctk.CTkLabel(keep_line, text="Sauvegardes complètes à conserver :", font=theme.font(12)).pack(side="left")
        keep = int(self.settings.get("backup_keep_full", 0) or 0)
        self.keep = ctk.CTkOptionMenu(keep_line, values=list(KEEP_CHOICES), width=150, height=30,
                                      command=lambda _v: self._save_prefs())
        self.keep.set(next((k for k, v in KEEP_CHOICES.items() if v == keep), "Tout garder"))
        self.keep.pack(side="left", padx=8)
        ctk.CTkLabel(keep_line, text="(les plus anciennes et celles qui en dépendent sont supprimées "
                                     "automatiquement)", font=theme.font(11), text_color=theme.MUTED) \
            .pack(side="left")
        self._mode_changed(save=False)

        self._build_auto_card(tab).grid(row=4, column=0, sticky="ew", pady=(0, 10))

        actions = ctk.CTkFrame(tab, fg_color="transparent")
        actions.grid(row=5, column=0, sticky="ew", pady=(4, 6))
        self.start_btn = primary_button(actions, "Lancer la sauvegarde", self.start_backup, width=200)
        self.start_btn.pack(side="left")
        ctk.CTkLabel(actions, text="Rien n'est copié avant votre confirmation.", font=theme.font(12),
                     text_color=theme.MUTED).pack(side="left", padx=12)
        return tab

    # ---------------------------------------------------------------- dossiers
    def _source_rows(self) -> list[tuple[str, str, bool]]:
        """(libellé, chemin, personnalisé)"""
        rows = [(label, path, False) for label, path in backup.default_sources()]
        known = {os.path.normcase(p) for _l, p, _c in rows}
        rows += [(os.path.basename(p.rstrip("\\/")) or p, p, True) for p in self.custom
                 if os.path.normcase(p) not in known]
        return rows

    def _build_sources(self) -> None:
        for child in self.sources_box.winfo_children():
            child.destroy()
        saved = self.settings.get("backup_sources", None)
        selected = None if saved is None else {os.path.normcase(p) for p in saved}
        old_vars = self.source_vars
        self.source_vars = {}
        self.size_labels = {}
        for row, (label, path, custom) in enumerate(self._source_rows()):
            if path in old_vars:
                value = old_vars[path].get()
            elif selected is None:
                value = label in ("Documents", "Bureau", "Images") or custom
            else:
                value = os.path.normcase(path) in selected
            var = ctk.BooleanVar(value=value)
            self.source_vars[path] = var
            line = ctk.CTkFrame(self.sources_box, fg_color="transparent")
            line.grid(row=row, column=0, sticky="ew", pady=1)
            line.grid_columnconfigure(1, weight=1)
            ctk.CTkCheckBox(line, text=label, variable=var, width=170, font=theme.font(13), checkbox_width=20,
                            checkbox_height=20, command=self._sources_changed).grid(row=0, column=0, sticky="w",
                                                                                    padx=(4, 8))
            ctk.CTkLabel(line, text=path, font=theme.font(11), text_color=theme.MUTED, anchor="w") \
                .grid(row=0, column=1, sticky="w")
            size = ctk.CTkLabel(line, text=self._size_text(path), font=theme.font(12), width=150, anchor="e")
            size.grid(row=0, column=2, sticky="e", padx=(8, 4))
            self.size_labels[path] = size
            if custom:
                ctk.CTkButton(line, text="✕", width=28, height=26, fg_color="transparent",
                              hover_color=theme.NEUTRAL_BTN, text_color=theme.MUTED,
                              command=lambda p=path: self._remove_folder(p)).grid(row=0, column=3, padx=(2, 0))
            else:
                ctk.CTkLabel(line, text="", width=30).grid(row=0, column=3)
        self._update_total()

    def _selected_sources(self) -> list[str]:
        return [p for p, var in self.source_vars.items() if var.get()]

    def _sources_changed(self) -> None:
        self._save_prefs()
        self._update_total()
        self._schedule_check()

    def _add_folder(self) -> None:
        folder = filedialog.askdirectory(parent=self.winfo_toplevel(), title="Dossier à sauvegarder",
                                         initialdir=str(Path.home()))
        if not folder:
            return
        folder = os.path.normpath(folder)
        if any(backup.same_path(folder, p) for p in self.source_vars):
            self.source_vars[next(p for p in self.source_vars if backup.same_path(folder, p))].set(True)
        else:
            self.custom.append(folder)
            self.settings.set("backup_custom", self.custom)
            self._build_sources()
            self.source_vars[folder].set(True)
            self._compute_sizes([folder])
        self._sources_changed()

    def _remove_folder(self, path: str) -> None:
        self.custom = [p for p in self.custom if not backup.same_path(p, path)]
        self.settings.set("backup_custom", self.custom)
        self.source_vars.pop(path, None)
        self._build_sources()
        self._sources_changed()

    def _compute_sizes(self, paths: list[str] | None = None) -> None:
        paths = paths or list(self.source_vars)
        report = self.dispatcher.wrap(self._size_done)

        def work() -> None:
            for path in paths:
                try:
                    report(path, backup.folder_size(path))
                except Exception:  # noqa: BLE001 - un dossier illisible ne doit pas tout arrêter
                    report(path, None)

        threading.Thread(target=work, daemon=True).start()

    def _size_text(self, path: str) -> str:
        if path not in self.sizes:
            return "…"
        result = self.sizes[path]
        if result is None:
            return "illisible"
        return f"{human_size(result[1])}  ({_count(result[0])} fich.)"

    def _size_done(self, path: str, result: tuple[int, int] | None) -> None:
        self.sizes[path] = result
        label = self.size_labels.get(path)
        if label is not None and label.winfo_exists():
            label.configure(text=self._size_text(path))
        self._update_total()

    def _update_total(self) -> None:
        selected = self._selected_sources()
        if not selected:
            self.total_label.configure(text="Aucun dossier sélectionné", text_color=theme.WARNING)
        elif any(p not in self.sizes for p in selected):
            self.total_label.configure(text="Calcul de la taille…", text_color=theme.MUTED)
        else:
            total = sum(self.sizes[p][1] for p in selected if self.sizes[p])
            self.total_label.configure(text=f"Total : {human_size(total)}", text_color=theme.NEUTRAL_TEXT)

    # ---------------------------------------------------------------- destination
    def _refresh_drives(self) -> None:
        def work() -> list[tuple[str, str]]:
            system = os.path.splitdrive(os.environ.get("SystemRoot", "C:\\Windows"))[0].upper()
            found = []
            for part in psutil.disk_partitions(all=False):
                opts = part.opts.lower()
                root = part.mountpoint
                if "cdrom" in opts or not part.fstype or root[:2].upper() == system:
                    continue
                try:
                    free = psutil.disk_usage(root).free
                except OSError:
                    continue
                label, _fs = backup.volume_info(root)
                kind = "USB" if "removable" in opts else "réseau" if "remote" in opts else "disque"
                text = f"{root.rstrip(chr(92))}  {label or kind}  —  {human_size(free)} libres"
                found.append((root, text))
            return found

        self.dispatcher.run(work, on_done=self._show_drives)

    def _show_drives(self, drives: list[tuple[str, str]]) -> None:
        for child in self.drives_box.winfo_children():
            child.destroy()
        if not drives:
            ctk.CTkLabel(self.drives_box, text="Aucun disque externe détecté : branchez un disque USB, ou indiquez "
                                               "un dossier réseau ci-dessous.",
                         font=theme.font(12), text_color=theme.WARNING, anchor="w").pack(side="left")
        for root, text in drives:
            ctk.CTkButton(self.drives_box, text=text, height=30, width=10, font=theme.font(12),
                          fg_color=theme.NEUTRAL_BTN, hover_color=theme.NEUTRAL_BTN_HOVER,
                          text_color=theme.NEUTRAL_TEXT,
                          command=lambda r=root: self.destination.set(r)).pack(side="left", padx=(0, 6))
        ctk.CTkButton(self.drives_box, text="↻", width=30, height=30, fg_color="transparent",
                      hover_color=theme.NEUTRAL_BTN, text_color=theme.MUTED,
                      command=self._refresh_drives).pack(side="left")

    def _browse_destination(self) -> None:
        folder = filedialog.askdirectory(parent=self.winfo_toplevel(), title="Où enregistrer la sauvegarde ?",
                                         initialdir=self.destination.get() or None)
        if folder:
            self.destination.set(os.path.normpath(folder))

    def _schedule_check(self) -> None:
        if self._check_after:
            self.after_cancel(self._check_after)
        self._check_after = self.after(400, self._check_destination)

    def _check_destination(self) -> None:
        self._check_after = None
        dest = self.destination.get().strip()
        sources = self._selected_sources()
        self.settings.set("backup_destination", dest)

        def done(check: DestinationCheck) -> None:
            if dest != self.destination.get().strip():
                return
            self.dest_check = check
            symbol = {"success": "✓", "warning": "⚠", "error": "✕"}.get(check.level, "")
            self.dest_label.configure(text=f"{symbol}  {check.summary}",
                                      text_color=LEVEL_COLORS.get(check.level, theme.MUTED))
            self.dest_warn.configure(text="\n".join(check.warnings))
            self._render_auto()

        self.dispatcher.run(backup.check_destination, dest, sources, on_done=done)

    # ---------------------------------------------------------------- type
    def _current_mode(self) -> str:
        label = self.mode.get()
        return next((m for m in MODES if KIND_LABELS[m] == label), INCREMENTAL)

    def _mode_changed(self, save: bool = True) -> None:
        self.mode_help.configure(text=MODE_HELP[self._current_mode()])
        if save:
            self._save_prefs()

    def _save_prefs(self) -> None:
        self.settings.set("backup_sources", self._selected_sources(), save=False)
        self.settings.set("backup_mode", self._current_mode(), save=False)
        self.settings.set("backup_keep_full", KEEP_CHOICES.get(self.keep.get(), 0))

    def _refresh_last_backup(self) -> None:
        last = backup_schedule.last_backup(self.settings.get("backup_last") or {}, backup_schedule.load_status())
        when = backup.parse_time(last.get("finished"))
        if when:
            kind = KIND_LABELS.get(last.get("kind", ""), "").lower()
            auto = " automatique" if last.get("auto") else ""
            text = (f"Dernière sauvegarde{auto} : {when:%d/%m/%Y à %H:%M} ({_ago(when)}), {kind}, sur "
                    f"{last.get('destination', '?')}.")
            if (datetime.now() - when).days >= 7:
                text += " Pensez à en refaire une : au moins une fois par semaine est conseillé"
                text += "." if self.settings.get("backup_auto", False) else \
                    " (ou activez la sauvegarde automatique, étape 4)."
            self.last_tip.set_text(text)
        else:
            self.last_tip.set_text("Aucune sauvegarde pour l'instant. Branchez un disque externe, choisissez vos "
                                   "dossiers, puis lancez une première sauvegarde (elle sera complète).")

    # ---------------------------------------------------------------- sauvegarde automatique
    def _build_auto_card(self, tab) -> Card:
        card = Card(tab)
        card.grid_columnconfigure(0, weight=1)
        head = ctk.CTkFrame(card, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", padx=16, pady=(14, 2))
        ctk.CTkLabel(head, text="4.  Sauvegarde automatique", font=theme.font(15, "bold"), anchor="w") \
            .pack(side="left")
        self.auto_var = ctk.BooleanVar(value=bool(self.settings.get("backup_auto", False)))
        self.auto_switch = ctk.CTkSwitch(head, text="Activer", variable=self.auto_var, font=theme.font(13, "bold"),
                                         command=self._toggle_auto)
        self.auto_switch.pack(side="right")
        ctk.CTkLabel(card, text="L'application sauvegarde vos dossiers toute seule, en arrière-plan, même quand elle "
                                "est fermée, avec les dossiers et la destination choisis ci-dessus. Si le PC est "
                                "éteint ou le disque débranché à l'heure prévue, la sauvegarde se fait dès que "
                                "possible (vérification toutes les heures).",
                     font=theme.font(12), text_color=theme.MUTED, anchor="w", justify="left", wraplength=760) \
            .grid(row=1, column=0, sticky="w", padx=16, pady=(0, 8))

        opts = ctk.CTkFrame(card, fg_color="transparent")
        opts.grid(row=2, column=0, sticky="w", padx=16, pady=(0, 6))
        ctk.CTkLabel(opts, text="Fréquence :", font=theme.font(12)).pack(side="left")
        self.auto_freq = ctk.CTkOptionMenu(opts, values=list(FREQUENCIES), width=180, height=30,
                                           command=lambda _v: self._save_auto_prefs())
        hours = int(self.settings.get("backup_auto_hours", backup_schedule.DEFAULT_HOURS) or 24)
        self.auto_freq.set(backup_schedule.frequency_label(hours))
        self.auto_freq.pack(side="left", padx=(8, 20))
        ctk.CTkLabel(opts, text="Type :", font=theme.font(12)).pack(side="left")
        self.auto_mode = ctk.CTkSegmentedButton(opts, values=[KIND_LABELS[m] for m in MODES], font=theme.font(12),
                                                height=30, command=lambda _v: self._save_auto_prefs())
        self.auto_mode.set(KIND_LABELS.get(self.settings.get("backup_auto_mode", INCREMENTAL),
                                           KIND_LABELS[INCREMENTAL]))
        self.auto_mode.pack(side="left", padx=8)

        full_line = ctk.CTkFrame(card, fg_color="transparent")
        full_line.grid(row=3, column=0, sticky="w", padx=16, pady=(0, 4))
        ctk.CTkLabel(full_line, text="Faire aussi une sauvegarde complète :", font=theme.font(12)).pack(side="left")
        self.auto_full = ctk.CTkOptionMenu(full_line, values=list(FULL_EVERY), width=190, height=30,
                                           command=lambda _v: self._save_auto_prefs())
        weeks = int(self.settings.get("backup_auto_full_weeks", 0) or 0)
        self.auto_full.set(next((k for k, v in FULL_EVERY.items() if v == weeks), "Jamais"))
        self.auto_full.pack(side="left", padx=8)
        ctk.CTkLabel(card, text="Une complète régulière repart d'une base neuve ; les plus anciennes sont supprimées "
                                "selon « Sauvegardes complètes à conserver » (étape 3).",
                     font=theme.font(11), text_color=theme.MUTED, anchor="w", justify="left", wraplength=760) \
            .grid(row=4, column=0, sticky="w", padx=16, pady=(0, 2))

        self.auto_state = ctk.CTkLabel(card, text="", font=theme.font(12, "bold"), anchor="w", justify="left",
                                       wraplength=760)
        self.auto_state.grid(row=5, column=0, sticky="w", padx=16, pady=(6, 0))
        self.auto_detail = ctk.CTkLabel(card, text="", font=theme.font(12), text_color=theme.MUTED, anchor="w",
                                        justify="left", wraplength=760)
        self.auto_detail.grid(row=6, column=0, sticky="w", padx=16, pady=(0, 4))
        self.auto_actions = ctk.CTkFrame(card, fg_color="transparent", height=4)
        self.auto_actions.grid(row=7, column=0, sticky="w", padx=16, pady=(0, 12))
        self.auto_repair_btn = neutral_button(self.auto_actions, "Réparer la tâche planifiée", self._repair_auto,
                                              width=10)
        self.auto_admin_btn = neutral_button(self.auto_actions, "Relancer en administrateur",
                                             self.app.restart_as_admin, width=10)
        self._update_full_state()
        self._render_auto()
        return card

    def _auto_mode(self) -> str:
        label = self.auto_mode.get()
        return next((m for m in MODES if KIND_LABELS[m] == label), INCREMENTAL)

    def _update_full_state(self) -> None:
        state = "disabled" if self._auto_mode() == FULL else "normal"
        self.auto_full.configure(state=state)

    def _save_auto_prefs(self) -> None:
        """La tâche relit les réglages à chaque exécution : rien à recréer quand ils changent."""
        self.settings.set("backup_auto_hours", FREQUENCIES.get(self.auto_freq.get(), 24), save=False)
        self.settings.set("backup_auto_mode", self._auto_mode(), save=False)
        self.settings.set("backup_auto_full_weeks", FULL_EVERY.get(self.auto_full.get(), 0))
        self._update_full_state()
        self._render_auto()

    def _toggle_auto(self) -> None:
        enable = bool(self.auto_var.get())
        if self._auto_busy:
            self.auto_var.set(not enable)
            return
        if not enable:
            self.settings.set("backup_auto", False)
            self._run_task_op(enable=False)
            return
        self.auto_var.set(False)       # cochée seulement une fois la tâche créée
        if not self.is_admin:
            if ask_yes_no(self, "Droits administrateur requis",
                          "Pour planifier la sauvegarde automatique, l'application doit être lancée en "
                          "administrateur (une seule fois : la sauvegarde se fera ensuite toute seule, sans rien "
                          "vous demander).\n\nRelancer l'application en administrateur ?"):
                self.app.restart_as_admin()
            return
        if not self._selected_sources():
            show_warning(self, "Aucun dossier", "Cochez d'abord au moins un dossier à sauvegarder (étape 1).")
            return
        dest = self.destination.get().strip()
        if not dest:
            show_warning(self, "Destination", "Choisissez d'abord où enregistrer la sauvegarde (étape 2).")
            return
        unc = backup_schedule.network_drive_target(dest)
        if unc and ask_yes_no(self, "Lecteur réseau",
                              f"{dest} est une lettre de lecteur réseau. La sauvegarde automatique ne voit pas ces "
                              "lettres (elles n'existent que dans votre session) : il vaut mieux utiliser le chemin "
                              f"réseau complet :\n\n{unc}\n\nUtiliser ce chemin ?"):
            self.destination.set(unc)
        if self.dest_check is not None and self.dest_check.same_disk and not ask_yes_no(
                self, "Même disque",
                self.dest_check.warnings[0] + "\n\nActiver quand même la sauvegarde automatique (protège seulement "
                                              "contre une suppression accidentelle) ?"):
            return
        self._save_prefs()
        self.settings.set("backup_destination", self.destination.get().strip(), save=False)
        self._save_auto_prefs()
        self._run_task_op(enable=True)

    def _repair_auto(self) -> None:
        if not self.is_admin:
            self.app.restart_as_admin()
            return
        self._run_task_op(enable=True)

    def _run_task_op(self, enable: bool) -> None:
        self._auto_busy = True
        self.auto_switch.configure(state="disabled")
        self.auto_state.configure(text="Création de la tâche planifiée…" if enable
                                  else "Arrêt de la sauvegarde automatique…", text_color=theme.MUTED)

        def work():
            if enable:
                result = backup_schedule.create_task()
            elif self._task is not None or not self._task_checked:
                result = backup_schedule.delete_task()
            else:
                result = None
            return result, backup_schedule.query_task()

        def done(out) -> None:
            result, task = out
            self._task, self._task_checked = task, True
            if enable:
                if result is not None and result.ok and task is not None:
                    self.settings.set("backup_auto", True)
                    self.app.notify("Sauvegarde automatique activée",
                                    f"{self.auto_freq.get()} — première vérification dans quelques minutes.",
                                    "success")
                else:
                    self.settings.set("backup_auto", False)
                    detail = (result.output if result is not None else "") or "erreur inconnue"
                    show_error(self, "Sauvegarde automatique",
                               f"La tâche planifiée n'a pas pu être créée.\n\nDétail : {detail[:500]}")
            self._render_auto()

        def error(exc: BaseException) -> None:
            if enable:
                self.settings.set("backup_auto", False)
            show_error(self, "Sauvegarde automatique", f"Opération impossible : {exc}")
            self._render_auto()

        def finished() -> None:
            self._auto_busy = False
            self.auto_switch.configure(state="normal")
            self._render_auto()

        self.dispatcher.run(work, on_done=done, on_error=error, on_finally=finished)

    def _refresh_auto(self, query: bool = False) -> None:
        if query and not self._auto_busy:
            def done(task: TaskInfo | None) -> None:
                self._task, self._task_checked = task, True
                self._render_auto()

            self.dispatcher.run(backup_schedule.query_task, on_done=done, on_error=lambda _e: None)
        self._render_auto()

    def _render_auto(self) -> None:
        if self._auto_busy:
            return
        enabled = bool(self.settings.get("backup_auto", False))
        self.auto_var.set(enabled)
        for btn in (self.auto_repair_btn, self.auto_admin_btn):
            btn.pack_forget()
        now = datetime.now()
        if not enabled:
            self.auto_state.configure(text="Désactivée.", text_color=theme.MUTED)
            detail = ("Activez-la pour ne plus avoir à y penser : idéal si le disque de sauvegarde reste branché ou "
                      "si vous le branchez régulièrement.")
            if not self.is_admin:
                detail += " Il faut lancer l'application en administrateur pour l'activer."
                self.auto_admin_btn.pack(side="left")
            if self._task is not None:
                detail += (" La tâche planifiée est encore inscrite dans Windows, mais elle ne fait plus rien "
                           "(relancez l'application en administrateur pour la retirer).")
            self.auto_detail.configure(text=detail)
            return

        cfg = backup_schedule.AutoConfig.from_settings(self.settings)
        status = backup_schedule.load_status()
        command, arguments, _wd = backup_schedule.task_command()
        repair = False
        fix = ("cliquez sur « Réparer »." if self.is_admin
               else "relancez l'application en administrateur pour la réparer.")
        if self._task_checked and self._task is None:
            state, color, repair = (f"⚠ Activée, mais la tâche planifiée est introuvable dans Windows : {fix}",
                                    theme.WARNING, True)
        elif self._task is not None and not self._task.matches(command, arguments):
            state, color, repair = ("⚠ Activée, mais la tâche planifiée lance une autre copie de l'application "
                                    f"(déplacée ?) : {fix}", theme.WARNING, True)
        else:
            state = (f"✓ Activée — {backup_schedule.frequency_label(cfg.hours).lower()}, "
                     f"sauvegarde {KIND_LABELS[cfg.mode].lower()}")
            if cfg.full_weeks and cfg.mode != FULL:
                full = next((k for k, v in FULL_EVERY.items() if v == cfg.full_weeks), "")
                state += f", et une complète {full.lower()}"
            state, color = state + ".", theme.SUCCESS
        if repair:
            (self.auto_repair_btn if self.is_admin else self.auto_admin_btn).pack(side="left")

        lines = []
        last = backup.parse_time(status.get("last_success"))
        if last:
            line = (f"Dernière sauvegarde automatique : {last:%d/%m/%Y à %H:%M} ({_ago(last)}), "
                    f"{KIND_LABELS.get(status.get('last_kind', ''), '').lower()}, "
                    f"{_count(int(status.get('copied', 0) or 0))} fichier(s) copié(s)")
            errors = int(status.get("errors", 0) or 0)
            lines.append(line + (f", {_count(errors)} non copié(s)." if errors else "."))
        else:
            lines.append("Aucune sauvegarde automatique pour l'instant.")
        present = bool(cfg.destination) and os.path.isdir(cfg.destination)
        missing = backup.parse_time(status.get("disk_missing_since"))
        if status.get("result") == backup_schedule.ERROR and status.get("message"):
            attempt = backup.parse_time(status.get("last_attempt"))
            when = f" le {attempt:%d/%m à %H:%M}" if attempt else ""
            lines.append(f"✕ Échec{when} : {status['message']}")
            if not repair:
                color = theme.DANGER
        if missing and not present:
            lines.append(f"⚠ Disque de sauvegarde absent depuis {backup_schedule.days_text(now - missing)}.")
            if not repair and color == theme.SUCCESS:
                color = theme.WARNING
        start = backup.parse_time(status.get("last_success_started"))
        if not present:
            lines.append(f"Prochaine : dès que {cfg.destination or 'la destination'} sera disponible "
                         "(disque branché).")
        elif backup_schedule.is_due(start, cfg.hours, now):
            lines.append("Prochaine : dans moins d'une heure.")
        else:
            due = backup_schedule.next_due(start, cfg.hours)
            day = "" if due.date() == now.date() else f"le {due:%d/%m} "
            lines.append(f"Prochaine : {day}vers {due:%H:%M}.")
        self.auto_state.configure(text=state, text_color=color)
        self.auto_detail.configure(text="\n".join(lines))

    # ---------------------------------------------------------------- lancement
    def start_backup(self) -> None:
        if self.busy:
            return
        sources = self._selected_sources()
        dest = self.destination.get().strip()
        if not sources:
            show_warning(self, "Aucun dossier", "Cochez au moins un dossier à sauvegarder.")
            return
        check = backup.check_destination(dest, sources)
        if not check.ok:
            show_warning(self, "Destination", check.summary)
            return
        if check.same_disk and not ask_yes_no(
                self, "Même disque",
                check.warnings[0] + "\n\nContinuer quand même (protège seulement contre une suppression "
                                    "accidentelle) ?"):
            return
        if not self._lock.acquire():
            show_info(self, "Sauvegarde en cours",
                      "Une sauvegarde automatique est en cours en arrière-plan.\n\nRéessayez dans quelques minutes.")
            return
        self._save_prefs()
        self.cancel.clear()
        self._set_busy(True)
        self.status.busy("Analyse des dossiers…")
        progress = self.dispatcher.wrap(lambda p: self.status.set_text(_progress_text(p)))
        mode, cancel = self._current_mode(), self.cancel

        repo = backup.repository_for(dest)

        def plan() -> tuple[BackupPlan, int, int]:
            # Les sauvegardes interrompues ne sont supprimées qu'après le « Oui » de l'utilisateur (leurs fichiers
            # déjà copiés restent récupérables à la main d'ici là) : on se contente de les compter.
            leftovers = backup.find_incomplete(repo)
            leftover_bytes = sum(backup.tree_size(p) for p in leftovers)
            return backup.plan_backup(sources, dest, mode, None, cancel, progress), len(leftovers), leftover_bytes

        def planned(out: tuple[BackupPlan, int, int]) -> None:
            self._confirm_backup(out[0], check, out[1], out[2])

        self.dispatcher.run(plan, on_done=planned,
                            on_error=self._backup_error, on_finally=self._after_plan)

    def _after_plan(self) -> None:
        # Le bouton reste bloqué si la copie a démarré (elle gère elle-même la fin).
        if not self._copying:
            self._lock.release()
            self._set_busy(False)

    def _confirm_backup(self, plan: BackupPlan, check: DestinationCheck, leftovers: int = 0,
                        leftover_bytes: int = 0) -> None:
        self.status.idle("")
        lines = [f"Type : {KIND_LABELS[plan.kind]}"]
        lines += [f"  {note}" for note in plan.notes]
        if plan.reference:
            lines.append(f"Comparée à : sauvegarde du {plan.reference.created:%d/%m/%Y %H:%M}")
        lines.append(f"Fichiers à copier : {_count(len(plan.to_copy))} ({human_size(plan.copy_bytes)})")
        lines.append(f"Fichiers dans les dossiers : {_count(plan.file_count)} ({human_size(plan.total_bytes)})")
        try:
            free = shutil.disk_usage(plan.destination).free
        except OSError:
            free = check.free
        if free is not None:
            lines.append(f"Place libre sur la destination : {human_size(free)}")
        if leftovers:
            lines.append(f"\n{leftovers} sauvegarde(s) interrompue(s) ({human_size(leftover_bytes)}) seront supprimées "
                         "avant la copie : elles ne peuvent pas être restaurées. Répondez Non pour les garder "
                         "(leurs fichiers déjà copiés restent récupérables à la main).")
            if free is not None:
                free += leftover_bytes      # place rendue par cette suppression
        if plan.cloud_skipped:
            lines.append(f"\n{_count(plan.cloud_skipped)} fichier(s) OneDrive « en ligne uniquement » ignorés "
                         "(ils sont déjà dans le cloud).")
        if plan.too_big_for_fat32:
            lines.append(f"\n{len(plan.too_big_for_fat32)} fichier(s) de plus de 4 Go ne peuvent pas aller sur ce "
                         "disque FAT32 et seront ignorés.")
        if free is not None and plan.copy_bytes > free:
            show_error(self, "Place insuffisante",
                       "\n".join(lines) + "\n\nLa destination n'a pas assez de place. Libérez de l'espace, "
                                          "choisissez un autre disque ou réduisez « Sauvegardes à conserver ».")
            return
        if not plan.to_copy:
            lines.append("\nAucun fichier n'a changé : la sauvegarde sera très rapide (seul l'inventaire est "
                         "enregistré).")
        if not ask_yes_no(self, "Lancer la sauvegarde ?", "\n".join(lines) + "\n\nLancer la sauvegarde ?"):
            self.status.set_text("Sauvegarde annulée.")
            return
        self._copying = True
        self._set_busy(True)
        self.status.progress(0, "Copie des fichiers…")
        progress = self.dispatcher.wrap(lambda p: self.status.progress(p.fraction, _progress_text(p)))
        keep = KEEP_CHOICES.get(self.keep.get(), 0)
        cancel = self.cancel

        def run() -> BackupResult:
            if leftovers:
                backup.remove_incomplete(plan.repo)     # accepté par l'utilisateur dans la question ci-dessus
            return backup.run_backup(plan, cancel, progress, keep)

        self.dispatcher.run(run, on_done=lambda r: self._backup_done(plan, r), on_error=self._backup_error,
                            on_finally=self._after_copy)

    def _after_copy(self) -> None:
        self._copying = False
        self._lock.release()
        self._set_busy(False)

    def _backup_error(self, exc: BaseException) -> None:
        if isinstance(exc, backup.Cancelled):
            self.status.idle("Opération arrêtée.")
            return
        self.status.idle(f"Erreur : {exc}")
        self.app.notify("Sauvegarde impossible", str(exc), "error")

    def _backup_done(self, plan: BackupPlan, result: BackupResult) -> None:
        if result.cancelled:
            reason = ("Le disque de sauvegarde est plein." if result.disk_full
                      else "Sauvegarde arrêtée à votre demande.")
            cleaned = ("La copie partielle a été supprimée." if result.path is None else
                       "La copie partielle sera supprimée au début de la prochaine sauvegarde.")
            self.status.idle(f"{reason} {cleaned}")
            self.app.notify("Sauvegarde interrompue", reason, "warning")
            self.repo = None
            return
        self.settings.set("backup_last", {"finished": datetime.now().isoformat(timespec="seconds"),
                                          "kind": result.kind, "destination": plan.destination,
                                          "set": result.set_id})
        self._refresh_last_backup()
        self.repo = None  # l'onglet Restaurer se rechargera
        msg = (f"Sauvegarde {KIND_LABELS[result.kind].lower()} terminée : {_count(result.copied)} fichier(s) "
               f"copié(s), {human_size(result.copied_bytes)}.")
        level = "warning" if result.errors else "success"
        self.status.idle(msg + (f" {len(result.errors)} fichier(s) non copié(s)." if result.errors else ""))
        self.app.notify("Sauvegarde terminée", msg, level)
        stats = [("Type", KIND_LABELS[result.kind]),
                 ("Fichiers copiés", f"{_count(result.copied)} ({human_size(result.copied_bytes)})"),
                 ("Fichiers protégés au total", f"{_count(result.file_count)} ({human_size(result.total_bytes)})"),
                 ("Durée", _duration(result.duration))]
        if result.errors:
            stats.append(("Non copiés", _count(len(result.errors))))
        tips = []
        if result.errors:
            sample = "\n".join(f"– {os.path.basename(p)} : {why}" for p, why in result.errors[:4])
            tips.append("Certains fichiers n'ont pas pu être copiés (souvent parce qu'un programme les utilise, "
                        "comme Outlook). Fermez ces programmes et relancez une sauvegarde :\n" + sample)
        if result.pruned:
            tips.append(f"{len(result.pruned)} ancienne(s) sauvegarde(s) supprimée(s) selon votre réglage.")
        tips.append("Débranchez le disque de sauvegarde quand vous ne vous en servez pas : il est ainsi protégé "
                    "des virus (rançongiciels) et des surtensions.")
        ResultDialog(self, "Sauvegarde terminée", "Vos fichiers sont en sécurité !" if not result.errors
                     else "Sauvegarde terminée (avec des fichiers ignorés)", stats, tips,
                     level=level, actions=[
                         ("Ouvrir la sauvegarde dans l'Explorateur", lambda: open_path(result.path)),
                         ("Vérifier la sauvegarde", lambda: self._verify_from_result(result)),
                     ])

    def _verify_from_result(self, result: BackupResult) -> None:
        self._switch_tab(TABS[1])
        self.load_repository(result.path.parent, select=result.set_id, then_verify=True)

    # ================================================================== onglet Restaurer
    def _build_restore_tab(self) -> ctk.CTkFrame:
        tab = ctk.CTkFrame(self, fg_color="transparent")
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(4, weight=1)

        top = ctk.CTkFrame(tab, fg_color="transparent")
        top.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        top.grid_columnconfigure(2, weight=1)
        self.open_repo_btn = neutral_button(top, "Ouvrir une sauvegarde…", self._browse_repo, width=10)
        self.open_repo_btn.grid(row=0, column=0, sticky="w")
        neutral_button(top, "Actualiser", lambda: self.load_repository(self.repo) if self.repo else None,
                       width=90).grid(row=0, column=1, padx=8)
        self.repo_label = ctk.CTkLabel(top, text="Aucune sauvegarde ouverte.", font=theme.font(12),
                                       text_color=theme.MUTED, anchor="w", justify="left", wraplength=620)
        self.repo_label.grid(row=0, column=2, sticky="w", padx=(4, 0))

        self.sets_table = Table(tab, [
            ("date", "Date", 140, "w", False),
            ("kind", "Type", 110, "w", False),
            ("files", "Fichiers", 90, "e", False),
            ("copied", "Copié", 110, "e", False),
            ("total", "Total protégé", 130, "e", False),
            ("state", "État", 260, "w", True),
        ], selectmode="browse", on_select=self._set_selected, height=5)
        self.sets_table.grid(row=1, column=0, sticky="ew")

        buttons = ctk.CTkFrame(tab, fg_color="transparent")
        buttons.grid(row=2, column=0, sticky="ew", pady=(8, 10))
        self.restore_all_btn = primary_button(buttons, "Tout restaurer…", lambda: self._restore(None), width=150)
        self.restore_all_btn.pack(side="left")
        self.verify_btn = neutral_button(buttons, "Vérifier", self._verify, width=90)
        self.verify_btn.pack(side="left", padx=8)
        neutral_button(buttons, "Ouvrir le dossier", self._open_set, width=10).pack(side="left")
        self.delete_btn = danger_button(buttons, "Supprimer", self._delete_set, width=100)
        self.delete_btn.pack(side="right")

        search = ctk.CTkFrame(tab, fg_color="transparent")
        search.grid(row=3, column=0, sticky="ew", pady=(0, 6))
        ctk.CTkLabel(search, text="Retrouver un fichier :", font=theme.font(13)).pack(side="left")
        self.search = ctk.StringVar()
        ctk.CTkEntry(search, textvariable=self.search, height=32, placeholder_text="nom ou partie du chemin…") \
            .pack(side="left", fill="x", expand=True, padx=8)
        self.search.trace_add("write", lambda *_a: self._schedule_search())
        self.files_info = ctk.CTkLabel(search, text="", font=theme.font(12), text_color=theme.MUTED)
        self.files_info.pack(side="left")

        self.files_table = Table(tab, [
            ("name", "Fichier", 240, "w", True),
            ("folder", "Dossier", 320, "w", True),
            ("size", "Taille", 90, "e", False),
            ("date", "Modifié le", 130, "center", False),
        ], on_double_click=lambda _iid: self._open_file(), height=8)
        self.files_table.grid(row=4, column=0, sticky="nsew")

        file_buttons = ctk.CTkFrame(tab, fg_color="transparent")
        file_buttons.grid(row=5, column=0, sticky="ew", pady=(8, 0))
        self.restore_sel_btn = primary_button(file_buttons, "Restaurer la sélection…", self._restore_selection,
                                              width=190)
        self.restore_sel_btn.pack(side="left")
        neutral_button(file_buttons, "Ouvrir", self._open_file, width=80).pack(side="left", padx=8)
        neutral_button(file_buttons, "Afficher dans l'Explorateur", self._reveal_file, width=10).pack(side="left")
        ctk.CTkLabel(file_buttons, text="Double-cliquez pour ouvrir un fichier directement depuis la sauvegarde.",
                     font=theme.font(11), text_color=theme.MUTED).pack(side="right")
        return tab

    # ---------------------------------------------------------------- dossier de sauvegardes
    def _open_default_repo(self) -> None:
        dest = self.destination.get().strip()
        if dest:
            repo = backup.repository_for(dest)
            if repo.is_dir():
                self.load_repository(repo)
                return
        self.repo_label.configure(text="Branchez le disque de sauvegarde puis ouvrez-le (par exemple après une "
                                       "panne, sur un nouveau PC).")

    def _browse_repo(self) -> None:
        folder = filedialog.askdirectory(parent=self.winfo_toplevel(), title="Disque ou dossier de sauvegarde",
                                         initialdir=self.destination.get() or None)
        if not folder:
            return
        repos = backup.find_repositories(folder)
        if not repos:
            show_warning(self, "Aucune sauvegarde", "Aucune sauvegarde NPone29 n'a été trouvée dans ce dossier.\n\n"
                         f"Choisissez le disque de sauvegarde ou le dossier « {backup.REPO_DIR} ».")
            return
        if len(repos) == 1:
            self.load_repository(repos[0])
            return
        ChoiceDialog(self, "Plusieurs PC", "Ce disque contient les sauvegardes de plusieurs PC. Lequel ouvrir ?",
                     [r.name for r in repos], lambda i: self.load_repository(repos[i]))

    def load_repository(self, repo: Path | None, select: str | None = None, then_verify: bool = False) -> None:
        if repo is None:
            return
        self.repo = Path(repo)
        self.repo_label.configure(text=f"Sauvegardes de « {self.repo.name} » — {self.repo}")
        self.inventory, self.inventory_set = {}, None
        self.files_table.clear()
        self.files_info.configure(text="")

        def done(sets: list[BackupSet]) -> None:
            self.sets = sets
            self._render_sets()
            target = select or next((s.id for s in reversed(sets) if s.complete), None)
            if target and self.sets_table.tree.exists(target):
                self.sets_table.tree.selection_set([target])
                self.sets_table.tree.see(target)
                if then_verify:
                    self.after(100, self._verify)
            if not sets:
                self.status.set_text("Ce dossier ne contient encore aucune sauvegarde.")

        self.dispatcher.run(backup.list_sets, self.repo, on_done=done,
                            on_error=lambda e: self.status.idle(f"Erreur : {e}"))

    def _render_sets(self) -> None:
        by_id = {s.id: s for s in self.sets}
        rows = []
        for s in reversed(self.sets):
            if not s.complete:
                state, tags = "Interrompue : ne peut pas être restaurée", ("error",)
            elif not backup.chain_ok(by_id, s):
                state, tags = "Une sauvegarde nécessaire a été supprimée", ("error",)
            else:
                needs = len(s.requires)
                state = "✓ Restaurable" + (f" (+ {needs} précédente{'s' if needs > 1 else ''})" if needs else "")
                tags = ("warn",) if s.error_count else ()
                if s.error_count:
                    state += f" — {s.error_count} fichier(s) non copié(s)"
            rows.append((s.id, [f"{s.created:%d/%m/%Y %H:%M}", s.label,
                                _count(s.file_count) if s.complete else "—",
                                human_size(s.copied_bytes) if s.complete else "—",
                                human_size(s.total_bytes) if s.complete else "—", state],
                         [s.created, s.label, s.file_count, s.copied_bytes, s.total_bytes, state], tags))
        self.sets_table.set_rows(rows)

    def _selected_set(self) -> BackupSet | None:
        ids = self.sets_table.selected()
        return next((s for s in self.sets if ids and s.id == ids[0]), None)

    def _set_selected(self) -> None:
        backup_set = self._selected_set()
        if backup_set is None or backup_set is self.inventory_set:
            return
        self.inventory, self.inventory_set = {}, backup_set
        self.files_table.clear()
        if not backup_set.complete:
            self.files_info.configure(text="Sauvegarde interrompue.")
            return
        self.files_info.configure(text="Chargement…")

        def done(inventory: dict[str, list]) -> None:
            if self.inventory_set is not backup_set:
                return
            self.inventory = inventory
            self._file_keys = sorted(inventory, key=str.lower)
            self._apply_search()

        self.dispatcher.run(backup_set.inventory, on_done=done,
                            on_error=lambda e: self.files_info.configure(text=f"Inventaire illisible : {e}"))

    # ---------------------------------------------------------------- fichiers
    def _schedule_search(self) -> None:
        if self._search_after:
            self.after_cancel(self._search_after)
        self._search_after = self.after(250, self._apply_search)

    def _apply_search(self) -> None:
        self._search_after = None
        terms = [t for t in self.search.get().lower().replace("\\", "/").split() if t]
        matches = [k for k in self._file_keys if all(t in k.lower() for t in terms)]
        shown = matches[:MAX_FILE_ROWS]
        rows = []
        for key in shown:
            size, mtime, _holder = self.inventory[key]
            folder, _sep, name = key.rpartition("/")
            when = datetime.fromtimestamp(mtime / 1e9)
            rows.append((key, [name, folder.replace("/", "\\"), human_size(size), f"{when:%d/%m/%Y %H:%M}"],
                         [name, folder, size, when], ()))
        self.files_table.set_rows(rows)
        info = f"{_count(len(matches))} fichier(s)"
        if len(matches) > len(shown):
            info += f", {len(shown)} affichés : précisez la recherche"
        self.files_info.configure(text=info)

    def _backup_copy(self, key: str) -> Path | None:
        if self.repo is None or key not in self.inventory:
            return None
        return self.repo / self.inventory[key][2] / key

    def _open_file(self) -> None:
        keys = self.files_table.selected()
        path = self._backup_copy(keys[0]) if keys else None
        if path is None:
            return
        try:
            open_path(path if len(str(path)) < 250 else backup.long_path(path))
        except OSError as exc:
            show_error(self, "Impossible d'ouvrir", f"{path}\n\n{exc}")

    def _reveal_file(self) -> None:
        keys = self.files_table.selected()
        path = self._backup_copy(keys[0]) if keys else None
        if path is not None:
            reveal_in_explorer(path)

    # ---------------------------------------------------------------- actions
    def _open_set(self) -> None:
        backup_set = self._selected_set()
        if backup_set:
            open_path(backup_set.path)
        elif self.repo:
            open_path(self.repo)

    def _restore_selection(self) -> None:
        keys = self.files_table.selected()
        if not keys:
            self.status.set_text("Sélectionnez un ou plusieurs fichiers dans la liste (Ctrl+clic, Maj+clic).")
            return
        self._restore(keys)

    def _restore(self, keys: list[str] | None) -> None:
        backup_set = self._selected_set()
        if self.busy or backup_set is None or self.repo is None:
            if backup_set is None:
                self.status.set_text("Choisissez d'abord une sauvegarde dans la liste.")
            return
        if not backup.chain_ok({s.id: s for s in self.sets}, backup_set):
            show_warning(self, "Restauration impossible",
                         "Cette sauvegarde est interrompue ou une sauvegarde dont elle dépend a été supprimée. "
                         "Choisissez-en une autre.")
            return
        count = len(keys) if keys is not None else backup_set.file_count
        RestoreDialog(self, backup_set, count, lambda mode, folder, overwrite:
                      self._run_restore(backup_set, keys, mode, folder, overwrite))

    def _take_lock(self) -> bool:
        """Verrou des sauvegardes, pour restaurer, vérifier ou supprimer : la sauvegarde automatique ne doit pas
        écrire dans (ou supprimer) une sauvegarde utilisée au même moment."""
        if self._lock.acquire():
            return True
        show_info(self, "Sauvegarde en cours",
                  "Une sauvegarde automatique est en cours en arrière-plan.\n\nRéessayez dans quelques minutes.")
        return False

    def _release_lock(self) -> None:
        self._lock.release()
        self._set_busy(False)

    def _run_restore(self, backup_set: BackupSet, keys: list[str] | None, mode: str, folder: str,
                     overwrite: bool) -> None:
        if self.busy or not self._take_lock():
            return
        self.cancel.clear()
        self._set_busy(True)
        self.status.progress(0, "Restauration…")
        progress = self.dispatcher.wrap(lambda p: self.status.progress(p.fraction, _progress_text(p)))
        repo, cancel = self.repo, self.cancel

        def work() -> RestoreResult:
            with backup.RepoLock(repo):     # aucune sauvegarde (autre compte) ne supprime ce qu'on restaure
                return backup.restore(repo, backup_set.id, mode, folder, overwrite, keys, cancel, progress)

        self.dispatcher.run(work, on_done=lambda r: self._restore_done(r, mode, folder),
                            on_error=self._backup_error, on_finally=self._release_lock)

    def _restore_done(self, result: RestoreResult, mode: str, folder: str) -> None:
        problems = len(result.errors) + len(result.missing)
        msg = f"Restauration terminée : {_count(result.restored)} fichier(s), {human_size(result.restored_bytes)}."
        if result.cancelled:
            msg = "Restauration arrêtée. " + msg
        self.status.idle(msg)
        level = "warning" if problems or result.cancelled else "success"
        self.app.notify("Restauration terminée", msg, level)
        stats = [("Fichiers restaurés", f"{_count(result.restored)} ({human_size(result.restored_bytes)})")]
        if result.identical:
            stats.append(("Déjà présents et identiques", _count(result.identical)))
        if result.kept:
            stats.append(("Existants conservés (non écrasés)", _count(result.kept)))
        if problems:
            stats.append(("Problèmes", _count(problems)))
        tips = []
        if result.kept:
            tips.append("Des fichiers existaient déjà avec un contenu différent : ils n'ont pas été remplacés. "
                        "Relancez en cochant « Remplacer les fichiers existants » si vous voulez la version "
                        "de la sauvegarde.")
        if result.missing:
            tips.append(f"{len(result.missing)} fichier(s) sont absents du disque de sauvegarde (supprimés à la "
                        "main ?). Lancez « Vérifier » pour la liste complète.")
        for path, why in result.errors[:4]:
            tips.append(f"{os.path.basename(path)} : {why}")
        actions = []
        if mode == FOLDER:
            actions.append(("Ouvrir les fichiers restaurés", lambda: open_path(folder)))
        ResultDialog(self, "Restauration", "Fichiers restaurés !" if level == "success" else msg, stats, tips,
                     level=level, actions=actions)

    def _verify(self) -> None:
        backup_set = self._selected_set()
        if self.busy or backup_set is None or self.repo is None:
            return
        if not backup_set.complete:
            show_warning(self, "Sauvegarde interrompue", "Cette sauvegarde est incomplète : supprimez-la.")
            return
        if not self._take_lock():
            return
        self.cancel.clear()
        self._set_busy(True)
        self.status.progress(0, "Vérification…")
        progress = self.dispatcher.wrap(lambda p: self.status.progress(p.fraction, _progress_text(p)))
        repo, cancel = self.repo, self.cancel

        def work() -> VerifyResult:
            with backup.RepoLock(repo):
                return backup.verify(repo, backup_set.id, cancel, progress)

        self.dispatcher.run(work, on_done=lambda r: self._verify_done(backup_set, r), on_error=self._backup_error,
                            on_finally=self._release_lock)

    def _verify_done(self, backup_set: BackupSet, result: VerifyResult) -> None:
        if result.cancelled:
            self.status.idle("Vérification arrêtée.")
            return
        if result.ok:
            self.status.idle(f"✓ Sauvegarde du {backup_set.created:%d/%m/%Y %H:%M} vérifiée : "
                             f"{_count(result.checked)} fichiers présents et complets.")
            show_info(self, "Vérification", f"Tout est en ordre : les {_count(result.checked)} fichiers de cette "
                                            "sauvegarde sont présents et complets.")
            return
        bad = result.missing + result.damaged
        self.status.idle(f"⚠ {len(bad)} fichier(s) manquant(s) ou abîmé(s).")
        sample = "\n".join(f"• {p}" for p in bad[:12]) + ("\n• …" if len(bad) > 12 else "")
        show_warning(self, "Problèmes détectés",
                     f"{len(result.missing)} fichier(s) manquant(s) et {len(result.damaged)} abîmé(s) :\n\n{sample}"
                     "\n\nLancez une nouvelle sauvegarde complète pour repartir sur une base saine.")

    def _delete_set(self) -> None:
        backup_set = self._selected_set()
        if self.busy or backup_set is None or self.repo is None:
            return
        doomed = [backup_set]
        pending = [backup_set.id]
        while pending:
            current = pending.pop()
            for dep in backup.dependents(self.sets, current):
                if dep not in doomed:
                    doomed.append(dep)
                    pending.append(dep.id)
        message = f"Supprimer définitivement la sauvegarde du {backup_set.created:%d/%m/%Y %H:%M} " \
                  f"({backup_set.label.lower()}) ?"
        if len(doomed) > 1:
            others = "\n".join(f"• {s.created:%d/%m/%Y %H:%M} ({s.label.lower()})" for s in doomed[1:])
            message += ("\n\nCes sauvegardes en dépendent et ne pourraient plus être restaurées : elles seront "
                        f"supprimées aussi :\n{others}")
        if not ask_yes_no(self, "Supprimer", message):
            return
        if self.busy or not self._take_lock():
            return
        repo = self.repo
        self._set_busy(True)
        self.status.busy("Suppression…")

        def work() -> None:
            # Refusé si une sauvegarde (même d'un autre compte Windows) écrit dans ce dossier.
            backup.delete_sets(repo, [s.id for s in doomed])

        def finished() -> None:
            self._release_lock()
            self.load_repository(repo)

        self.dispatcher.run(work, on_done=lambda _r: self.status.idle(f"{len(doomed)} sauvegarde(s) supprimée(s)."),
                            on_error=lambda e: self.status.idle(f"Erreur : {e}"), on_finally=finished)


# ====================================================================== fenêtres

class _Dialog(ctk.CTkToplevel):
    def __init__(self, parent, title: str):
        super().__init__(parent)
        self.withdraw()
        self.title(title)
        self.configure(fg_color=theme.WINDOW_BG)
        self.resizable(False, False)
        self.transient(parent.winfo_toplevel())
        self.body = ctk.CTkFrame(self, fg_color="transparent")
        self.body.pack(fill="both", expand=True, padx=24, pady=20)

    def show(self) -> None:
        self.update_idletasks()
        top = self.master.winfo_toplevel()
        x = top.winfo_rootx() + (top.winfo_width() - self.winfo_reqwidth()) // 2
        y = top.winfo_rooty() + (top.winfo_height() - self.winfo_reqheight()) // 3
        self.geometry(f"+{max(0, x)}+{max(0, y)}")
        self.deiconify()
        self.after(200, self._grab)

    def _grab(self) -> None:
        from app.utils.paths import resource_path

        icon = resource_path("assets/icon.ico")
        try:
            if icon.exists():
                self.iconbitmap(str(icon))
            self.grab_set()
            self.focus_force()
        except Exception:  # noqa: BLE001 - fenêtre fermée entre-temps
            pass


class ChoiceDialog(_Dialog):
    def __init__(self, parent, title: str, question: str, choices: list[str], on_choice):
        super().__init__(parent, title)
        ctk.CTkLabel(self.body, text=question, font=theme.font(13), wraplength=380, justify="left") \
            .pack(anchor="w", pady=(0, 10))
        for index, choice in enumerate(choices):
            neutral_button(self.body, choice, lambda i=index: (self.destroy(), on_choice(i)), width=380) \
                .pack(pady=3)
        self.show()


class RestoreDialog(_Dialog):
    """Où restaurer, et faut-il remplacer les fichiers existants ?"""

    def __init__(self, parent, backup_set: BackupSet, count: int, on_ok):
        super().__init__(parent, "Restaurer")
        self.on_ok = on_ok
        body = self.body
        ctk.CTkLabel(body, text=f"Restaurer {_count(count)} fichier(s)", font=theme.font(17, "bold"), anchor="w") \
            .pack(anchor="w")
        ctk.CTkLabel(body, text=f"tels qu'ils étaient le {backup_set.created:%d/%m/%Y à %H:%M}",
                     font=theme.font(12), text_color=theme.MUTED, anchor="w").pack(anchor="w", pady=(0, 12))

        self.mode = ctk.StringVar(value=FOLDER)
        ctk.CTkRadioButton(body, text="Dans un dossier de mon choix (recommandé)", variable=self.mode, value=FOLDER,
                           font=theme.font(13), command=self._mode_changed).pack(anchor="w")
        line = ctk.CTkFrame(body, fg_color="transparent")
        line.pack(fill="x", padx=(28, 0), pady=(4, 2))
        default = Path.home() / "Desktop" / f"Restauration {datetime.now():%Y-%m-%d %Hh%M}"
        self.folder = ctk.StringVar(value=str(default))
        self.folder_entry = ctk.CTkEntry(line, textvariable=self.folder, width=360, height=32)
        self.folder_entry.pack(side="left")
        self.browse_btn = neutral_button(line, "Parcourir…", self._browse, width=90)
        self.browse_btn.pack(side="left", padx=(6, 0))
        ctk.CTkLabel(body, text="Un sous-dossier par dossier sauvegardé (Documents, Images…). Rien n'est modifié "
                                "ailleurs sur le PC.", font=theme.font(11), text_color=theme.MUTED,
                     wraplength=440, justify="left", anchor="w").pack(anchor="w", padx=(28, 0), pady=(0, 10))

        ctk.CTkRadioButton(body, text="À leur emplacement d'origine", variable=self.mode, value=ORIGINAL,
                           font=theme.font(13), command=self._mode_changed).pack(anchor="w")
        origins = "\n".join(f"• {s.get('path')}" for s in backup_set.sources[:6])
        ctk.CTkLabel(body, text=origins + "\nSur un nouveau PC, le nom d'utilisateur a pu changer : préférez alors "
                                          "un dossier de votre choix.",
                     font=theme.font(11), text_color=theme.MUTED, wraplength=440, justify="left", anchor="w") \
            .pack(anchor="w", padx=(28, 0), pady=(2, 10))

        self.overwrite = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(body, text="Remplacer les fichiers existants s'ils sont différents", variable=self.overwrite,
                        font=theme.font(12), checkbox_width=20, checkbox_height=20).pack(anchor="w", pady=(0, 4))
        ctk.CTkLabel(body, text="Décoché : un fichier déjà présent et modifié depuis est conservé tel quel.",
                     font=theme.font(11), text_color=theme.MUTED, anchor="w").pack(anchor="w", padx=(28, 0))

        buttons = ctk.CTkFrame(body, fg_color="transparent")
        buttons.pack(fill="x", pady=(18, 0))
        primary_button(buttons, "Restaurer", self._ok, width=140).pack(side="right")
        neutral_button(buttons, "Annuler", self.destroy, width=100).pack(side="right", padx=8)
        self.show()

    def _mode_changed(self) -> None:
        state = "normal" if self.mode.get() == FOLDER else "disabled"
        self.folder_entry.configure(state=state)
        self.browse_btn.configure(state=state)

    def _browse(self) -> None:
        folder = filedialog.askdirectory(parent=self, title="Dossier où restaurer")
        if folder:
            self.folder.set(os.path.normpath(folder))

    def _ok(self) -> None:
        mode = self.mode.get()
        folder = self.folder.get().strip()
        if mode == FOLDER:
            if not folder:
                return
            try:
                os.makedirs(folder, exist_ok=True)
            except OSError as exc:
                show_error(self, "Dossier invalide", str(exc))
                return
        elif not ask_yes_no(self, "Emplacement d'origine",
                            "Les fichiers vont être recopiés à leur emplacement d'origine."
                            + ("\n\nLes fichiers existants différents seront REMPLACÉS par la version de la "
                               "sauvegarde." if self.overwrite.get() else "")
                            + "\n\nContinuer ?"):
            return
        overwrite = self.overwrite.get()
        self.destroy()
        self.on_ok(mode, folder, overwrite)
