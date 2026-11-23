"""Page « Mon PC » : carte interactive du matériel, expliquée pour les non-connaisseurs."""

from __future__ import annotations

import re
import tkinter as tk

import customtkinter as ctk

from app.core import hardware
from app.core.hardware import HardwareReport
from app.core.vendors import board_maker
from app.ui import theme
from app.ui.pages.base import Page
from app.ui.widgets import Card, LEVEL_COLORS, LEVEL_SYMBOLS, PageHeader, TipBox, neutral_button, symbol, Badge
from app.utils.fmt import human_size
from app.utils.system import open_url

# Couleur de chaque famille de composants (clair, sombre).
KIND_COLORS = {
    "cpu": ("#2563eb", "#60a5fa"), "ram": ("#7c3aed", "#a78bfa"), "gpu": ("#16a34a", "#4ade80"),
    "storage": ("#d97706", "#fbbf24"), "network": ("#0891b2", "#22d3ee"), "audio": ("#db2777", "#f472b6"),
    "display": ("#0284c7", "#38bdf8"), "cooling": ("#0d9488", "#2dd4bf"), "board": ("#64748b", "#94a3b8"),
    "battery": ("#ca8a04", "#facc15"),
}
GLYPHS = {
    "cpu": "", "ram": "", "gpu": "", "storage": "", "network": "",
    "audio": "", "display": "", "cooling": "", "board": "", "battery": "",
}


def _mix(c1: str, c2: str, t: float) -> str:
    """Mélange deux couleurs hexadécimales : t=0 -> c1, t=1 -> c2."""
    a = [int(c1[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(c2[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(x + (y - x) * t):02x}" for x, y in zip(a, b))


def short_cpu(name: str) -> str:
    """'13th Gen Intel(R) Core(TM) i7-13700KF' -> 'Core i7-13700KF'."""
    text = re.sub(r"\((R|TM)\)", "", name, flags=re.I)
    text = re.sub(r"\b\d+(st|nd|rd|th) Gen\b", "", text, flags=re.I)
    text = re.sub(r"\s+(\d+-Core )?Processor\b.*$", "", text, flags=re.I)
    text = text.replace("Intel ", "").replace("AMD ", "")
    return " ".join(text.split())


def short_gpu(name: str) -> str:
    return " ".join(name.replace("NVIDIA ", "").replace("GeForce ", "").replace("AMD ", "").split())


class PcMap(tk.Canvas):
    """Schéma du PC dessiné sur un canevas, mis à l'échelle de la place disponible."""

    VW, VH = 580, 700

    def __init__(self, master, on_select, icon_family: str):
        super().__init__(master, highlightthickness=0, bd=0)
        self.on_select = on_select
        self.icon_family = icon_family
        self.report: HardwareReport | None = None
        self.selected = "cpu"
        self.live: dict[str, str] = {}
        self._base_style: dict[str, tuple[str, float]] = {}
        self.s = 1.0
        self.ox = self.oy = 0.0
        self.bind("<Configure>", lambda _e: self.redraw())
        self.tag_bind("comp", "<Button-1>", self._click)
        self.tag_bind("comp", "<Enter>", self._enter)
        self.tag_bind("comp", "<Leave>", self._leave)

    # ------------------------------------------------------------------ outils de dessin
    def X(self, x: float) -> float:  # noqa: N802 - notation géométrique
        return self.ox + x * self.s

    def Y(self, y: float) -> float:  # noqa: N802
        return self.oy + y * self.s

    def font(self, size: float, weight: str = "normal"):
        return (theme.FONT_FAMILY, -max(8, int(size * self.s)), weight)

    def rrect(self, x, y, w, h, r, **kw):
        x1, y1, x2, y2 = self.X(x), self.Y(y), self.X(x + w), self.Y(y + h)
        r = r * self.s
        pts = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2, x2 - r, y2,
               x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
        return self.create_polygon(pts, smooth=True, splinesteps=10, **kw)

    def text(self, x, y, text, size=13, weight="normal", color=None, anchor="w", tags=(), angle=0):
        return self.create_text(self.X(x), self.Y(y), text=text, font=self.font(size, weight),
                                fill=color or self.fg, anchor=anchor, tags=tags, angle=angle)

    def glyph(self, x, y, kind, size=22, color=None, tags=()):
        if not self.icon_family:
            return None
        return self.create_text(self.X(x), self.Y(y), text=GLYPHS.get(kind, ""),
                                font=(self.icon_family, -max(8, int(size * self.s))),
                                fill=color or theme.pick(KIND_COLORS[kind]), tags=tags)

    def tags_for(self, kind: str) -> tuple[str, ...]:
        return ("comp", f"kind:{kind}")

    def box(self, kind: str, x, y, w, h, radius=14):
        """Rectangle d'un composant, coloré selon sa famille ; renvoie la couleur principale."""
        color = theme.pick(KIND_COLORS[kind])
        selected = kind == self.selected
        outline = color if selected else _mix(color, self.bg, 0.45)
        width = (3 if selected else 1.5) * max(self.s, 0.6)
        self._base_style[kind] = (outline, width)
        self.rrect(x, y, w, h, radius, fill=_mix(color, self.bg, 0.86), outline=outline, width=width,
                   tags=self.tags_for(kind) + (f"shape:{kind}",))
        return color

    # ------------------------------------------------------------------ dessin
    def set_report(self, report: HardwareReport) -> None:
        self.report = report
        self.redraw()

    def update_live(self, values: dict[str, str]) -> None:
        self.live.update(values)
        for key, value in values.items():
            self.itemconfigure(f"live:{key}", text=value)

    def redraw(self) -> None:
        self.delete("all")
        width, height = max(self.winfo_width(), 50), max(self.winfo_height(), 50)
        self.s = min(width / self.VW, height / self.VH)
        self.ox = (width - self.VW * self.s) / 2
        self.oy = (height - self.VH * self.s) / 2
        self.bg = theme.pick(theme.CARD)
        self.fg = theme.pick(theme.NEUTRAL_TEXT)
        self.muted = "#9aa3b2" if theme.is_dark() else "#6b7280"
        self.configure(bg=self.bg)
        if self.report is None:
            self.create_text(width / 2, height / 2, text="Analyse du matériel…", fill=self.muted,
                             font=(theme.FONT_FAMILY, -16))
            return
        rep = self.report
        self._draw_board(rep)
        self._draw_cpu(rep)
        self._draw_ram(rep)
        self._draw_gpu(rep)
        self._draw_storage(rep)
        self._draw_peripherals(rep)

    @staticmethod
    def _fit(text: str, chars: int) -> str:
        return text if len(text) <= chars else text[:max(1, chars - 1)] + "…"

    def _draw_board(self, rep: HardwareReport) -> None:
        color = theme.pick(KIND_COLORS["board"])
        selected = self.selected == "board"
        outline = color if selected else _mix(color, self.bg, 0.5)
        width = (3 if selected else 1.5) * max(self.s, 0.6)
        self._base_style["board"] = (outline, width)
        tags = self.tags_for("board")
        self.rrect(15, 15, 550, 525, 22, fill=_mix(color, self.bg, 0.92), outline=outline, width=width,
                   tags=tags + ("shape:board",))
        # Pistes décoratives entre les composants.
        trace = _mix(color, self.bg, 0.7)
        for x1, y1, x2, y2 in ((275, 150, 295, 150), (275, 185, 295, 185), (180, 240, 180, 275),
                               (100, 375, 100, 390), (490, 375, 490, 390), (300, 470, 300, 490)):
            self.create_line(self.X(x1), self.Y(y1), self.X(x2), self.Y(y2), fill=trace,
                             width=max(1, 3 * self.s), tags=tags)
        board = rep.of("board")
        name = board[0].name if board else "Carte mère"
        self.glyph(48, 40, "board", 18, tags=tags)
        self.text(66, 40, self._fit(f"Carte mère  •  {name}", 52), 14, "bold", tags=tags)
        bios = dict(board[0].specs).get("BIOS / UEFI", "") if board else ""
        self.rrect(395, 480, 155, 46, 8, fill=_mix(color, self.bg, 0.75), outline="", tags=tags)
        self.text(410, 495, "BIOS / UEFI", 12, "bold", tags=tags)
        self.text(410, 513, bios.split(" du ")[0] if bios else "—", 12, color=self.muted, tags=tags)
        self.text(45, 503, "Cliquez sur un composant", 11, color=self.muted, tags=tags)

    def _draw_cpu(self, rep: HardwareReport) -> None:
        cpus = rep.of("cpu")
        if not cpus:
            return
        tags = self.tags_for("cpu")
        color = self.box("cpu", 45, 65, 230, 175, 16)
        inner = _mix(color, self.bg, 0.6)
        self.rrect(127, 84, 66, 66, 8, fill=_mix(color, self.bg, 0.7), outline=inner, width=2 * self.s, tags=tags)
        for i in range(5):
            pos = 136 + i * 12   # broches haut / bas
            side = 93 + i * 12   # broches gauche / droite
            for x1, y1, x2, y2 in ((pos, 77, pos, 83), (pos, 151, pos, 157),
                                   (120, side, 126, side), (194, side, 200, side)):
                self.create_line(self.X(x1), self.Y(y1), self.X(x2), self.Y(y2), fill=inner,
                                 width=max(1, 2 * self.s), tags=tags)
        self.glyph(160, 117, "cpu", 28, tags=tags)
        self.text(160, 177, self._fit(short_cpu(cpus[0].name), 24), 16, "bold", anchor="center", tags=tags)
        self.text(160, 199, cpus[0].summary, 12, color=self.muted, anchor="center", tags=tags)
        self.text(160, 221, self.live.get("cpu", "Processeur"), 13, "bold", color=color, anchor="center",
                  tags=tags + ("live:cpu",))

    def _draw_ram(self, rep: HardwareReport) -> None:
        tags = self.tags_for("ram")
        color = theme.pick(KIND_COLORS["ram"])
        modules = rep.ram_modules
        slots = min(max(rep.ram_slots, len(modules), 2), 8)
        self.box("ram", 295, 65, 255, 190, 14)
        self.text(310, 85, "Mémoire vive", 14, "bold", tags=tags)
        spacing = 225 / slots
        for i in range(slots):
            x = 310 + i * spacing + (spacing - 26) / 2
            if i < len(modules):
                self.rrect(x, 100, 26, 110, 4, fill=color, outline="", tags=tags)
                self.text(x + 13, 155, human_size(modules[i]).replace(",0", ""), 12, "bold",
                          color=self.bg, anchor="center", tags=tags, angle=90)
            else:
                self.rrect(x, 100, 26, 110, 4, fill="", outline=_mix(color, self.bg, 0.4), width=1.5 * self.s,
                           dash=(3, 3), tags=tags)
                self.text(x + 13, 155, "libre", 11, color=self.muted, anchor="center", tags=tags, angle=90)
        ram = rep.of("ram")
        self.text(422, 226, ram[0].summary if ram else "", 12, color=self.muted, anchor="center", tags=tags)
        self.text(422, 243, self.live.get("ram", ""), 13, "bold", color=color, anchor="center",
                  tags=tags + ("live:ram",))

    def _draw_gpu(self, rep: HardwareReport) -> None:
        gpus = rep.of("gpu")
        if not gpus:
            return
        tags = self.tags_for("gpu")
        color = self.box("gpu", 45, 275, 505, 100, 12)
        main = gpus[0]
        self.glyph(72, 302, "gpu", 22, tags=tags)
        self.text(94, 302, self._fit(short_gpu(main.name), 26), 17, "bold", tags=tags)
        self.text(62, 328, f"Carte graphique  •  {main.summary}", 12, color=self.muted, tags=tags)
        self.text(62, 353, self.live.get(main.key, ""), 13, "bold", color=color, tags=tags + (f"live:{main.key}",))
        if len(gpus) > 1:
            self.text(230, 353, self._fit("+ " + ", ".join(short_gpu(g.name) for g in gpus[1:]), 24), 11,
                      color=self.muted, tags=tags)
        for cx in (410, 488):  # deux ventilateurs stylisés
            self.create_oval(self.X(cx - 32), self.Y(293), self.X(cx + 32), self.Y(357),
                             outline=_mix(color, self.bg, 0.35), width=2 * self.s, tags=tags)
            for start in (0, 120, 240):
                self.create_arc(self.X(cx - 24), self.Y(301), self.X(cx + 24), self.Y(349), start=start, extent=55,
                                fill=_mix(color, self.bg, 0.55), outline="", tags=tags)

    def _draw_storage(self, rep: HardwareReport) -> None:
        disks = rep.of("storage")
        if not disks:
            return
        tags = self.tags_for("storage")
        self.box("storage", 45, 390, 505, 80, 12)
        shown = disks[:2]
        width = 490 / len(shown)
        color = theme.pick(KIND_COLORS["storage"])
        for i, disk in enumerate(shown):
            x = 52 + i * width
            self.rrect(x, 397, width - 14, 66, 8, fill=_mix(color, self.bg, 0.72), outline="", tags=tags)
            self.glyph(x + 22, 430, "storage", 20, tags=tags)
            chars = int((width - 60) / 8)
            self.text(x + 44, 419, self._fit(disk.summary, chars), 14, "bold", tags=tags)
            self.text(x + 44, 442, self._fit(disk.name, chars + 4), 11, color=self.muted, tags=tags)
        if len(disks) > 2:
            self.text(540, 462, f"+{len(disks) - 2}", 12, "bold", anchor="e", tags=tags)

    def _draw_peripherals(self, rep: HardwareReport) -> None:
        """Rangée sous la carte mère : réseau, audio, écran, ventilation, batterie."""
        kinds = [k for k in ("network", "audio", "display", "cooling", "battery") if rep.of(k)]
        if not kinds:
            return
        y0, h, gap = 560, 130, 8
        left, total = 2, 576
        tw = (total - gap * (len(kinds) - 1)) / len(kinds)
        line = _mix(theme.pick(theme.ACCENT), self.bg, 0.55)
        chars = int((tw - 18) / 5.6)
        gpu = rep.of("gpu")
        for i, kind in enumerate(kinds):
            x = left + i * (tw + gap)
            cx = x + tw / 2
            self.create_line(self.X(cx), self.Y(540), self.X(cx), self.Y(y0), fill=line,
                             width=max(1, 2 * self.s), dash=(4, 3))
            comps = rep.of(kind)
            tags = self.tags_for(kind)
            color = self.box(kind, x, y0, tw, h, 14)
            self.glyph(x + 22, y0 + 26, kind, 20, tags=tags)
            line1 = line2 = ""
            live_key = ""
            if kind == "network":
                connected = next((n for n in comps if "connecté" in n.summary), comps[0])
                title, line1 = "Réseau", connected.summary
                line2 = f"{len(comps)} cartes réseau" if len(comps) > 1 else connected.name
            elif kind == "audio":
                title, line1 = "Audio", comps[0].name
                line2 = f"+ {len(comps) - 1} autre(s)" if len(comps) > 1 else comps[0].summary
            elif kind == "display":
                title, line1 = "Écran", comps[0].name
                line2 = dict(gpu[0].specs).get("Affichage actuel", "") if gpu else ""
                if len(comps) > 1:
                    line2 = f"{len(comps)} écrans"
            elif kind == "cooling":
                title, line1, live_key = "Ventilation", "GPU : temp. • ventil.", "cooling"
                line2 = comps[0].summary
            else:
                title, line1 = "Batterie", comps[0].summary
            self.text(x + 40, y0 + 26, title, 14, "bold", tags=tags)
            self.text(x + 12, y0 + 62, self._fit(line1, chars), 11, tags=tags)
            if live_key:
                self.text(x + 12, y0 + 90, self.live.get(live_key, line2), 13, "bold", color=color,
                          tags=tags + (f"live:{live_key}",))
            elif line2:
                self.text(x + 12, y0 + 90, self._fit(line2, chars), 11, color=self.muted, tags=tags)

    # ------------------------------------------------------------------ interactions
    def _kind_at_cursor(self) -> str | None:
        for tag in self.gettags("current"):
            if tag.startswith("kind:"):
                return tag[5:]
        return None

    def _click(self, _event) -> None:
        kind = self._kind_at_cursor()
        if kind:
            self.selected = kind
            self.redraw()
            self.on_select(kind)

    def _enter(self, _event) -> None:
        kind = self._kind_at_cursor()
        if kind and kind != self.selected:
            color = theme.pick(KIND_COLORS[kind])
            self.itemconfigure(f"shape:{kind}", outline=color, width=2.5 * max(self.s, 0.6))
        self.configure(cursor="hand2")

    def _leave(self, _event) -> None:
        for kind, (outline, width) in self._base_style.items():
            self.itemconfigure(f"shape:{kind}", outline=outline, width=width)
        self.configure(cursor="")


class HardwarePage(Page):
    page_id = "hardware"
    LIVE_MS = 3000

    def __init__(self, master, app):
        super().__init__(master, app)
        self.report: HardwareReport | None = None
        self.selected = "cpu"
        self._after_id = None
        self._live_busy = False

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        head = ctk.CTkFrame(self, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        PageHeader(head, "Mon PC", "La carte de votre ordinateur : de quoi il est fait et à quoi sert chaque pièce.") \
            .pack(side="left")
        neutral_button(head, "Copier la configuration", self._copy, width=10).pack(side="right")
        neutral_button(head, "Actualiser", self.load, width=100).pack(side="right", padx=8)

        TipBox(self, "Cliquez sur un composant du schéma pour savoir à quoi il sert, voir ses caractéristiques "
                     "et nos conseils. Les valeurs colorées (utilisation, températures) se mettent à jour en direct.") \
            .grid(row=1, column=0, sticky="ew", pady=(0, 10))

        body = ctk.CTkFrame(self, fg_color="transparent")
        body.grid(row=2, column=0, sticky="nsew")
        body.grid_columnconfigure(0, weight=3)
        body.grid_columnconfigure(1, weight=2, minsize=330)
        body.grid_rowconfigure(0, weight=1)

        map_card = Card(body)
        map_card.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        self.map = PcMap(map_card, self._select, app.icon_family)
        self.map.pack(fill="both", expand=True, padx=10, pady=10)

        self.detail = ctk.CTkScrollableFrame(body, fg_color=theme.CARD, corner_radius=12, border_width=1,
                                             border_color=theme.CARD_BORDER)
        self.detail.grid(row=0, column=1, sticky="nsew")
        self.detail.grid_columnconfigure(0, weight=1)
        self.load()

    # ------------------------------------------------------------------ données
    def load(self) -> None:
        self.map.report = None
        self.map.redraw()
        self.dispatcher.run(hardware.collect, on_done=self._loaded,
                            on_error=lambda e: self._show_error(str(e)))

    def _loaded(self, report: HardwareReport) -> None:
        self.report = report
        self.map.set_report(report)
        self._render_detail()

    def _show_error(self, message: str) -> None:
        for child in self.detail.winfo_children():
            child.destroy()
        ctk.CTkLabel(self.detail, text=f"Lecture du matériel impossible :\n{message}", font=theme.font(12),
                     text_color=theme.DANGER, wraplength=300, justify="left").grid(row=0, column=0, padx=16, pady=16)

    def _refresh_live(self) -> None:
        self._after_id = self.after(self.LIVE_MS, self._refresh_live)
        if self._live_busy:
            return
        self._live_busy = True

        def done(values: dict[str, str]) -> None:
            self.map.update_live(values)

        self.dispatcher.run(hardware.live_values, on_done=done,
                            on_finally=lambda: setattr(self, "_live_busy", False))

    def on_show(self) -> None:
        if self._after_id is None:
            self._refresh_live()

    def on_hide(self) -> None:
        if self._after_id is not None:
            self.after_cancel(self._after_id)
            self._after_id = None

    def on_theme_change(self) -> None:
        self.map.redraw()
        self._render_detail()

    # ------------------------------------------------------------------ panneau de détails
    def _select(self, kind: str) -> None:
        self.selected = kind
        self._render_detail()

    def _render_detail(self) -> None:
        for child in self.detail.winfo_children():
            child.destroy()
        if self.report is None:
            return
        comps = self.report.of(self.selected)
        if not comps:
            return
        color = KIND_COLORS[self.selected]
        row = 0
        head = ctk.CTkFrame(self.detail, fg_color="transparent")
        head.grid(row=row, column=0, sticky="ew", padx=14, pady=(14, 4))
        if self.app.icon_family:
            ctk.CTkLabel(head, text=GLYPHS[self.selected], font=(self.app.icon_family, 22), text_color=color) \
                .pack(side="left", padx=(0, 10))
        ctk.CTkLabel(head, text=comps[0].title, font=theme.font(20, "bold"), text_color=color).pack(side="left")
        row += 1

        ctk.CTkLabel(self.detail, text="À quoi ça sert ?", font=theme.font(13, "bold"), anchor="w") \
            .grid(row=row, column=0, sticky="w", padx=14, pady=(6, 2))
        row += 1
        ctk.CTkLabel(self.detail, text=comps[0].explanation, font=theme.font(12), text_color=theme.MUTED,
                     anchor="w", justify="left", wraplength=330) \
            .grid(row=row, column=0, sticky="w", padx=14)
        row += 1

        advice = [a for c in comps for a in c.advice]
        seen = set()
        unique_advice = []
        for a in advice:
            if a.text not in seen:
                seen.add(a.text)
                unique_advice.append(a)
        if unique_advice:
            ctk.CTkLabel(self.detail, text="Nos conseils", font=theme.font(13, "bold"), anchor="w") \
                .grid(row=row, column=0, sticky="w", padx=14, pady=(14, 4))
            row += 1
            for a in unique_advice:
                line = ctk.CTkFrame(self.detail, fg_color="transparent")
                line.grid(row=row, column=0, sticky="ew", padx=14, pady=2)
                sym, sym_font = symbol(LEVEL_SYMBOLS.get(a.level, "i"), 11)
                Badge(line, text=sym, width=20, height=20, corner_radius=10,
                             fg_color=LEVEL_COLORS.get(a.level, theme.ACCENT), text_color="#ffffff",
                             font=sym_font).pack(side="left", anchor="n", pady=2)
                ctk.CTkLabel(line, text=a.text, font=theme.font(12), anchor="w", justify="left", wraplength=290) \
                    .pack(side="left", padx=(8, 0), fill="x")
                row += 1

        for comp in comps:
            ctk.CTkLabel(self.detail, text=comp.name, font=theme.font(14, "bold"), anchor="w", justify="left",
                         wraplength=330).grid(row=row, column=0, sticky="w", padx=14, pady=(16, 4))
            row += 1
            grid = ctk.CTkFrame(self.detail, fg_color="transparent")
            grid.grid(row=row, column=0, sticky="ew", padx=14)
            grid.grid_columnconfigure(1, weight=1)
            for r, (key, value) in enumerate(comp.specs):
                ctk.CTkLabel(grid, text=key, font=theme.font(12), text_color=theme.MUTED, anchor="nw",
                             justify="left", wraplength=140).grid(row=r, column=0, sticky="nw", pady=2, padx=(0, 10))
                ctk.CTkLabel(grid, text=value, font=theme.font(12), anchor="w", justify="left",
                             wraplength=190).grid(row=r, column=1, sticky="w", pady=2)
            row += 1

        actions = self._actions_for(self.selected)
        if actions:
            box = ctk.CTkFrame(self.detail, fg_color="transparent")
            box.grid(row=row, column=0, sticky="ew", padx=14, pady=(16, 14))
            for label, callback in actions:
                neutral_button(box, label, callback, width=10).pack(fill="x", pady=3)

    def _actions_for(self, kind: str) -> list[tuple[str, object]]:
        drivers_action = ("Vérifier les pilotes", lambda: self.app.show_page("drivers").start_analysis())
        if kind in ("gpu", "network", "audio", "board"):
            return [drivers_action]
        if kind == "storage":
            return [("Libérer de l'espace (Nettoyage)", lambda: self.app.show_page("cleaner")),
                    ("Trouver les gros fichiers", lambda: self.app.show_page("bigfiles"))]
        if kind == "cooling" and self.report:
            board = self.report.of("board")
            maker = board_maker(dict(board[0].specs).get("Fabricant", "")) if board else None
            if maker and maker.fan_tool:
                from app.core.vendors import board_drivers_search_url

                url = board_drivers_search_url(maker, maker.fan_tool)
                return [(f"Trouver {maker.fan_tool}  ↗", lambda u=url: open_url(u))]
        return []

    def _copy(self) -> None:
        """Copie un résumé texte de la configuration (pratique pour demander de l'aide sur un forum)."""
        if self.report is None:
            return
        lines = ["Configuration du PC (NPone29 Cleaner)", ""]
        for comp in self.report.components:
            lines.append(f"{comp.title} : {comp.name}" + (f" — {comp.summary}" if comp.summary else ""))
        self.clipboard_clear()
        self.clipboard_append("\n".join(lines))
        self.app.notify("Configuration copiée", "Vous pouvez la coller dans un message (Ctrl+V).", "success")
