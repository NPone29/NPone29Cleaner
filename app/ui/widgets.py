"""Composants d'interface réutilisables : en-tête de page, cartes, tableau triable…"""

from __future__ import annotations

import tkinter as tk
import tkinter.font as tkfont
import weakref
from tkinter import messagebox, ttk
from typing import Any, Callable, Iterable

import customtkinter as ctk

from app.ui import theme

CHECKED = "☑"
UNCHECKED = "☐"


def neutral_button(master, text: str, command: Callable | None = None, width: int = 140, **kw) -> ctk.CTkButton:
    return ctk.CTkButton(master, text=text, command=command, width=width, height=34,
                         fg_color=theme.NEUTRAL_BTN, hover_color=theme.NEUTRAL_BTN_HOVER,
                         text_color=theme.NEUTRAL_TEXT, font=theme.font(13), **kw)


def primary_button(master, text: str, command: Callable | None = None, width: int = 160, **kw) -> ctk.CTkButton:
    return ctk.CTkButton(master, text=text, command=command, width=width, height=36,
                         fg_color=theme.ACCENT, hover_color=theme.ACCENT_HOVER,
                         font=theme.font(13, "bold"), **kw)


def success_button(master, text: str, command: Callable | None = None, width: int = 160, **kw) -> ctk.CTkButton:
    return ctk.CTkButton(master, text=text, command=command, width=width, height=36,
                         fg_color=theme.SUCCESS, hover_color=theme.SUCCESS_HOVER,
                         font=theme.font(13, "bold"), **kw)


def danger_button(master, text: str, command: Callable | None = None, width: int = 140, **kw) -> ctk.CTkButton:
    return ctk.CTkButton(master, text=text, command=command, width=width, height=34,
                         fg_color=theme.DANGER, hover_color=theme.DANGER_HOVER,
                         font=theme.font(13), **kw)


class Card(ctk.CTkFrame):
    def __init__(self, master, **kw):
        kw.setdefault("fg_color", theme.CARD)
        kw.setdefault("corner_radius", 12)
        kw.setdefault("border_width", 1)
        kw.setdefault("border_color", theme.CARD_BORDER)
        super().__init__(master, **kw)


class PageHeader(ctk.CTkFrame):
    def __init__(self, master, title: str, subtitle: str = ""):
        super().__init__(master, fg_color="transparent")
        ctk.CTkLabel(self, text=title, font=theme.font(24, "bold"), anchor="w").pack(anchor="w")
        if subtitle:
            ctk.CTkLabel(self, text=subtitle, font=theme.font(13), text_color=theme.MUTED,
                         anchor="w", justify="left", wraplength=820).pack(anchor="w", pady=(2, 0))


class StatusLine(ctk.CTkFrame):
    """Barre de progression + texte d'état, en bas d'une page."""

    def __init__(self, master):
        super().__init__(master, fg_color="transparent")
        self.label = ctk.CTkLabel(self, text="", font=theme.font(12), text_color=theme.MUTED, anchor="w")
        self.label.pack(side="left", fill="x", expand=True)
        self.bar = ctk.CTkProgressBar(self, width=220, height=8)
        self.bar.set(0)
        self._busy = False

    def set_text(self, text: str) -> None:
        self.label.configure(text=text)

    def busy(self, text: str = "") -> None:
        if text:
            self.set_text(text)
        if not self._busy:
            self._busy = True
            self.bar.pack(side="right", padx=(12, 0))
            self.bar.configure(mode="indeterminate")
            self.bar.start()

    def progress(self, fraction: float, text: str = "") -> None:
        if text:
            self.set_text(text)
        if self._busy:
            self.bar.stop()
        else:
            self.bar.pack(side="right", padx=(12, 0))
        self._busy = True
        self.bar.configure(mode="determinate")
        self.bar.set(max(0.0, min(1.0, fraction)))

    def idle(self, text: str | None = None) -> None:
        if text is not None:
            self.set_text(text)
        if self._busy:
            self.bar.stop()
            self.bar.pack_forget()
            self._busy = False


class Table(ctk.CTkFrame):
    """Tableau (ttk.Treeview) stylé, triable par clic sur l'en-tête, avec cases à cocher optionnelles.

    `columns` : liste de (clé, titre, largeur, alignement « w »/« e »/« center », extensible).
    """

    _instances: "weakref.WeakSet[Table]" = weakref.WeakSet()

    def __init__(self, master, columns: list[tuple[str, str, int, str, bool]], checkable: bool = False,
                 selectmode: str = "extended", on_select: Callable[[], None] | None = None,
                 on_double_click: Callable[[str], None] | None = None, height: int = 12):
        super().__init__(master, fg_color=theme.CARD, corner_radius=10, border_width=1,
                         border_color=theme.CARD_BORDER)
        self.checkable = checkable
        self._columns = columns
        self._keys = (["__check"] if checkable else []) + [c[0] for c in columns]
        self._rows: dict[str, dict[str, Any]] = {}
        self._order: list[str] = []
        self._checked: set[str] = set()
        self._sort_key: str | None = None
        self._sort_reverse = False
        self._on_check_change: Callable[[], None] | None = None

        inner = tk.Frame(self, bd=0, highlightthickness=0)
        inner.pack(fill="both", expand=True, padx=2, pady=2)
        self.tree = ttk.Treeview(inner, columns=self._keys, show="headings", style="NP.Treeview",
                                 selectmode=selectmode, height=height)
        scroll = ctk.CTkScrollbar(inner, command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self._inner = inner

        if checkable:
            self.tree.heading("__check", text=CHECKED, command=self._toggle_all)
            self.tree.column("__check", width=38, minwidth=38, stretch=False, anchor="center")
        for key, title, width, anchor, stretch in columns:
            self.tree.heading(key, text=title, anchor=anchor, command=lambda k=key: self.sort_by(k))
            self.tree.column(key, width=width, minwidth=40, anchor=anchor, stretch=False)
        # Les largeurs sont recalculées à chaque redimensionnement pour toujours tenir dans le tableau.
        self.tree.bind("<Configure>", self._fit_columns, add="+")

        if checkable:
            self.tree.bind("<Button-1>", self._on_click, add="+")
            self.tree.bind("<space>", self._on_space, add="+")
        if on_select:
            self.tree.bind("<<TreeviewSelect>>", lambda _e: on_select(), add="+")
        if on_double_click:
            def _dbl(event):
                iid = self.tree.identify_row(event.y)
                if iid:
                    on_double_click(iid)
            self.tree.bind("<Double-1>", _dbl, add="+")

        Table._instances.add(self)
        self.refresh_colors()

    def _fit_columns(self, event=None) -> None:
        """Répartit la largeur disponible : colonnes fixes à leur taille, le reste aux colonnes extensibles."""
        total = (event.width if event else self.tree.winfo_width()) - 2
        if total <= 50:
            return
        if self.checkable:
            total -= 38
        fixed = [c for c in self._columns if not c[4]]
        flexible = [c for c in self._columns if c[4]]
        fixed_sum = sum(c[2] for c in fixed)
        flex_sum = sum(c[2] for c in flexible) or 1
        # Si la place manque, toutes les colonnes rétrécissent proportionnellement.
        scale = min(1.0, total / max(fixed_sum + 60 * len(flexible), 1))
        remaining = total - int(fixed_sum * scale)
        for key, _title, width, _anchor, stretch in self._columns:
            if stretch:
                new_width = max(60, int(remaining * width / flex_sum))
            else:
                new_width = max(40, int(width * scale))
            self.tree.column(key, width=new_width)

    # ------------------------------------------------------------------ couleurs
    @classmethod
    def refresh_all(cls, alt_color: str | None = None) -> None:
        for table in list(cls._instances):
            try:
                table.refresh_colors(alt_color)
            except tk.TclError:
                pass

    def refresh_colors(self, alt_color: str | None = None) -> None:
        dark = theme.is_dark()
        alt = alt_color or ("#262b36" if dark else "#f7f8fb")
        self._inner.configure(bg="#232731" if dark else "#ffffff")
        self.tree.tag_configure("odd", background=alt)
        for tag, pair in theme.TAG_COLORS.items():
            self.tree.tag_configure(tag, foreground=pair[1] if dark else pair[0])

    # ------------------------------------------------------------------ données
    def set_rows(self, rows: Iterable[tuple[str, list[Any], list[Any] | None, tuple[str, ...]]],
                 checked: Iterable[str] | None = None) -> None:
        """rows : (iid, valeurs affichées, valeurs de tri ou None, tags)."""
        self._rows.clear()
        self._order.clear()
        for iid, values, sort_values, tags in rows:
            self._rows[iid] = {"values": list(values), "sort": list(sort_values or values), "tags": tuple(tags)}
            self._order.append(iid)
        self._checked = set(checked or []) & set(self._rows)
        if self._sort_key:
            self._apply_sort()
        self._render()

    def update_row(self, iid: str, values: list[Any] | None = None, sort_values: list[Any] | None = None,
                   tags: tuple[str, ...] | None = None) -> None:
        row = self._rows.get(iid)
        if row is None:
            return
        if values is not None:
            row["values"] = list(values)
            row["sort"] = list(sort_values or values)
        if tags is not None:
            row["tags"] = tuple(tags)
        if self.tree.exists(iid):
            idx = self.tree.index(iid)
            self.tree.item(iid, values=self._display(iid), tags=self._tags(iid, idx))

    def _display(self, iid: str) -> list[Any]:
        values = self._rows[iid]["values"]
        if self.checkable:
            return [CHECKED if iid in self._checked else UNCHECKED, *values]
        return values

    def _tags(self, iid: str, index: int) -> tuple[str, ...]:
        tags = self._rows[iid]["tags"]
        return tags + (("odd",) if index % 2 else ())

    def _render(self) -> None:
        selection = set(self.tree.selection())
        self.tree.delete(*self.tree.get_children())
        for index, iid in enumerate(self._order):
            self.tree.insert("", "end", iid=iid, values=self._display(iid), tags=self._tags(iid, index))
        keep = [i for i in selection if self.tree.exists(i)]
        if keep:
            self.tree.selection_set(keep)

    def clear(self) -> None:
        self.set_rows([])

    def __len__(self) -> int:
        return len(self._rows)

    # ------------------------------------------------------------------ tri
    def sort_by(self, key: str) -> None:
        if self._sort_key == key:
            self._sort_reverse = not self._sort_reverse
        else:
            self._sort_key, self._sort_reverse = key, False
        self._apply_sort()
        self._render()
        for k, title, *_ in self._columns:
            arrow = (" ▼" if self._sort_reverse else " ▲") if k == key else ""
            self.tree.heading(k, text=title + arrow)

    def _apply_sort(self) -> None:
        keys = [c[0] for c in self._columns]
        if self._sort_key not in keys:
            return
        col = keys.index(self._sort_key)

        def sort_value(iid: str):
            value = self._rows[iid]["sort"][col]
            if value is None:
                return (1, "")
            if isinstance(value, str):
                return (0, value.lower())
            return (0, value)

        try:
            self._order.sort(key=sort_value, reverse=self._sort_reverse)
        except TypeError:
            self._order.sort(key=lambda i: str(self._rows[i]["sort"][col]).lower(), reverse=self._sort_reverse)

    # ------------------------------------------------------------------ sélection / cases
    def selected(self) -> list[str]:
        return list(self.tree.selection())

    def checked(self) -> list[str]:
        return [iid for iid in self._order if iid in self._checked]

    def set_checked(self, iids: Iterable[str], value: bool = True) -> None:
        for iid in iids:
            if iid not in self._rows:
                continue
            if value:
                self._checked.add(iid)
            else:
                self._checked.discard(iid)
            if self.tree.exists(iid):
                self.tree.set(iid, "__check", CHECKED if value else UNCHECKED)
        if self._on_check_change:
            self._on_check_change()

    def set_all_checked(self, value: bool) -> None:
        self.set_checked(list(self._rows), value)

    def on_check_change(self, callback: Callable[[], None]) -> None:
        self._on_check_change = callback

    def _toggle_all(self) -> None:
        self.set_all_checked(len(self._checked) < len(self._rows))

    def _on_click(self, event) -> str | None:
        if self.tree.identify_region(event.x, event.y) != "cell":
            return None
        if self.tree.identify_column(event.x) != "#1":
            return None
        iid = self.tree.identify_row(event.y)
        if iid:
            self.set_checked([iid], iid not in self._checked)
            return "break"
        return None

    def _on_space(self, _event) -> str:
        sel = self.selected()
        if sel:
            new_value = not all(i in self._checked for i in sel)
            self.set_checked(sel, new_value)
        return "break"

    def row_values(self, iid: str) -> list[Any]:
        return list(self._rows.get(iid, {}).get("values", []))


def ask_yes_no(parent, title: str, message: str) -> bool:
    return messagebox.askyesno(title, message, parent=parent.winfo_toplevel())


def show_info(parent, title: str, message: str) -> None:
    messagebox.showinfo(title, message, parent=parent.winfo_toplevel())


def show_error(parent, title: str, message: str) -> None:
    messagebox.showerror(title, message, parent=parent.winfo_toplevel())


def show_warning(parent, title: str, message: str) -> None:
    messagebox.showwarning(title, message, parent=parent.winfo_toplevel())


# --------------------------------------------------------------------------- aide à l'utilisateur

LEVEL_COLORS = {
    "success": theme.SUCCESS, "ok": theme.SUCCESS, "info": theme.ACCENT,
    "warning": theme.WARNING, "warn": theme.WARNING, "error": theme.DANGER,
}
LEVEL_SYMBOLS = {"success": "✓", "ok": "✓", "info": "i", "warning": "!", "warn": "!", "error": "✕"}

_ICON_FAMILY: str | None = None
# Le « ✓ » de Segoe UI est trop large pour tenir dans un cercle : on prend celui de la police d'icônes.
_ICON_SYMBOLS = {"✓": "\ue73e", "✕": "\ue711"}


def icon_family() -> str:
    global _ICON_FAMILY
    if _ICON_FAMILY is None:
        try:
            families = set(tkfont.families())
        except tk.TclError:
            families = set()
        _ICON_FAMILY = next((f for f in ("Segoe Fluent Icons", "Segoe MDL2 Assets") if f in families), "")
    return _ICON_FAMILY


class Badge(ctk.CTkLabel):
    """Pastille ronde (numéro d'étape, ✓, !, i…).

    CustomTkinter ajoute de chaque côté du texte une marge égale au rayon des coins,
    ce qui transforme un cercle en ovale : on la supprime.
    """

    def _create_grid(self):
        super()._create_grid()
        self._label.grid_configure(padx=0)


def symbol(sym: str, size: int):
    """Texte + police pour afficher un symbole centré dans une pastille ronde."""
    family = icon_family()
    if sym in _ICON_SYMBOLS and family:
        return _ICON_SYMBOLS[sym], (family, max(8, size - 2))
    return sym, theme.font(size, "bold")


class Stepper(ctk.CTkFrame):
    """Indicateur d'étapes : ① Analyser — ② Choisir — ③ Nettoyer — ④ Terminé."""

    def __init__(self, master, steps: list[str]):
        super().__init__(master, fg_color="transparent")
        self._items: list[tuple[ctk.CTkLabel, ctk.CTkLabel]] = []
        self._lines: list[ctk.CTkFrame] = []
        for i, label in enumerate(steps):
            box = ctk.CTkFrame(self, fg_color="transparent")
            box.grid(row=0, column=i * 2, sticky="w")
            circle = Badge(box, text=str(i + 1), width=26, height=26, corner_radius=13,
                                  font=theme.font(12, "bold"))
            circle.pack(side="left")
            text = ctk.CTkLabel(box, text=label, font=theme.font(12))
            text.pack(side="left", padx=(6, 0))
            self._items.append((circle, text))
            if i < len(steps) - 1:
                line = ctk.CTkFrame(self, height=2, width=34, fg_color=theme.CARD_BORDER)
                line.grid(row=0, column=i * 2 + 1, padx=8)
                self._lines.append(line)
        self.current = 0
        self.set(0)

    def set(self, current: int) -> None:
        """`current` = index de l'étape en cours ; len(steps) = tout est terminé."""
        self.current = current
        for i, (circle, text) in enumerate(self._items):
            if i < current:
                text_sym, font = symbol("✓", 12)
                circle.configure(text=text_sym, font=font, fg_color=theme.SUCCESS, text_color="#ffffff")
                text.configure(text_color=theme.NEUTRAL_TEXT, font=theme.font(12))
            elif i == current:
                circle.configure(text=str(i + 1), font=theme.font(12, "bold"), fg_color=theme.ACCENT,
                                 text_color="#ffffff")
                text.configure(text_color=theme.NEUTRAL_TEXT, font=theme.font(12, "bold"))
            else:
                circle.configure(text=str(i + 1), font=theme.font(12, "bold"), fg_color=theme.NEUTRAL_BTN,
                                 text_color=theme.MUTED)
                text.configure(text_color=theme.MUTED, font=theme.font(12))
        for i, line in enumerate(self._lines):
            line.configure(fg_color=theme.SUCCESS if i < current else theme.CARD_BORDER)


class TipBox(ctk.CTkFrame):
    """Encadré d'explication pour guider l'utilisateur."""

    def __init__(self, master, text: str, level: str = "info"):
        super().__init__(master, fg_color=("#eef4ff", "#1c2638") if level == "info" else ("#fff4e5", "#33291a"),
                         corner_radius=10)
        color = LEVEL_COLORS.get(level, theme.ACCENT)
        Badge(self, text=LEVEL_SYMBOLS.get(level, "i"), width=22, height=22, corner_radius=11,
                     fg_color=color, text_color="#ffffff", font=theme.font(12, "bold")) \
            .pack(side="left", padx=(12, 8), pady=10, anchor="n")
        self.label = ctk.CTkLabel(self, text=text, font=theme.font(12), anchor="w", justify="left", wraplength=780)
        self.label.pack(side="left", fill="x", expand=True, padx=(0, 12), pady=9)
        self.bind("<Configure>", self._rewrap, add="+")

    def _rewrap(self, event) -> None:
        scaling = ctk.ScalingTracker.get_widget_scaling(self)
        self.label.configure(wraplength=max(200, int(event.width / scaling) - 70))

    def set_text(self, text: str) -> None:
        self.label.configure(text=text)


class Toast(ctk.CTkFrame):
    """Notification affichée quelques secondes en haut à droite de l'application."""

    DURATION_MS = 7000

    def __init__(self, master, title: str, message: str, level: str = "success",
                 action: tuple[str, Callable[[], None]] | None = None,
                 on_close: Callable[["Toast"], None] | None = None):
        super().__init__(master, fg_color=theme.CARD, corner_radius=12, border_width=2,
                         border_color=LEVEL_COLORS.get(level, theme.ACCENT))
        self._on_close = on_close
        self._closed = False
        color = LEVEL_COLORS.get(level, theme.ACCENT)
        sym, sym_font = symbol(LEVEL_SYMBOLS.get(level, "i"), 14)
        Badge(self, text=sym, width=28, height=28, corner_radius=14, fg_color=color,
                     text_color="#ffffff", font=sym_font) \
            .grid(row=0, column=0, rowspan=3, padx=(14, 10), pady=14, sticky="n")
        ctk.CTkLabel(self, text=title, font=theme.font(14, "bold"), anchor="w", justify="left",
                     wraplength=260).grid(row=0, column=1, sticky="w", pady=(12, 0))
        ctk.CTkLabel(self, text=message, font=theme.font(12), text_color=theme.MUTED, anchor="w", justify="left",
                     wraplength=270).grid(row=1, column=1, sticky="w", pady=(2, 4 if action else 12))
        if action:
            ctk.CTkButton(self, text=action[0], height=28, width=10, font=theme.font(12, "bold"),
                          fg_color=theme.ACCENT, hover_color=theme.ACCENT_HOVER,
                          command=lambda: (action[1](), self.close())).grid(row=2, column=1, sticky="w", pady=(0, 12))
        ctk.CTkButton(self, text="✕", width=26, height=26, fg_color="transparent", hover_color=theme.NEUTRAL_BTN,
                      text_color=theme.MUTED, command=self.close).grid(row=0, column=2, padx=8, pady=8, sticky="ne")
        self.after(self.DURATION_MS, self.close)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._on_close:
            self._on_close(self)
        try:
            self.destroy()
        except tk.TclError:
            pass


class ResultDialog(ctk.CTkToplevel):
    """Fenêtre de bilan : résultat, chiffres clés, conseils et prochaines étapes proposées."""

    def __init__(self, parent, title: str, headline: str, stats: list[tuple[str, str]] | None = None,
                 tips: list[str] | None = None, actions: list[tuple[str, Callable[[], None]]] | None = None,
                 level: str = "success", close_text: str = "Fermer", actions_title: str = "Et maintenant ?"):
        super().__init__(parent)
        self.withdraw()
        self.title(title)
        self.configure(fg_color=theme.WINDOW_BG)
        self.resizable(False, False)
        self.transient(parent.winfo_toplevel())

        body = ctk.CTkFrame(self, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=28, pady=24)
        color = LEVEL_COLORS.get(level, theme.SUCCESS)
        sym, sym_font = symbol(LEVEL_SYMBOLS.get(level, "✓"), 26)
        Badge(body, text=sym, width=56, height=56, corner_radius=28, fg_color=color,
                     text_color="#ffffff", font=sym_font).pack()
        ctk.CTkLabel(body, text=headline, font=theme.font(18, "bold"), wraplength=440, justify="center") \
            .pack(pady=(12, 14))

        if stats:
            card = Card(body)
            card.pack(fill="x", pady=(0, 12))
            card.grid_columnconfigure(1, weight=1)
            last = len(stats) - 1
            for r, (label, value) in enumerate(stats):
                pad = (10 if r == 0 else 3, 10 if r == last else 3)
                ctk.CTkLabel(card, text=label, font=theme.font(13), text_color=theme.MUTED, anchor="w") \
                    .grid(row=r, column=0, sticky="w", padx=(16, 12), pady=pad)
                ctk.CTkLabel(card, text=value, font=theme.font(13, "bold"), anchor="e") \
                    .grid(row=r, column=1, sticky="e", padx=(12, 16), pady=pad)
        for tip in tips or []:
            ctk.CTkLabel(body, text=f"•  {tip}", font=theme.font(12), text_color=theme.MUTED, anchor="w",
                         justify="left", wraplength=440).pack(fill="x", pady=2)

        if actions:
            ctk.CTkLabel(body, text=actions_title, font=theme.font(14, "bold"), anchor="w") \
                .pack(fill="x", pady=(14, 6))
            for label, callback in actions:
                ctk.CTkButton(body, text=label, height=34, font=theme.font(13), anchor="w",
                              fg_color=theme.NEUTRAL_BTN, hover_color=theme.NEUTRAL_BTN_HOVER,
                              text_color=theme.NEUTRAL_TEXT,
                              command=lambda cb=callback: (self.destroy(), cb())).pack(fill="x", pady=3)
        ctk.CTkButton(body, text=close_text, height=36, font=theme.font(13, "bold"), fg_color=theme.ACCENT,
                      hover_color=theme.ACCENT_HOVER, command=self.destroy).pack(fill="x", pady=(16, 0))

        self.update_idletasks()
        top = parent.winfo_toplevel()
        x = top.winfo_rootx() + (top.winfo_width() - self.winfo_reqwidth()) // 2
        y = top.winfo_rooty() + (top.winfo_height() - self.winfo_reqheight()) // 3
        self.geometry(f"+{max(0, x)}+{max(0, y)}")
        self.deiconify()
        self.after(250, self._finish)

    def _finish(self) -> None:
        from app.utils.paths import resource_path

        icon = resource_path("assets/icon.ico")
        if icon.exists():
            try:
                self.iconbitmap(str(icon))
            except Exception:
                pass
        try:
            self.grab_set()
            self.focus_force()
        except tk.TclError:
            pass
