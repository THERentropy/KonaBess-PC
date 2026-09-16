"""频率-电压曲线图面板。"""

from __future__ import annotations

import math
import tkinter as tk
from tkinter import ttk
from typing import Optional

from konabess.gpu_table import format_freq_hz

from . import theme
from .dialogs import FreqDialog, VoltageDialog


class CurvePanel(ttk.Frame):
    """以电压为横轴、频率为纵轴展示当前 bin 的调频曲线。"""

    @property
    def PAD_LEFT(self) -> int:  # noqa: N802
        return theme.px(78)

    @property
    def PAD_RIGHT(self) -> int:  # noqa: N802
        return theme.px(40)

    @property
    def PAD_TOP(self) -> int:  # noqa: N802
        return theme.px(34)

    @property
    def PAD_BOTTOM(self) -> int:  # noqa: N802
        return theme.px(52)

    def __init__(self, master: tk.Misc, app):
        super().__init__(master)
        self.app = app
        self.bin_index = 0
        self._points: list[tuple[float, float, int, int]] = []  # x, y, bin_idx, level_idx
        self._selected: Optional[int] = None
        self._hover: Optional[int] = None
        self._build()

    # -- 构建 ------------------------------------------------------------

    def _build(self) -> None:
        bar = ttk.Frame(self, style="Panel.TFrame", padding=(10, 8))
        bar.pack(fill="x")
        ttk.Label(bar, text="调频曲线", style="Section.TLabel").pack(side="left")

        ttk.Label(bar, text="Bin:", style="Dim.TLabel").pack(side="left", padx=(18, 6))
        self.bin_var = tk.StringVar()
        self.bin_combo = ttk.Combobox(bar, textvariable=self.bin_var, state="readonly", width=28)
        self.bin_combo.pack(side="left")
        self.bin_combo.bind("<<ComboboxSelected>>", self._on_bin_selected)

        self.info_label = ttk.Label(bar, text="", style="Dim.TLabel")
        self.info_label.pack(side="left", padx=(16, 0))

        ttk.Label(bar, text="双击数据点可编辑 · 横轴为电压档位，纵轴为频率",
                  style="Dim.TLabel").pack(side="right")

        self.canvas = tk.Canvas(self, highlightthickness=0)
        theme.style_canvas(self.canvas)
        self.canvas.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        self.canvas.bind("<Configure>", lambda _e: self.redraw())
        self.canvas.bind("<Button-1>", self._on_click)
        self.canvas.bind("<Double-Button-1>", self._on_double_click)
        self.canvas.bind("<Motion>", self._on_motion)
        self.canvas.bind("<Leave>", lambda _e: self._set_hover(None))

    # -- 数据 ------------------------------------------------------------

    def refresh(self) -> None:
        session = self.app.session
        table = session.table if session else None
        values: list[str] = []
        if table is not None:
            for bin_ in table.bins:
                values.append(bin_.display_name())
        if self.bin_combo["values"] != values:
            self.bin_combo.configure(values=values)
        if table is not None and table.bins:
            if self.bin_index >= len(table.bins):
                self.bin_index = 0
            self.bin_combo.current(self.bin_index)
        else:
            self.bin_combo.set("")
        self.redraw()

    def _on_bin_selected(self, _event=None) -> None:
        self.bin_index = self.bin_combo.current()
        self.redraw()

    # -- 绘制 ------------------------------------------------------------

    def redraw(self) -> None:
        canvas = self.canvas
        canvas.delete("all")
        width = canvas.winfo_width()
        height = canvas.winfo_height()
        if width < 60 or height < 60:
            return

        c = theme.COLORS
        session = self.app.session
        table = session.table if session else None
        if table is None or not table.bins:
            canvas.create_text(width // 2, height // 2, text="未加载数据",
                               fill=c["fg_muted"], font=theme.font(12))
            self.info_label.configure(text="")
            return

        if self.bin_index >= len(table.bins):
            self.bin_index = 0
        bin_ = table.bins[self.bin_index]
        levels = [(i, lv) for i, lv in enumerate(bin_.levels) if lv.is_valid]
        if not levels:
            canvas.create_text(width // 2, height // 2, text="该 Bin 没有有效的频率等级",
                               fill=c["fg_muted"], font=theme.font(12))
            return

        self.info_label.configure(text="%d 个有效等级" % len(levels))

        use_volt = all(lv.volt > 0 for _i, lv in levels)
        xs = [(lv.volt if use_volt else i) for i, lv in levels]
        ys = [lv.freq / 1_000_000.0 for _i, lv in levels]
        x_label = "电压档位 (qcom,level)" if use_volt else "等级序号"

        x_min, x_max = min(xs), max(xs)
        y_min, y_max = min(ys), max(ys)
        if x_max - x_min < 1e-9:
            x_min -= 1
            x_max += 1
        if y_max - y_min < 1e-9:
            y_min -= 100
            y_max += 100
        x_pad = (x_max - x_min) * 0.06
        y_pad = (y_max - y_min) * 0.08
        x_min -= x_pad
        x_max += x_pad
        y_min = max(0.0, y_min - y_pad)
        y_max += y_pad

        plot_w = width - self.PAD_LEFT - self.PAD_RIGHT
        plot_h = height - self.PAD_TOP - self.PAD_BOTTOM
        if plot_w <= 20 or plot_h <= 20:
            return

        def to_px(x: float, y: float) -> tuple[float, float]:
            px = self.PAD_LEFT + (x - x_min) / (x_max - x_min) * plot_w
            py = self.PAD_TOP + plot_h - (y - y_min) / (y_max - y_min) * plot_h
            return px, py

        # 网格与坐标轴
        canvas.create_rectangle(self.PAD_LEFT, self.PAD_TOP,
                                self.PAD_LEFT + plot_w, self.PAD_TOP + plot_h,
                                outline=c["chart_grid"])
        y_step = self._nice_step(y_max - y_min, 5)
        y_tick = math.ceil(y_min / y_step) * y_step
        while y_tick <= y_max:
            _px, py = to_px(x_min, y_tick)
            canvas.create_line(self.PAD_LEFT, py, self.PAD_LEFT + plot_w, py,
                               fill=c["chart_grid"], dash=(2, 4))
            label = ("%.2f GHz" % (y_tick / 1000.0)) if y_tick >= 1000 else ("%.0f MHz" % y_tick)
            canvas.create_text(self.PAD_LEFT - 10, py, text=label, anchor="e",
                               fill=c["chart_text"], font=theme.font(8))
            y_tick += y_step

        x_step = self._nice_step(x_max - x_min, 6)
        x_tick = math.ceil(x_min / x_step) * x_step
        while x_tick <= x_max:
            px, _py = to_px(x_tick, y_min)
            canvas.create_line(px, self.PAD_TOP, px, self.PAD_TOP + plot_h,
                               fill=c["chart_grid"], dash=(2, 4))
            canvas.create_text(px, self.PAD_TOP + plot_h + 14,
                               text="%d" % round(x_tick), fill=c["chart_text"],
                               font=theme.font(8))
            x_tick += x_step

        canvas.create_text(self.PAD_LEFT + plot_w / 2, height - 14, text=x_label,
                           fill=c["chart_text"], font=theme.font(9))
        canvas.create_text(16, self.PAD_TOP + plot_h / 2, text="频率", angle=90,
                           fill=c["chart_text"], font=theme.font(9))

        # 曲线
        pixels = [to_px(x, y) for x, y in zip(xs, ys)]
        self._points = []
        for (index, level), (px, py) in zip(levels, pixels):
            self._points.append((px, py, self.bin_index, index))

        if len(pixels) > 1:
            canvas.create_line([coord for point in pixels for coord in point],
                               fill=c["chart_line"], width=2, smooth=False)

        for (index, level), (px, py) in zip(levels, pixels):
            selected = self._selected == index
            hovered = self._hover == index
            radius = 6 if (selected or hovered) else 4
            fill = c["accent"] if selected else (c["warn"] if hovered else c["chart_point"])
            canvas.create_oval(px - radius, py - radius, px + radius, py + radius,
                               fill=fill, outline=c["chart_bg"], width=1)
            label = format_freq_hz(level.freq)
            if selected or hovered:
                canvas.create_text(px, py - 16, text=label, fill=c["fg"], font=theme.font(9, True))
            else:
                canvas.create_text(px, py - 14, text=label, fill=c["chart_text"], font=theme.font(8))

    @staticmethod
    def _nice_step(span: float, target: int) -> float:
        if span <= 0:
            return 1.0
        raw = span / max(1, target)
        magnitude = 10 ** math.floor(math.log10(raw))
        for multiple in (1, 2, 2.5, 5, 10):
            if raw <= magnitude * multiple:
                return magnitude * multiple
        return magnitude * 10

    # -- 交互 ------------------------------------------------------------

    def _nearest(self, x: int, y: int) -> Optional[int]:
        best_index = None
        best_distance = 14.0
        for px, py, _bin_index, level_index in self._points:
            distance = math.hypot(px - x, py - y)
            if distance < best_distance:
                best_distance = distance
                best_index = level_index
        return best_index

    def _on_click(self, event: tk.Event) -> None:
        index = self._nearest(event.x, event.y)
        self._selected = index
        self._hover = index
        self.redraw()

    def _on_motion(self, event: tk.Event) -> None:
        index = self._nearest(event.x, event.y)
        if index != self._hover:
            self._set_hover(index)

    def _set_hover(self, index: Optional[int]) -> None:
        if index != self._hover:
            self._hover = index
            self.redraw()

    def _on_double_click(self, event: tk.Event) -> None:
        index = self._nearest(event.x, event.y)
        if index is None:
            return
        session = self.app.session
        table = session.table if session else None
        if session is None or table is None:
            return
        bin_ = table.bins[self.bin_index]
        if index >= len(bin_.levels):
            return
        level = bin_.levels[index]

        # 双击靠近点上方 → 编辑频率；否则编辑电压
        _px, py, _b, _l = next(p for p in self._points if p[3] == index)
        if abs(event.y - (py - 16)) < 12 and level.volt > 0:
            value = VoltageDialog(self, table, level.volt).show()
            if value is not None and value != level.volt:
                if session.set_level_volt(self.bin_index, index, value):
                    self.app.on_session_changed("修改电压档位")
        else:
            value = FreqDialog(self, level.freq if level.freq > 0 else 0).show()
            if value is not None and value != level.freq:
                if session.set_level_freq(self.bin_index, index, value):
                    self.app.on_session_changed("修改频率")
