"""Couleurs, polices et style des tableaux (ttk) assortis au thème clair/sombre."""

from __future__ import annotations

import tkinter.font as tkfont
from tkinter import ttk

import customtkinter as ctk

FONT_FAMILY = "Segoe UI"

# Paires (clair, sombre) comprises par CustomTkinter.
ACCENT = ("#2563eb", "#3b82f6")
ACCENT_HOVER = ("#1d4ed8", "#2563eb")
SUCCESS = ("#16a34a", "#22a55a")
SUCCESS_HOVER = ("#15803d", "#1a8a4a")
DANGER = ("#dc2626", "#d64541")
DANGER_HOVER = ("#b91c1c", "#b83532")
WARNING = ("#c2410c", "#f59e0b")
MUTED = ("gray40", "gray62")
CARD = ("#ffffff", "#232731")
CARD_BORDER = ("#e3e6ec", "#2e3340")
WINDOW_BG = ("#f3f5f9", "#181b22")
SIDEBAR_BG = ("#e8ecf3", "#11141a")
SIDEBAR_HOVER = ("#d7deea", "#1e2330")
SIDEBAR_ACTIVE = ("#cfd9ee", "#252c3d")
NEUTRAL_BTN = ("#e3e7ee", "#2c3240")
NEUTRAL_BTN_HOVER = ("#d3d9e3", "#363d4e")
NEUTRAL_TEXT = ("#1f2937", "#e5e7eb")

TAG_COLORS = {
    "warn": ("#c2410c", "#f59e0b"),
    "ok": ("#15803d", "#4ade80"),
    "error": ("#b91c1c", "#f87171"),
    "muted": ("#6b7280", "#8b93a3"),
    "info": ("#1d4ed8", "#60a5fa"),
}


def font(size: int = 13, weight: str = "normal") -> ctk.CTkFont:
    return ctk.CTkFont(family=FONT_FAMILY, size=size, weight=weight)


def is_dark() -> bool:
    return ctk.get_appearance_mode().lower() == "dark"


def pick(pair: tuple[str, str]) -> str:
    return pair[1] if is_dark() else pair[0]


def apply_treeview_style(root) -> None:
    """Configure le style global « NP.Treeview » selon le mode clair/sombre courant."""
    dark = is_dark()
    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except Exception:
        pass
    bg = "#232731" if dark else "#ffffff"
    alt = "#262b36" if dark else "#f7f8fb"
    fg = "#e5e7eb" if dark else "#1f2937"
    head_bg = "#2c3240" if dark else "#eef1f6"
    head_hover = "#363d4e" if dark else "#e1e6ef"
    selected = "#2f4f8f" if dark else "#cfdcf7"
    selected_fg = "#ffffff" if dark else "#111827"

    body_font = tkfont.Font(root=root, family=FONT_FAMILY, size=10)
    row_height = body_font.metrics("linespace") + 12

    style.configure("NP.Treeview", background=bg, fieldbackground=bg, foreground=fg,
                    rowheight=row_height, borderwidth=0, relief="flat", font=(FONT_FAMILY, 10))
    style.map("NP.Treeview", background=[("selected", selected)], foreground=[("selected", selected_fg)])
    style.configure("NP.Treeview.Heading", background=head_bg, foreground=fg, relief="flat",
                    borderwidth=0, font=(FONT_FAMILY, 10, "bold"), padding=(8, 6))
    style.map("NP.Treeview.Heading", background=[("active", head_hover)])
    style.layout("NP.Treeview", [("NP.Treeview.treearea", {"sticky": "nswe"})])
    style.configure("NP.Treeview", indent=0)
    # Les tableaux existants recolorent leurs lignes alternées et leurs tags.
    from app.ui.widgets import Table

    Table.refresh_all(alt)
