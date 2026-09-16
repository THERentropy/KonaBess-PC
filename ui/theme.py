"""界面主题：深色配色与 ttk 样式。"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

# -- 配色 -------------------------------------------------------------------

COLORS = {
    "bg": "#191b1f",
    "bg_alt": "#202329",
    "panel": "#24272e",
    "panel_alt": "#2a2e36",
    "border": "#363b45",
    "border_light": "#454b57",
    "fg": "#e8eaee",
    "fg_dim": "#9aa1ad",
    "fg_muted": "#6b7280",
    "accent": "#e8503a",
    "accent_hover": "#ff6a52",
    "accent_dim": "#8f3427",
    "ok": "#4ec98a",
    "warn": "#e0b355",
    "error": "#ef6b6b",
    "info": "#5aa9f0",
    "select_bg": "#33415c",
    "entry_bg": "#1c1f24",
    "chart_bg": "#17191d",
    "chart_grid": "#2a2e36",
    "chart_line": "#e8503a",
    "chart_point": "#ffb4a6",
    "chart_text": "#9aa1ad",
}

FONT_FAMILY = "Microsoft YaHei UI"
FONT_FALLBACK = ("Microsoft YaHei", "Segoe UI", "TkDefaultFont")

#: 高 DPI 缩放因子（1.0 = 100%，1.25 = 125%），由 main.py 启动时设置。
SCALE = 1.0


def set_scale(scale: float) -> None:
    global SCALE
    SCALE = max(1.0, float(scale))


def px(value: float) -> int:
    """把设计尺寸（100% 缩放下的像素）换算为当前 DPI 下的像素。"""
    return max(1, int(round(value * SCALE)))


def font(size: int = 10, bold: bool = False) -> tuple:
    return (FONT_FAMILY, size, "bold") if bold else (FONT_FAMILY, size)


def mono_font(size: int = 10) -> tuple:
    return ("Consolas", size)


# -- 样式 -------------------------------------------------------------------

def apply_theme(root: tk.Misc) -> ttk.Style:
    """把深色主题应用到整个窗口。"""
    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:  # pragma: no cover
        pass

    c = COLORS
    root.option_add("*Font", font(10))
    root.option_add("*selectBackground", c["select_bg"])
    root.option_add("*selectForeground", c["fg"])

    style.configure(".", background=c["bg"], foreground=c["fg"],
                    fieldbackground=c["entry_bg"], bordercolor=c["border"],
                    lightcolor=c["border"], darkcolor=c["border"],
                    focuscolor=c["accent"])

    style.configure("TFrame", background=c["bg"])
    style.configure("Panel.TFrame", background=c["panel"])
    style.configure("Toolbar.TFrame", background=c["bg_alt"])
    style.configure("Info.TFrame", background=c["panel"])

    style.configure("TLabel", background=c["bg"], foreground=c["fg"])
    style.configure("Panel.TLabel", background=c["panel"], foreground=c["fg"])
    style.configure("Dim.TLabel", background=c["panel"], foreground=c["fg_dim"])
    style.configure("Toolbar.TLabel", background=c["bg_alt"], foreground=c["fg_dim"])
    style.configure("Title.TLabel", background=c["bg"], foreground=c["fg"], font=font(13, True))
    style.configure("Section.TLabel", background=c["panel"], foreground=c["fg"], font=font(10, True))
    style.configure("Accent.TLabel", background=c["panel"], foreground=c["accent"], font=font(10, True))
    style.configure("Status.TLabel", background=c["bg_alt"], foreground=c["fg_dim"])

    # 按钮
    style.configure("TButton", background=c["panel_alt"], foreground=c["fg"],
                    bordercolor=c["border"], lightcolor=c["panel_alt"],
                    darkcolor=c["panel_alt"], padding=(10, 5), relief="flat")
    style.map("TButton",
              background=[("pressed", c["border"]), ("active", c["border_light"]),
                          ("disabled", c["panel"])],
              foreground=[("disabled", c["fg_muted"])])
    style.configure("Accent.TButton", background=c["accent"], foreground="#ffffff")
    style.map("Accent.TButton",
              background=[("pressed", c["accent_dim"]), ("active", c["accent_hover"]),
                          ("disabled", c["panel_alt"])],
              foreground=[("disabled", c["fg_muted"])])

    # 输入
    style.configure("TEntry", fieldbackground=c["entry_bg"], foreground=c["fg"],
                    insertcolor=c["fg"], bordercolor=c["border"], padding=4)
    style.configure("TCombobox", fieldbackground=c["entry_bg"], foreground=c["fg"],
                    background=c["panel_alt"], arrowcolor=c["fg"],
                    bordercolor=c["border"], padding=3)
    style.map("TCombobox",
              fieldbackground=[("readonly", c["entry_bg"])],
              foreground=[("readonly", c["fg"])])

    # 标签页（tab 行右侧空白区域使用与标签条一致的底色，避免出现突兀色块）
    style.configure("TNotebook", background=c["bg_alt"], bordercolor=c["bg_alt"],
                    lightcolor=c["bg_alt"], darkcolor=c["bg_alt"],
                    troughcolor=c["bg_alt"], tabmargins=(0, 0, 0, 0))
    style.configure("TNotebook.Tab", background=c["bg_alt"], foreground=c["fg_dim"],
                    padding=(16, 7), bordercolor=c["border"], lightcolor=c["bg_alt"],
                    darkcolor=c["bg_alt"])
    style.map("TNotebook.Tab",
              background=[("selected", c["panel"])],
              foreground=[("selected", c["fg"])])

    # 表格
    style.configure("Treeview", background=c["panel"], fieldbackground=c["panel"],
                    foreground=c["fg"], bordercolor=c["border"], rowheight=px(26),
                    lightcolor=c["panel"], darkcolor=c["panel"], relief="flat")
    style.configure("Treeview.Heading", background=c["panel_alt"], foreground=c["fg_dim"],
                    relief="flat", padding=(6, 6), font=font(9, True))
    style.map("Treeview.Heading", background=[("active", c["border"])])
    style.map("Treeview",
              background=[("selected", c["select_bg"])],
              foreground=[("selected", "#ffffff")])

    # 滚动条
    style.configure("Vertical.TScrollbar", background=c["panel_alt"], troughcolor=c["bg"],
                    bordercolor=c["bg"], arrowcolor=c["fg_dim"], relief="flat")
    style.configure("Horizontal.TScrollbar", background=c["panel_alt"], troughcolor=c["bg"],
                    bordercolor=c["bg"], arrowcolor=c["fg_dim"], relief="flat")

    # 滑块
    style.configure("TScale", background=c["panel"], troughcolor=c["entry_bg"],
                    bordercolor=c["border"], lightcolor=c["accent"], darkcolor=c["accent"])

    # 分隔线
    style.configure("TSeparator", background=c["border"])

    # 列表
    style.configure("TListbox", background=c["entry_bg"], foreground=c["fg"],
                    bordercolor=c["border"], selectbackground=c["select_bg"])

    return style


def style_listbox(listbox: tk.Listbox) -> None:
    c = COLORS
    listbox.configure(
        background=c["entry_bg"], foreground=c["fg"],
        selectbackground=c["select_bg"], selectforeground="#ffffff",
        highlightthickness=1, highlightbackground=c["border"], highlightcolor=c["accent"],
        borderwidth=0, relief="flat", activestyle="none",
        font=mono_font(10),
    )


def style_canvas(canvas: tk.Canvas, bg_key: str = "chart_bg") -> None:
    canvas.configure(background=COLORS[bg_key], highlightthickness=0, borderwidth=0)
