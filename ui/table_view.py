"""频率表编辑面板（单个 speed bin 的等级列表）。"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Callable, Optional

from konabess.gpu_table import format_freq_hz

from . import theme
from .dialogs import FreqDialog, NumberDialog, VoltageDialog

#: (列键, 标题, 宽度, 对齐)
COLUMNS = [
    ("index", "#", 46, "center"),
    ("freq", "频率", 110, "e"),
    ("freq_hz", "频率 (Hz)", 120, "e"),
    ("volt", "电压档位", 230, "w"),
    ("bus_min", "bus-min", 78, "center"),
    ("bus_max", "bus-max", 78, "center"),
    ("bus_freq", "bus-freq", 78, "center"),
]


class LevelTablePanel(ttk.Frame):
    """一个 bin 的等级编辑表格。"""

    def __init__(self, master: tk.Misc, app, bin_index: int):
        super().__init__(master)
        self.app = app
        self.bin_index = bin_index
        self._row_map: dict[str, int] = {}
        self._build()

    # -- 构建 ------------------------------------------------------------

    def _build(self) -> None:
        c = theme.COLORS

        toolbar = ttk.Frame(self, style="Panel.TFrame", padding=(10, 8))
        toolbar.pack(fill="x")
        self.title_label = ttk.Label(toolbar, text="", style="Section.TLabel")
        self.title_label.pack(side="left")
        self.stats_label = ttk.Label(toolbar, text="", style="Dim.TLabel")
        self.stats_label.pack(side="left", padx=(14, 0))

        ttk.Button(toolbar, text="添加等级", command=self._add_level).pack(side="right", padx=(6, 0))
        ttk.Button(toolbar, text="复制选中", command=self._duplicate_level).pack(side="right", padx=(6, 0))
        ttk.Button(toolbar, text="删除选中", command=self._delete_level).pack(side="right", padx=(6, 0))

        hint = ttk.Label(self, text="双击单元格编辑 · 右键更多操作 · 电压建议使用预设档位",
                         style="Dim.TLabel", padding=(12, 4))
        hint.pack(fill="x")

        table_frame = ttk.Frame(self, padding=(10, 0, 10, 10))
        table_frame.pack(fill="both", expand=True)

        self.tree = ttk.Treeview(table_frame, columns=[col[0] for col in COLUMNS],
                                 show="headings", selectmode="browse")
        for key, title, width, anchor in COLUMNS:
            self.tree.heading(key, text=title)
            self.tree.column(key, width=theme.px(width), minwidth=theme.px(width // 2),
                             anchor=anchor, stretch=(key in ("volt", "freq")))

        scroll = ttk.Scrollbar(table_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

        self.tree.tag_configure("odd", background=c["panel"])
        self.tree.tag_configure("even", background=c["panel_alt"])
        self.tree.tag_configure("placeholder", foreground=c["fg_muted"])
        self.tree.tag_configure("top", foreground=c["accent"])

        self.tree.bind("<Double-Button-1>", self._on_double_click)
        self.tree.bind("<Button-3>", self._on_right_click)
        self.tree.bind("<Delete>", lambda _e: self._delete_level())

        self.menu = tk.Menu(self, tearoff=0, background=c["panel_alt"], foreground=c["fg"],
                            activebackground=c["select_bg"], activeforeground="#ffffff",
                            borderwidth=0)
        self.menu.add_command(label="修改频率...", command=lambda: self._edit_column("freq"))
        self.menu.add_command(label="修改电压档位...", command=lambda: self._edit_column("volt"))
        self.menu.add_separator()
        self.menu.add_command(label="在下方添加等级", command=self._add_level)
        self.menu.add_command(label="在顶部添加等级", command=self._add_level_top)
        self.menu.add_command(label="复制该等级", command=self._duplicate_level)
        self.menu.add_command(label="删除该等级", command=self._delete_level)

    # -- 数据刷新 --------------------------------------------------------

    @property
    def session(self):
        return self.app.session

    def current_bin(self):
        session = self.session
        if session is None or session.table is None:
            return None
        if self.bin_index < len(session.table.bins):
            return session.table.bins[self.bin_index]
        return None

    def refresh(self) -> None:
        table = self.session.table if self.session else None
        bin_ = self.current_bin()

        selected = self._selected_level_index()
        self.tree.delete(*self.tree.get_children())
        self._row_map.clear()

        if table is None or bin_ is None:
            self.title_label.configure(text="无数据")
            self.stats_label.configure(text="")
            return

        self.title_label.configure(text=bin_.display_name())
        self.stats_label.configure(
            text="%d 个等级 · 默认档位 %s" % (
                len(bin_.levels),
                bin_.initial_pwrlevel if bin_.initial_pwrlevel is not None else "—"))

        opp_freq_map = {opp.freq: opp.volt for opp in table.opps}

        for index, level in enumerate(bin_.levels):
            iid = str(index)
            tags = ["odd" if index % 2 else "even"]
            if not level.is_valid:
                tags.append("placeholder")
            freq_text = level.format_freq() if level.is_valid else "占位 (0 Hz)"
            freak_hz = str(level.freq) if level.freq and level.freq > 0 else "—"

            if level.volt > 0:
                label = table.volt_label(level.volt)
                volt_text = label if label else "%d (自定义)" % level.volt
            elif table.voltage_type == "OPP_TABLE" and level.freq in opp_freq_map:
                volt_text = "电压表: %d" % opp_freq_map[level.freq]
            else:
                volt_text = "—"

            self.tree.insert("", "end", iid=iid, tags=tuple(tags), values=(
                index, freq_text, freak_hz, volt_text,
                self._bus_text(level.bus_min),
                self._bus_text(level.bus_max),
                self._bus_text(level.bus_freq),
            ))
            self._row_map[iid] = index

        if selected is not None and 0 <= selected < len(bin_.levels):
            self.tree.selection_set(str(selected))

    @staticmethod
    def _bus_text(value: int) -> str:
        return "—" if value is None or value < 0 else str(value)

    # -- 交互 ------------------------------------------------------------

    def _selected_level_index(self) -> Optional[int]:
        selection = self.tree.selection()
        if not selection:
            return None
        try:
            return int(selection[0])
        except ValueError:
            return None

    def _on_double_click(self, event: tk.Event) -> None:
        if self.tree.identify_region(event.x, event.y) != "cell":
            return
        row = self.tree.identify_row(event.y)
        column = self.tree.identify_column(event.x)
        if not row:
            return
        try:
            col_index = int(column[1:]) - 1
        except ValueError:
            return
        self.tree.selection_set(row)
        self._edit_column(COLUMNS[col_index][0])

    def _on_right_click(self, event: tk.Event) -> None:
        row = self.tree.identify_row(event.y)
        if row:
            self.tree.selection_set(row)
        try:
            self.menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.menu.grab_release()

    def _edit_column(self, key: str) -> None:
        level_index = self._selected_level_index()
        if level_index is None:
            return
        session = self.session
        table = session.table if session else None
        if table is None:
            return
        level = table.bins[self.bin_index].levels[level_index]

        if key == "freq":
            value = FreqDialog(self, level.freq if level.freq > 0 else 0).show()
            if value is not None and value != level.freq:
                if session.set_level_freq(self.bin_index, level_index, value):
                    self.app.on_session_changed("修改频率")
        elif key == "volt":
            if level.volt <= 0 and table.voltage_type == "OPP_TABLE":
                self.app.set_status("该机型的电压在独立电压表中，请切换到「电压表」页编辑")
                return
            value = VoltageDialog(self, table, level.volt if level.volt > 0 else 1).show()
            if value is not None and value != level.volt:
                if session.set_level_volt(self.bin_index, level_index, value):
                    self.app.on_session_changed("修改电压档位")
        elif key in ("bus_min", "bus_max", "bus_freq"):
            prop = "qcom," + key.replace("_", "-")
            current = {"bus_min": level.bus_min, "bus_max": level.bus_max,
                       "bus_freq": level.bus_freq}[key]
            hint = "总线档位索引（对应 qcom,bus-table-ddr 中的序号）\n不要超过该设备默认的最高档位。"
            value = NumberDialog(self, "修改 %s" % key, max(current, 0), hint=hint,
                                 maximum=255).show()
            if value is not None and value != current:
                if session.set_level_bus(self.bin_index, level_index, prop, value):
                    self.app.on_session_changed("修改 %s" % key)

    # -- 结构操作 --------------------------------------------------------

    def _add_level(self) -> None:
        session = self.session
        if session and session.add_level(self.bin_index, at_top=False):
            self.app.on_session_changed("添加等级")
            self._select_last()

    def _add_level_top(self) -> None:
        session = self.session
        if session and session.add_level(self.bin_index, at_top=True):
            self.app.on_session_changed("添加等级")
            self.tree.selection_set("0")

    def _duplicate_level(self) -> None:
        level_index = self._selected_level_index()
        session = self.session
        if level_index is None or session is None:
            return
        if session.duplicate_level(self.bin_index, level_index):
            self.app.on_session_changed("复制等级")
            self.tree.selection_set(str(level_index + 1))

    def _delete_level(self) -> None:
        level_index = self._selected_level_index()
        session = self.session
        bin_ = self.current_bin()
        if level_index is None or session is None or bin_ is None:
            return
        if len(bin_.levels) <= 1:
            self.app.set_status("至少需要保留一个等级")
            return
        if session.delete_level(self.bin_index, level_index):
            self.app.on_session_changed("删除等级")

    def _select_last(self) -> None:
        children = self.tree.get_children()
        if children:
            self.tree.selection_set(children[-1])
            self.tree.see(children[-1])


class VoltageTablePanel(ttk.Frame):
    """独立电压表（OPP table）编辑面板。"""

    def __init__(self, master: tk.Misc, app):
        super().__init__(master)
        self.app = app
        self._build()

    def _build(self) -> None:
        c = theme.COLORS
        toolbar = ttk.Frame(self, style="Panel.TFrame", padding=(10, 8))
        toolbar.pack(fill="x")
        ttk.Label(toolbar, text="独立电压表 (OPP table)", style="Section.TLabel").pack(side="left")
        self.stats = ttk.Label(toolbar, text="", style="Dim.TLabel")
        self.stats.pack(side="left", padx=(14, 0))

        hint = ttk.Label(self, text="双击「电压」列修改电压角数值（该表为 GPU 各频率点的电压来源）",
                         style="Dim.TLabel", padding=(12, 4))
        hint.pack(fill="x")

        frame = ttk.Frame(self, padding=(10, 0, 10, 10))
        frame.pack(fill="both", expand=True)

        columns = [("freq", "频率", 140, "e"), ("freq_hz", "频率 (Hz)", 140, "e"),
                   ("volt", "电压角", 260, "w")]
        self.tree = ttk.Treeview(frame, columns=[c0[0] for c0 in columns],
                                 show="headings", selectmode="browse")
        for key, title, width, anchor in columns:
            self.tree.heading(key, text=title)
            self.tree.column(key, width=width, anchor=anchor)
        scroll = ttk.Scrollbar(frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

        self.tree.tag_configure("odd", background=c["panel"])
        self.tree.tag_configure("even", background=c["panel_alt"])
        self.tree.bind("<Double-Button-1>", self._on_double_click)

        buttons = ttk.Frame(self, padding=(10, 0, 10, 10))
        buttons.pack(fill="x")
        ttk.Button(buttons, text="修改选中电压...", command=lambda: self._edit(None)).pack(side="left")

    def refresh(self) -> None:
        session = self.app.session
        table = session.table if session else None
        self.tree.delete(*self.tree.get_children())
        if table is None or not table.opps:
            self.stats.configure(text="无电压表数据")
            return
        self.stats.configure(text="%d 条记录" % len(table.opps))
        for index, opp in enumerate(table.opps):
            label = table.volt_label(opp.volt)
            volt_text = label if label else str(opp.volt)
            self.tree.insert("", "end", iid=str(index),
                             tags=("odd" if index % 2 else "even",),
                             values=(opp.format_freq(), opp.freq, volt_text))

    def _on_double_click(self, event: tk.Event) -> None:
        row = self.tree.identify_row(event.y)
        if not row:
            return
        self.tree.selection_set(row)
        self._edit(int(row))

    def _edit(self, opp_index: Optional[int]) -> None:
        session = self.app.session
        table = session.table if session else None
        if session is None or table is None or not table.opps:
            return
        if opp_index is None:
            selection = self.tree.selection()
            if not selection:
                self.app.set_status("请先选择一条记录")
                return
            opp_index = int(selection[0])
        opp = table.opps[opp_index]
        value = VoltageDialog(self, table, opp.volt, title="修改电压 (%s)" % opp.format_freq()).show()
        if value is not None and value != opp.volt:
            if session.set_opp_volt(opp_index, value):
                self.app.on_session_changed("修改电压表")
