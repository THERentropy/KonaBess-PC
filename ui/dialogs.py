"""对话框：频率输入、电压档位选择、帮助等。"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Optional

from konabess import chips
from konabess.gpu_table import GpuTable, format_freq_hz, parse_freq_input

from . import theme


class _BaseDialog(tk.Toplevel):
    """模态对话框基类。"""

    def __init__(self, parent: tk.Misc, title: str, width: int = 420, height: int = 300):
        super().__init__(parent)
        self.result = None
        self.title(title)
        self.configure(background=theme.COLORS["bg"])
        self.resizable(False, False)
        self.transient(parent.winfo_toplevel())

        width = theme.px(width)
        height = theme.px(height)
        self.geometry("%dx%d" % (width, height))
        self.update_idletasks()
        pos_x = parent.winfo_rootx() + (parent.winfo_width() - width) // 2
        pos_y = parent.winfo_rooty() + (parent.winfo_height() - height) // 3
        self.geometry("+%d+%d" % (max(0, pos_x), max(0, pos_y)))

        self.protocol("WM_DELETE_WINDOW", self._on_cancel)
        self.bind("<Escape>", lambda _e: self._on_cancel())

    def show(self):
        self.grab_set()
        self.wait_window()
        return self.result

    def _on_cancel(self) -> None:
        self.result = None
        self.destroy()


class FreqDialog(_BaseDialog):
    """频率输入对话框（支持 1160 / 1160MHz / 1.16GHz 等格式）。"""

    def __init__(self, parent: tk.Misc, current_hz: int, title: str = "设置频率"):
        super().__init__(parent, title, width=430, height=260)
        c = theme.COLORS

        body = ttk.Frame(self, padding=16)
        body.pack(fill="both", expand=True)

        ttk.Label(body, text="当前:", font=theme.font(10)).grid(row=0, column=0, sticky="w", pady=(0, 4))
        ttk.Label(body, text=format_freq_hz(current_hz), font=theme.font(12, True),
                  foreground=c["accent"]).grid(row=0, column=1, sticky="w", padx=(8, 0), pady=(0, 4))

        ttk.Label(body, text="新频率:", font=theme.font(10)).grid(row=1, column=0, sticky="w", pady=(8, 4))
        self.entry = tk.Entry(body, font=theme.mono_font(12), width=18,
                              background=c["entry_bg"], foreground=c["fg"],
                              insertbackground=c["fg"], highlightthickness=1,
                              highlightbackground=c["border"], highlightcolor=c["accent"],
                              relief="flat")
        self.entry.grid(row=1, column=1, sticky="w", padx=(8, 0), pady=(8, 4))
        self.entry.insert(0, "%d" % (current_hz // 1_000_000) if current_hz > 0 else "")
        self.entry.bind("<KeyRelease>", lambda _e: self._update_preview())
        self.entry.bind("<Return>", lambda _e: self._on_ok())
        self.entry.focus_set()
        self.entry.selection_range(0, "end")

        self.preview = ttk.Label(body, text="", font=theme.font(9), foreground=c["fg_dim"])
        self.preview.grid(row=2, column=0, columnspan=3, sticky="w", pady=(2, 10))

        quick = ttk.Frame(body)
        quick.grid(row=3, column=0, columnspan=3, sticky="w", pady=(0, 12))
        for label, delta in (("-100 MHz", -100), ("-25 MHz", -25), ("+25 MHz", 25), ("+100 MHz", 100)):
            ttk.Button(quick, text=label, width=9,
                       command=lambda d=delta: self._adjust(d)).pack(side="left", padx=(0, 6))

        buttons = ttk.Frame(body)
        buttons.grid(row=4, column=0, columnspan=3, sticky="e")
        ttk.Button(buttons, text="取消", command=self._on_cancel).pack(side="left", padx=(0, 8))
        ttk.Button(buttons, text="确定", style="Accent.TButton",
                   command=self._on_ok).pack(side="left")

        self._update_preview()

    def _current_input(self) -> Optional[int]:
        return parse_freq_input(self.entry.get())

    def _adjust(self, delta_mhz: int) -> None:
        value = self._current_input()
        if value is None:
            return
        value += delta_mhz * 1_000_000
        if value > 0:
            self.entry.delete(0, "end")
            self.entry.insert(0, "%d" % (value // 1_000_000))
        self._update_preview()

    def _update_preview(self) -> None:
        value = self._current_input()
        if value is None:
            self.preview.configure(text="无法解析，请输入例如: 1160 或 1.16GHz",
                                   foreground=theme.COLORS["error"])
        elif value <= 0:
            self.preview.configure(text="频率必须大于 0", foreground=theme.COLORS["error"])
        else:
            self.preview.configure(
                text="= %s (%d Hz)" % (format_freq_hz(value), value),
                foreground=theme.COLORS["ok"])

    def _on_ok(self) -> None:
        value = self._current_input()
        if value is None or value <= 0:
            self.bell()
            return
        self.result = value
        self.destroy()


class VoltageDialog(_BaseDialog):
    """电压档位选择器：滑块 + 预设列表 + 直接输入。"""

    def __init__(self, parent: tk.Misc, table: GpuTable, current_value: int,
                 title: str = "选择电压档位", max_value: int = 0):
        super().__init__(parent, title, width=470, height=520)
        c = theme.COLORS
        self.table = table
        self.levels = table.level_map()
        self.limit = max_value or table.level_count or 480

        body = ttk.Frame(self, padding=14)
        body.pack(fill="both", expand=True)

        # 当前值
        head = ttk.Frame(body)
        head.pack(fill="x")
        ttk.Label(head, text="当前:", font=theme.font(10)).pack(side="left")
        self.value_label = ttk.Label(head, text="", font=theme.font(12, True),
                                     foreground=c["accent"])
        self.value_label.pack(side="left", padx=(6, 0))

        # 滑块
        slider_row = ttk.Frame(body)
        slider_row.pack(fill="x", pady=(8, 2))
        ttk.Label(slider_row, text="1", font=theme.font(8),
                  foreground=c["fg_muted"]).pack(side="left")
        self.scale = ttk.Scale(slider_row, from_=1, to=self.limit, orient="horizontal",
                               command=self._on_scale)
        self.scale.pack(side="left", fill="x", expand=True, padx=6)
        ttk.Label(slider_row, text=str(self.limit), font=theme.font(8),
                  foreground=c["fg_muted"]).pack(side="left")

        # 预设列表
        ttk.Label(body, text="预设档位（推荐）", style="Section.TLabel").pack(anchor="w", pady=(10, 4))
        list_frame = ttk.Frame(body)
        list_frame.pack(fill="both", expand=True)
        self.listbox = tk.Listbox(list_frame, height=12, exportselection=False)
        theme.style_listbox(self.listbox)
        scroll = ttk.Scrollbar(list_frame, orient="vertical", command=self.listbox.yview)
        self.listbox.configure(yscrollcommand=scroll.set)
        self.listbox.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.listbox.bind("<<ListboxSelect>>", self._on_list_select)
        self.listbox.bind("<Double-Button-1>", lambda _e: self._on_ok())

        self.preset_values: list[int] = []
        for index in sorted(self.levels.keys()):
            value = index + 1
            self.preset_values.append(value)
            label = self.levels[index]
            self.listbox.insert("end", "  %s" % label)

        # 输入行
        input_row = ttk.Frame(body)
        input_row.pack(fill="x", pady=(10, 0))
        ttk.Label(input_row, text="直接输入:", font=theme.font(10)).pack(side="left")
        self.entry = tk.Entry(input_row, font=theme.mono_font(11), width=8,
                              background=c["entry_bg"], foreground=c["fg"],
                              insertbackground=c["fg"], highlightthickness=1,
                              highlightbackground=c["border"], highlightcolor=c["accent"],
                              relief="flat")
        self.entry.pack(side="left", padx=(6, 8))
        self.entry.bind("<Return>", lambda _e: self._on_entry())
        self.entry.bind("<KeyRelease>", self._on_entry_key)
        self.entry_hint = ttk.Label(input_row, text="", font=theme.font(9),
                                    foreground=c["fg_dim"])
        self.entry_hint.pack(side="left")

        buttons = ttk.Frame(body)
        buttons.pack(fill="x", pady=(12, 0))
        ttk.Button(buttons, text="取消", command=self._on_cancel).pack(side="right")
        ttk.Button(buttons, text="确定", style="Accent.TButton",
                   command=self._on_ok).pack(side="right", padx=(0, 8))

        # 初始化当前值
        self.current = current_value if current_value and current_value > 0 else 1
        self.scale.set(self.current)
        self._sync_from_value(self.current)

    # -- 联动 ------------------------------------------------------------

    def _on_scale(self, raw: str) -> None:
        try:
            value = int(round(float(raw)))
        except ValueError:
            return
        self._sync_from_value(value, update_scale=False)

    def _on_list_select(self, _event) -> None:
        selection = self.listbox.curselection()
        if not selection:
            return
        value = self.preset_values[selection[0]]
        self.scale.set(value)
        self._sync_from_value(value, update_scale=False)

    def _on_entry_key(self, _event) -> None:
        text = self.entry.get().strip()
        try:
            value = int(text, 0)
        except ValueError:
            self.entry_hint.configure(text="数值无效", foreground=theme.COLORS["error"])
            return
        if not (1 <= value <= self.limit):
            self.entry_hint.configure(
                text="超出范围 1..%d" % self.limit, foreground=theme.COLORS["error"])
            return
        label = self.table.volt_label(value)
        self.entry_hint.configure(
            text=label or "自定义值（无对应名称）",
            foreground=theme.COLORS["ok"] if label else theme.COLORS["warn"])

    def _sync_from_value(self, value: int, update_scale: bool = True) -> None:
        self.current = value
        if update_scale:
            self.scale.set(value)
        label = self.table.volt_label(value)
        text = "%d - %s" % (value, label.split(" - ", 1)[1]) if label else "%d (自定义)" % value
        self.value_label.configure(text=text)
        self.entry.delete(0, "end")
        self.entry.insert(0, str(value))
        # 高亮列表中的对应项
        if value in self.preset_values:
            index = self.preset_values.index(value)
            self.listbox.selection_clear(0, "end")
            self.listbox.selection_set(index)
            self.listbox.see(index)
        else:
            self.listbox.selection_clear(0, "end")

    # -- 确定 ------------------------------------------------------------

    def _on_entry(self) -> None:
        try:
            value = int(self.entry.get().strip(), 0)
        except ValueError:
            self.bell()
            return
        if 1 <= value <= self.limit:
            self.scale.set(value)
            self._sync_from_value(value, update_scale=False)
        else:
            self.bell()

    def _on_ok(self) -> None:
        self.result = self.current
        self.destroy()


class NumberDialog(_BaseDialog):
    """通用数值输入（总线档位等）。"""

    def __init__(self, parent: tk.Misc, title: str, current: int, hint: str = "",
                 maximum: int = 0xFFFFFFFF):
        super().__init__(parent, title, width=380, height=190 if hint else 165)
        c = theme.COLORS
        self.maximum = maximum

        body = ttk.Frame(self, padding=16)
        body.pack(fill="both", expand=True)

        if hint:
            ttk.Label(body, text=hint, font=theme.font(9), foreground=c["fg_dim"],
                      wraplength=340, justify="left").pack(anchor="w", pady=(0, 8))

        row = ttk.Frame(body)
        row.pack(fill="x")
        ttk.Label(row, text="数值:", font=theme.font(10)).pack(side="left")
        self.entry = tk.Entry(row, font=theme.mono_font(12), width=14,
                              background=c["entry_bg"], foreground=c["fg"],
                              insertbackground=c["fg"], highlightthickness=1,
                              highlightbackground=c["border"], highlightcolor=c["accent"],
                              relief="flat")
        self.entry.pack(side="left", padx=(8, 0))
        self.entry.insert(0, str(current))
        self.entry.focus_set()
        self.entry.selection_range(0, "end")
        self.entry.bind("<Return>", lambda _e: self._on_ok())

        self.hint_label = ttk.Label(body, text="支持十进制与 0x 十六进制", font=theme.font(9),
                                    foreground=c["fg_dim"])
        self.hint_label.pack(anchor="w", pady=(6, 0))

        buttons = ttk.Frame(body)
        buttons.pack(fill="x", pady=(12, 0))
        ttk.Button(buttons, text="取消", command=self._on_cancel).pack(side="right")
        ttk.Button(buttons, text="确定", style="Accent.TButton",
                   command=self._on_ok).pack(side="right", padx=(0, 8))

    def _on_ok(self) -> None:
        try:
            value = int(self.entry.get().strip(), 0)
        except ValueError:
            self.bell()
            self.hint_label.configure(text="数值无效", foreground=theme.COLORS["error"])
            return
        if value < 0 or value > self.maximum:
            self.bell()
            self.hint_label.configure(text="超出范围 0..%d" % self.maximum,
                                      foreground=theme.COLORS["error"])
            return
        self.result = value
        self.destroy()


class TextDialog(_BaseDialog):
    """只读文本对话框（帮助 / 关于 / DTS 视图）。"""

    def __init__(self, parent: tk.Misc, title: str, text: str,
                 width: int = 720, height: int = 560):
        super().__init__(parent, title, width=width, height=height)
        c = theme.COLORS

        body = ttk.Frame(self, padding=10)
        body.pack(fill="both", expand=True)

        self.text = tk.Text(body, wrap="none", font=theme.mono_font(9),
                            background=c["entry_bg"], foreground=c["fg"],
                            insertbackground=c["fg"], relief="flat",
                            highlightthickness=1, highlightbackground=c["border"])
        scroll_y = ttk.Scrollbar(body, orient="vertical", command=self.text.yview)
        scroll_x = ttk.Scrollbar(body, orient="horizontal", command=self.text.xview)
        self.text.configure(yscrollcommand=scroll_y.set, xscrollcommand=scroll_x.set)
        self.text.grid(row=0, column=0, sticky="nsew")
        scroll_y.grid(row=0, column=1, sticky="ns")
        scroll_x.grid(row=1, column=0, sticky="ew")
        body.rowconfigure(0, weight=1)
        body.columnconfigure(0, weight=1)

        self.text.insert("1.0", text)
        self.text.configure(state="disabled")

        ttk.Button(self, text="关闭", command=self.destroy).pack(pady=(0, 12))


HELP_TEXT = """KonaBess PC — 使用说明
========================================

【这是什么】
在电脑上编辑骁龙设备 GPU 频率/电压表的工具（参考 KonaBess Next 实现）。
通过修改 boot / vendor_boot / dtbo 镜像中的设备树 (DTB)，实现在不重编译
内核的情况下对 GPU 进行超频或降压。

【基本流程】
1. 从手机或官方固件包中提取 boot.img（或 vendor_boot.img / dtbo.img）
   · 若 DTB 不在 boot.img 中，程序会自动尝试内核内嵌的设备树
2. 点击「打开文件」载入镜像，程序自动识别芯片与 GPU 频率表
3. 在「频率表」中编辑：
   · 双击「频率」列 → 修改频率
   · 双击「电压」列 → 选择电压档位（推荐使用预设档位）
   · 双击「总线」列 → 修改总线档位
   · 右键 → 添加 / 复制 / 删除等级
4. 点击「保存镜像」生成新的镜像文件（如 boot_new.img）
5. 用 fastboot 刷入：
   fastboot flash boot boot_new.img
   （vendor_boot 对应 fastboot flash vendor_boot，dtbo 对应 fastboot flash dtbo）

【电压档位说明】
· 数值是 RPMh 电压角（corner）编号，数值越大电压越高。
· 降压（降低数值）可以降低功耗与发热，但过低会导致 GPU 不稳定/花屏，
  请逐档微调并充分测试。
· 超频时通常需要适当提高电压档位以保证稳定。

【安全提示】
· 修改前请务必备份原镜像，确保设备可进入 fastboot 模式。
· 调节不当可能导致设备不稳定，极端情况下需要重新刷入原镜像恢复。
· 本工具不会自动刷机，刷写操作完全由你控制。

【支持格式】
· Android boot image v0 / v1 / v2 / v3 / v4
· Android vendor_boot v3 / v4
· DTBO 镜像（表格式与纯拼接格式）
· 独立 DTB 文件 / 多个 DTB 拼接文件 / DTS 文本

【快捷键】
· Ctrl+O  打开文件      Ctrl+S  保存
· Ctrl+Z  撤销          Ctrl+Y  重做
· F1      帮助
"""

ABOUT_TEXT = """KonaBess PC 1.0.0

骁龙 GPU 频率/电压表桌面编辑器。

参考项目: KonaBess Next (github.com/KonaBess-Next/KonaBess-Next)
作者: libxzr (原版 KonaBess) 及 KonaBess Next 贡献者

本工具完全在本地运行，不依赖 magiskboot / dtc 等外部命令，
直接以设备树二进制 (FDT) 的方式读写镜像中的 DTB。

免责声明: 修改系统文件与超频/降压存在固有风险，
开发者不对设备损坏、数据丢失或系统不稳定承担责任。
"""
