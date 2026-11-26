"""Page « Entretien guidé » : un assistant qui vérifie tout le PC, étape par étape.

Rien n'est supprimé ni installé sans l'accord de l'utilisateur : l'assistant analyse,
explique ce qu'il a trouvé, puis demande avant chaque action.
"""

from __future__ import annotations

import threading
from datetime import datetime

import customtkinter as ctk

from app.core import cleaner, driver_analysis, software, startup
from app.ui import theme
from app.ui.pages.base import Page
from app.ui.widgets import (Card, LEVEL_COLORS, PageHeader, ResultDialog, TipBox, ask_yes_no, neutral_button,
                            primary_button, success_button, symbol, Badge)
from app.utils.fmt import human_size

STATE_STYLE = {
    "waiting": (theme.NEUTRAL_BTN, theme.MUTED, None),
    "running": (theme.ACCENT, "#ffffff", None),
    "ask": (theme.WARNING, "#ffffff", "!"),
    "done": (theme.SUCCESS, "#ffffff", "✓"),
    "skipped": (theme.NEUTRAL_BTN, theme.MUTED, "–"),
    "error": (theme.DANGER, "#ffffff", "✕"),
}


class StepCard(Card):
    def __init__(self, master, number: int, title: str, description: str):
        super().__init__(master)
        self.number = number
        self.grid_columnconfigure(1, weight=1)
        self.circle = Badge(self, text=str(number), width=34, height=34, corner_radius=17,
                                   font=theme.font(14, "bold"))
        self.circle.grid(row=0, column=0, rowspan=3, padx=(16, 14), pady=14, sticky="n")
        ctk.CTkLabel(self, text=title, font=theme.font(15, "bold"), anchor="w").grid(row=0, column=1, sticky="w",
                                                                                     pady=(14, 0))
        ctk.CTkLabel(self, text=description, font=theme.font(12), text_color=theme.MUTED, anchor="w",
                     justify="left", wraplength=560).grid(row=1, column=1, sticky="w")
        self.status = ctk.CTkLabel(self, text="En attente", font=theme.font(12, "bold"), anchor="w", justify="left",
                                   text_color=theme.MUTED, wraplength=560)
        self.status.grid(row=2, column=1, sticky="w", pady=(4, 14))
        self.bar = ctk.CTkProgressBar(self, mode="indeterminate", width=120, height=6)
        self.actions = ctk.CTkFrame(self, fg_color="transparent", width=1, height=1)
        self.actions.grid(row=0, column=2, rowspan=3, padx=16, pady=14, sticky="e")
        self.set_state("waiting", "En attente")

    def set_state(self, state: str, text: str = "", actions: list[tuple[str, object, str]] | None = None) -> None:
        self.state = state
        fg, color, sym = STATE_STYLE[state]
        circle_text, circle_font = symbol(sym, 14) if sym else (str(self.number), theme.font(14, "bold"))
        self.circle.configure(fg_color=fg, text_color=color, text=circle_text, font=circle_font)
        status_color = {"done": theme.SUCCESS, "error": theme.DANGER, "ask": theme.WARNING,
                        "running": theme.ACCENT}.get(state, theme.MUTED)
        self.status.configure(text=text, text_color=status_color)
        if state == "running":
            self.bar.grid(row=3, column=1, sticky="w", pady=(0, 14))
            self.bar.start()
        else:
            self.bar.stop()
            self.bar.grid_forget()
        # Tk ne rétrécit pas un cadre dont on retire tous les enfants : on le recrée.
        self.actions.destroy()
        self.actions = ctk.CTkFrame(self, fg_color="transparent", width=1, height=1)
        self.actions.grid(row=0, column=2, rowspan=3, padx=16, pady=14, sticky="e")
        for label, callback, style in actions or []:
            maker = {"primary": primary_button, "success": success_button}.get(style, neutral_button)
            maker(self.actions, label, callback, width=10).pack(fill="x", pady=3)


class AssistantPage(Page):
    page_id = "assistant"

    STEPS = [
        ("Rechercher les fichiers inutiles", "Fichiers temporaires, caches, corbeille… (analyse seulement)."),
        ("Nettoyer", "Supprime ce qui a été trouvé, après votre accord."),
        ("Vérifier les pilotes", "Compare vos pilotes avec Windows Update et les sources officielles des fabricants."),
        ("Vérifier les logiciels", "Cherche les nouvelles versions de vos applications."),
        ("Vérifier le démarrage", "Compte les programmes lancés avec Windows (ils ralentissent l'allumage)."),
    ]

    def __init__(self, master, app):
        super().__init__(master, app)
        self.running = False
        self.cancel = threading.Event()
        self.results: dict[str, object] = {}

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(3, weight=1)

        PageHeader(self, "Entretien guidé",
                   "Vérifiez tout votre PC en quelques minutes, sans rien connaître à l'informatique.") \
            .grid(row=0, column=0, sticky="ew", pady=(0, 10))
        TipBox(self, "L'assistant enchaîne 5 étapes. Rien n'est supprimé ni installé sans votre accord : il vous "
                     "montre ce qu'il a trouvé et vous demande avant chaque action. Vous pouvez continuer à utiliser "
                     "votre PC pendant ce temps, une notification vous prévient quand votre avis est nécessaire.") \
            .grid(row=1, column=0, sticky="ew", pady=(0, 10))

        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.grid(row=2, column=0, sticky="ew", pady=(0, 10))
        self.start_btn = primary_button(bar, "▶  Démarrer l'entretien", self.start, width=200)
        self.start_btn.pack(side="left")
        self.stop_btn = neutral_button(bar, "Arrêter", self.stop, width=100)
        self.overall = ctk.CTkLabel(bar, text="", font=theme.font(13, "bold"), text_color=theme.MUTED)
        self.overall.pack(side="right")
        last = self.settings.get("last_maintenance")
        if last:
            self.overall.configure(text=f"Dernier entretien : {last}")

        listing = ctk.CTkScrollableFrame(self, fg_color="transparent")
        listing.grid(row=3, column=0, sticky="nsew")
        listing.grid_columnconfigure(0, weight=1)
        self.cards = []
        for i, (title, desc) in enumerate(self.STEPS):
            card = StepCard(listing, i + 1, title, desc)
            card.grid(row=i, column=0, sticky="ew", pady=5, padx=(0, 6))
            self.cards.append(card)

    # ------------------------------------------------------------------ contrôle
    def start(self) -> None:
        if self.running:
            return
        self.running = True
        self.cancel.clear()
        self.results = {}
        self.start_btn.configure(state="disabled")
        self.stop_btn.pack(side="left", padx=8)
        for card in self.cards:
            card.set_state("waiting", "En attente")
        self._step_scan()

    def stop(self) -> None:
        self.cancel.set()
        self.overall.configure(text="Arrêt après l'étape en cours…")

    def _stopped(self) -> bool:
        if not self.cancel.is_set():
            return False
        for card in self.cards:
            if card.state in ("waiting", "ask"):
                card.set_state("skipped", "Non effectué (entretien arrêté)")
        self._end(stopped=True)
        return True

    def _progress(self, index: int) -> None:
        self.overall.configure(text=f"Étape {index + 1} sur {len(self.STEPS)}", text_color=theme.ACCENT)

    # ------------------------------------------------------------------ étape 1 : analyse
    def _targets(self) -> list[cleaner.CleanTarget]:
        saved = self.settings.get("cleaner_selection", {}) or {}
        return [t for t in cleaner.build_targets()
                if saved.get(t.id, t.default) and not (t.admin and not self.is_admin)]

    def _step_scan(self) -> None:
        self._progress(0)
        card = self.cards[0]
        card.set_state("running", "Analyse en cours…")
        targets = self._targets()

        def work():
            results = [cleaner.scan_target(t, self.cancel) for t in targets]
            return targets, results

        def done(value) -> None:
            targets, results = value
            total = sum(r.size for r in results)
            self.results["targets"] = [t for t, r in zip(targets, results) if r.size]
            self.results["found"] = total
            card.set_state("done", f"{human_size(total)} de fichiers inutiles trouvés.")
            if self._stopped():
                return
            if total == 0:
                self.cards[1].set_state("skipped", "Rien à nettoyer.")
                self._step_drivers()
            else:
                self._ask_clean(total)

        self.dispatcher.run(work, on_done=done, on_error=lambda e: self._step_error(0, e, self._step_drivers))

    # ------------------------------------------------------------------ étape 2 : nettoyage
    def _ask_clean(self, total: int) -> None:
        self._progress(1)
        names = ", ".join(t.name.split(" — ")[0] for t in self.results["targets"][:4])
        more = "…" if len(self.results["targets"]) > 4 else ""
        self.cards[1].set_state("ask", f"Votre accord est nécessaire : nettoyer {human_size(total)} ? "
                                       f"({names}{more})", actions=[
            (f"Nettoyer {human_size(total)}", self._step_clean, "success"),
            ("Voir le détail", lambda: self.app.show_page("cleaner"), "neutral"),
            ("Passer cette étape", self._skip_clean, "neutral"),
        ])
        self.app.notify("L'entretien attend votre accord", f"Nettoyer {human_size(total)} de fichiers inutiles ?",
                        "info", action=("Voir", lambda: self.app.show_page("assistant")))

    def _skip_clean(self) -> None:
        self.cards[1].set_state("skipped", "Étape passée.")
        self._step_drivers()

    def _step_clean(self) -> None:
        targets = self.results.get("targets", [])
        blockers = cleaner.running_blockers([t.id for t in targets])
        if blockers and not ask_yes_no(
                self, "Applications ouvertes",
                "Ces applications sont ouvertes : " + ", ".join(blockers) + ".\n\n"
                "Une partie de leurs fichiers ne pourra pas être supprimée. Fermez-les pour un nettoyage complet, "
                "puis cliquez sur Oui (ou Oui directement pour continuer quand même)."):
            return
        card = self.cards[1]
        card.set_state("running", "Nettoyage en cours…")

        def work():
            return [cleaner.clean_target(t, self.cancel) for t in targets]

        def done(results) -> None:
            freed = sum(r.freed for r in results)
            skipped = sum(r.failed for r in results)
            self.results["freed"] = freed
            text = f"{human_size(freed)} libérés."
            if skipped:
                text += f" {skipped} fichiers en cours d'utilisation ont été laissés."
            card.set_state("done", text)
            if not self._stopped():
                self._step_drivers()

        self.dispatcher.run(work, on_done=done, on_error=lambda e: self._step_error(1, e, self._step_drivers))

    # ------------------------------------------------------------------ étape 3 : pilotes
    def _step_drivers(self) -> None:
        self._progress(2)
        card = self.cards[2]
        card.set_state("running", "Analyse des pilotes… (jusqu'à 2 minutes)")
        progress = self.dispatcher.wrap(lambda text: card.status.configure(text=text))

        def done(report: driver_analysis.AnalysisReport) -> None:
            page = self.app.get_page("drivers")
            page.show_analysis(report, notify=False)
            auto = sum(1 for d in report.devices if d.can_auto_install)
            attention = sum(1 for d in report.devices
                            if d.status in (driver_analysis.PROBLEM, driver_analysis.GENERIC)
                            or (d.status == driver_analysis.VENDOR_UPDATE and not d.can_auto_install))
            unverified = report.count(driver_analysis.OLD, driver_analysis.UNVERIFIED)
            verified = report.count(driver_analysis.VERIFIED, driver_analysis.OPTIONAL)
            self.results["drivers_auto"] = auto
            self.results["drivers_check"] = attention
            self.results["drivers_unverified"] = unverified
            if auto or attention:
                parts = []
                if auto:
                    parts.append(f"{auto} pilote(s) à mettre à jour")
                if attention:
                    parts.append(f"{attention} à corriger ou télécharger")
                if unverified:
                    parts.append(f"{unverified} non vérifiable(s)")
                card.set_state("done", " • ".join(parts) + ". Vous pourrez vous en occuper à la fin.", actions=[
                    ("Voir les pilotes", lambda: self.app.show_page("drivers"), "neutral")])
            elif unverified:
                card.set_state("done", f"{verified} pilote(s) vérifié(s) à jour. {unverified} ne peuvent pas être "
                                       "vérifiés automatiquement (absents des sources officielles consultées) : "
                                       "la page Pilotes donne le lien officiel de chacun.", actions=[
                    ("Voir les pilotes", lambda: self.app.show_page("drivers"), "neutral")])
            else:
                card.set_state("done", "Tous vos pilotes sont vérifiés et à jour.")
            if not self._stopped():
                self._step_software()

        self.dispatcher.run(driver_analysis.run_full_analysis, self.app.driver_service, progress,
                            on_done=done, on_error=lambda e: self._step_error(2, e, self._step_software))

    # ------------------------------------------------------------------ étape 4 : logiciels
    def _step_software(self) -> None:
        self._progress(3)
        card = self.cards[3]
        if not software.winget_path():
            card.set_state("skipped", "winget n'est pas installé sur ce PC : étape ignorée.")
            self._step_startup()
            return
        card.set_state("running", "Recherche des mises à jour…")

        def done(updates) -> None:
            self.app.get_page("software").show_updates(updates, notify=False)
            self.results["software"] = len(updates)
            if updates:
                card.set_state("done", f"{len(updates)} logiciel(s) peuvent être mis à jour.", actions=[
                    ("Voir les logiciels", lambda: self.app.show_page("software"), "neutral")])
            else:
                card.set_state("done", "Tous vos logiciels sont à jour.")
            if not self._stopped():
                self._step_startup()

        self.dispatcher.run(software.list_upgrades, on_done=done,
                            on_error=lambda e: self._step_error(3, e, self._step_startup))

    # ------------------------------------------------------------------ étape 5 : démarrage
    def _step_startup(self) -> None:
        self._progress(4)
        card = self.cards[4]
        card.set_state("running", "Lecture des programmes de démarrage…")

        def done(entries) -> None:
            enabled = [e for e in entries if e.enabled]
            self.results["startup"] = len(enabled)
            text = f"{len(enabled)} programme(s) se lancent avec Windows."
            if len(enabled) > 8:
                text += " C'est beaucoup : en désactiver quelques-uns accélérera le démarrage."
            card.set_state("done", text, actions=[("Gérer le démarrage", lambda: self.app.show_page("startup"),
                                                   "neutral")])
            self._end()

        self.dispatcher.run(startup.list_startup_entries, on_done=done,
                            on_error=lambda e: self._step_error(4, e, self._end))

    # ------------------------------------------------------------------ fin
    def _step_error(self, index: int, exc: BaseException, next_step) -> None:
        self.cards[index].set_state("error", f"Erreur : {exc}")
        if not self._stopped():
            next_step()

    def _end(self, stopped: bool = False) -> None:
        self.running = False
        self.start_btn.configure(state="normal", text="▶  Relancer l'entretien")
        self.stop_btn.pack_forget()
        today = datetime.now().strftime("%d/%m/%Y à %H:%M")
        if stopped:
            self.overall.configure(text="Entretien arrêté.", text_color=theme.MUTED)
            return
        self.settings.set("last_maintenance", today)
        self.overall.configure(text=f"✓ Entretien terminé ({today})", text_color=LEVEL_COLORS["success"])

        r = self.results
        stats = [("Espace libéré", human_size(r.get("freed", 0))),
                 ("Pilotes à mettre à jour", str(r.get("drivers_auto", "—"))),
                 ("Pilotes à corriger", str(r.get("drivers_check", "—"))),
                 ("Pilotes non vérifiables", str(r.get("drivers_unverified", "—"))),
                 ("Logiciels à mettre à jour", str(r.get("software", "—"))),
                 ("Programmes au démarrage", str(r.get("startup", "—")))]
        actions = []
        if r.get("drivers_auto"):
            actions.append(("Mettre à jour les pilotes", lambda: self.app.show_page("drivers")))
        if r.get("software"):
            actions.append(("Mettre à jour les logiciels", lambda: self.app.show_page("software")))
        actions.append(("Découvrir la carte de mon PC", lambda: self.app.show_page("hardware")))
        tips = ["Refaites un entretien une fois par mois pour garder un PC rapide et à jour."]
        self.app.notify("Entretien terminé", "Votre bilan est prêt.", "success",
                        action=("Voir", lambda: self.app.show_page("assistant")))
        ResultDialog(self, "Entretien terminé", "Votre PC a été vérifié !", stats, tips, actions)
