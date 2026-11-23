"""Page Nettoyage : analyse puis suppression des fichiers inutiles."""

from __future__ import annotations

import threading

import customtkinter as ctk

import psutil

from app.core.cleaner import (CATEGORY_ORDER, CleanResult, CleanTarget, ScanResult, build_targets, clean_target,
                              running_blockers, scan_target)
from app.ui import theme
from app.ui.pages.base import Page
from app.ui.widgets import (PageHeader, ResultDialog, StatusLine, Stepper, ask_yes_no, neutral_button,
                            primary_button, success_button)
from app.utils.fmt import human_size

STEPS = ["Choisir", "Analyser", "Nettoyer", "Terminé"]


def _free_space() -> int | None:
    try:
        return psutil.disk_usage("C:\\").free
    except OSError:
        return None


class CleanerPage(Page):
    page_id = "cleaner"

    def __init__(self, master, app):
        super().__init__(master, app)
        self.targets: list[CleanTarget] = build_targets()
        self.vars: dict[str, ctk.BooleanVar] = {}
        self.size_labels: dict[str, ctk.CTkLabel] = {}
        self.results: dict[str, ScanResult] = {}
        self.cancel = threading.Event()
        self.busy = False
        self._free_before: int | None = None

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(3, weight=1)

        PageHeader(self, "Nettoyage",
                   "Libérez de l'espace en supprimant les fichiers temporaires et les caches. "
                   "Vos documents, mots de passe et historiques ne sont jamais touchés.") \
            .grid(row=0, column=0, sticky="ew", pady=(0, 10))

        self.stepper = Stepper(self, STEPS)
        self.stepper.grid(row=1, column=0, sticky="w", pady=(0, 12))

        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.grid(row=2, column=0, sticky="ew", pady=(0, 10))
        self.scan_btn = primary_button(bar, "Analyser", self.start_scan, width=130)
        self.scan_btn.pack(side="left")
        self.clean_btn = success_button(bar, "Nettoyer", self.start_clean, width=130)
        self.clean_btn.pack(side="left", padx=8)
        self.cancel_btn = neutral_button(bar, "Annuler", self._cancel, width=100)
        neutral_button(bar, "Tout cocher", lambda: self._select("all"), width=110).pack(side="left", padx=(16, 4))
        neutral_button(bar, "Par défaut", lambda: self._select("default"), width=110).pack(side="left", padx=4)
        neutral_button(bar, "Aucun", lambda: self._select("none"), width=80).pack(side="left", padx=4)
        self.total_label = ctk.CTkLabel(bar, text="", font=theme.font(18, "bold"), text_color=theme.SUCCESS)
        self.total_label.pack(side="right")

        self.list_frame = ctk.CTkScrollableFrame(self, fg_color=theme.CARD, corner_radius=12,
                                                 border_width=1, border_color=theme.CARD_BORDER)
        self.list_frame.grid(row=3, column=0, sticky="nsew")
        self.list_frame.grid_columnconfigure(0, weight=1)
        self._build_list()

        self.status = StatusLine(self)
        self.status.grid(row=4, column=0, sticky="ew", pady=(10, 0))
        self.status.set_text("Étape 1 : cochez les éléments à nettoyer (la sélection par défaut convient dans la "
                             "plupart des cas), puis cliquez sur « Analyser ».")

    # ------------------------------------------------------------------ liste
    def _build_list(self) -> None:
        saved: dict = self.settings.get("cleaner_selection", {}) or {}
        row = 0
        for category in CATEGORY_ORDER:
            items = [t for t in self.targets if t.category == category]
            if not items:
                continue
            ctk.CTkLabel(self.list_frame, text=category.upper(), font=theme.font(12, "bold"),
                         text_color=theme.ACCENT, anchor="w") \
                .grid(row=row, column=0, columnspan=2, sticky="w", padx=14, pady=(14 if row else 8, 4))
            row += 1
            for target in items:
                locked = target.admin and not self.is_admin
                value = bool(saved.get(target.id, target.default)) and not locked
                var = ctk.BooleanVar(value=value)
                self.vars[target.id] = var

                line = ctk.CTkFrame(self.list_frame, fg_color="transparent")
                line.grid(row=row, column=0, sticky="ew", padx=10, pady=3)
                line.grid_columnconfigure(0, weight=1)
                title = target.name + ("   (administrateur requis)" if locked else "")
                cb = ctk.CTkCheckBox(line, text=title, variable=var, font=theme.font(13),
                                     command=self._save_selection, checkbox_width=20, checkbox_height=20)
                cb.grid(row=0, column=0, sticky="w")
                if locked:
                    cb.configure(state="disabled")
                ctk.CTkLabel(line, text=target.description, font=theme.font(11), text_color=theme.MUTED,
                             anchor="w", justify="left", wraplength=640) \
                    .grid(row=1, column=0, sticky="w", padx=(30, 0))
                size = ctk.CTkLabel(line, text="", font=theme.font(13, "bold"), width=150, anchor="e")
                size.grid(row=0, column=1, rowspan=2, sticky="e", padx=(8, 6))
                self.size_labels[target.id] = size
                row += 1

    def _save_selection(self) -> None:
        self.settings.set("cleaner_selection", {k: v.get() for k, v in self.vars.items()})
        self._update_total()

    def _select(self, mode: str) -> None:
        for target in self.targets:
            if target.admin and not self.is_admin:
                continue
            value = {"all": True, "none": False}.get(mode, target.default)
            self.vars[target.id].set(value)
        self._save_selection()

    def _checked_targets(self) -> list[CleanTarget]:
        return [t for t in self.targets if self.vars[t.id].get() and not (t.admin and not self.is_admin)]

    def _update_total(self) -> None:
        total = sum(self.results[t.id].size for t in self._checked_targets() if t.id in self.results)
        self.total_label.configure(text=f"{human_size(total)} à libérer" if self.results else "")

    def _set_busy(self, busy: bool) -> None:
        self.busy = busy
        state = "disabled" if busy else "normal"
        self.scan_btn.configure(state=state)
        self.clean_btn.configure(state=state)
        if busy:
            self.cancel_btn.pack(side="left")
        else:
            self.cancel_btn.pack_forget()

    def _cancel(self) -> None:
        self.cancel.set()
        self.status.set_text("Annulation…")

    # ------------------------------------------------------------------ analyse
    def start_scan(self) -> None:
        if self.busy:
            return
        targets = self._checked_targets()
        if not targets:
            self.status.set_text("Aucun élément coché.")
            return
        self.cancel.clear()
        self._set_busy(True)
        self.stepper.set(1)
        for t in targets:
            self.size_labels[t.id].configure(text="…", text_color=theme.MUTED)
        progress = self.dispatcher.wrap(self._on_scan_progress)

        def work() -> None:
            for index, target in enumerate(targets):
                if self.cancel.is_set():
                    break
                progress(index, len(targets), target, None)
                result = scan_target(target, self.cancel)
                progress(index + 1, len(targets), target, result)

        self.dispatcher.run(work, on_done=lambda _r: self._scan_done(), on_error=self._on_error,
                            on_finally=lambda: self._set_busy(False))

    def _on_scan_progress(self, done: int, total: int, target: CleanTarget, result: ScanResult | None) -> None:
        if result is None:
            self.status.progress(done / total, f"Analyse : {target.name}…")
            return
        self.results[target.id] = result
        label = self.size_labels[target.id]
        if result.size:
            label.configure(text=f"{human_size(result.size)}  ({result.files} fich.)", text_color=theme.NEUTRAL_TEXT)
        else:
            label.configure(text="Rien à nettoyer", text_color=theme.MUTED)
        self._update_total()

    def _scan_done(self) -> None:
        total = sum(r.size for tid, r in self.results.items() if self.vars[tid].get())
        if self.cancel.is_set():
            self.stepper.set(0)
            self.status.idle("Analyse annulée.")
            return
        self.stepper.set(2)
        self.status.idle(f"Étape 3 : {human_size(total)} peuvent être libérés. Vérifiez la liste, décochez ce que "
                         "vous voulez garder, puis cliquez sur « Nettoyer ».")
        self.app.notify("Analyse terminée", f"{human_size(total)} de fichiers inutiles trouvés.", "info",
                        action=("Nettoyer maintenant", self.start_clean) if total else None)

    # ------------------------------------------------------------------ nettoyage
    def start_clean(self) -> None:
        if self.busy:
            return
        targets = self._checked_targets()
        if not targets:
            self.status.set_text("Aucun élément coché.")
            return
        estimate = sum(self.results[t.id].size for t in targets if t.id in self.results)
        names = "\n".join(f"• {t.name}" for t in targets[:12]) + ("\n• …" if len(targets) > 12 else "")
        message = f"Les éléments suivants vont être nettoyés :\n\n{names}\n\n"
        if estimate:
            message += f"Espace estimé : {human_size(estimate)}\n\n"
        message += "Les fichiers en cours d'utilisation seront ignorés. Continuer ?"
        if not ask_yes_no(self, "Confirmer le nettoyage", message):
            return
        blockers = running_blockers([t.id for t in targets])
        if blockers and not ask_yes_no(
                self, "Applications ouvertes",
                "Ces applications sont ouvertes : " + ", ".join(blockers) + ".\n\n"
                "Leurs fichiers en cours d'utilisation ne pourront pas être supprimés. Pour un nettoyage complet, "
                "fermez-les puis relancez.\n\nContinuer quand même ?"):
            self.status.set_text("Fermez les applications indiquées puis cliquez à nouveau sur « Nettoyer ».")
            return

        self._free_before = _free_space()
        self.cancel.clear()
        self._set_busy(True)
        self.stepper.set(2)
        progress = self.dispatcher.wrap(self._on_clean_progress)

        def work() -> list[CleanResult]:
            results = []
            for index, target in enumerate(targets):
                if self.cancel.is_set():
                    break
                progress(index, len(targets), target, None)
                result = clean_target(target, self.cancel)
                results.append(result)
                progress(index + 1, len(targets), target, result)
            return results

        self.dispatcher.run(work, on_done=self._clean_done, on_error=self._on_error,
                            on_finally=lambda: self._set_busy(False))

    def _on_clean_progress(self, done: int, total: int, target: CleanTarget, result: CleanResult | None) -> None:
        if result is None:
            self.status.progress(done / total, f"Nettoyage : {target.name}…")
            self.size_labels[target.id].configure(text="Nettoyage…", text_color=theme.MUTED)
            return
        self.results.pop(target.id, None)
        text = f"✓ {human_size(result.freed)} libérés"
        if result.failed:
            text += f"  ({result.failed} en cours d'utilisation)"
        self.size_labels[target.id].configure(text=text, text_color=theme.SUCCESS)

    def _clean_done(self, results: list[CleanResult]) -> None:
        freed = sum(r.freed for r in results)
        deleted = sum(r.deleted for r in results)
        skipped = sum(r.failed for r in results)
        self.total_label.configure(text=f"{human_size(freed)} libérés")
        msg = f"Nettoyage terminé : {human_size(freed)} libérés ({deleted} fichiers supprimés)."
        if skipped:
            msg += f" {skipped} fichiers en cours d'utilisation ont été ignorés."
        self.status.idle(msg)
        self.stepper.set(len(STEPS))
        self.app.notify("Nettoyage terminé", f"{human_size(freed)} libérés sur votre PC.", "success")

        stats = [("Espace libéré", human_size(freed)), ("Fichiers supprimés", f"{deleted:,}".replace(",", " "))]
        if skipped:
            stats.append(("Fichiers ignorés (en cours d'utilisation)", f"{skipped:,}".replace(",", " ")))
        after = _free_space()
        if self._free_before is not None and after is not None:
            stats.append(("Espace libre sur C:", f"{human_size(self._free_before)} → {human_size(after)}"))
        tips = []
        if skipped:
            tips.append("Les fichiers ignorés sont utilisés par des programmes ouverts : ils seront supprimés "
                        "lors d'un prochain nettoyage, une fois ces programmes fermés.")
        tips.append("Les caches se reconstituent avec le temps : un nettoyage par mois suffit largement.")
        ResultDialog(self, "Nettoyage terminé", f"{human_size(freed)} libérés !", stats, tips, actions=[
            ("Vérifier les pilotes", lambda: self.app.show_page("drivers").start_analysis()),
            ("Mettre à jour les logiciels", lambda: self.app.show_page("software").start_search()),
            ("Découvrir la carte de mon PC", lambda: self.app.show_page("hardware")),
        ])

    def _on_error(self, exc: BaseException) -> None:
        self.status.idle(f"Erreur : {exc}")
        self.app.notify("Erreur pendant le nettoyage", str(exc), "error")
