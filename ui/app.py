"""主窗口：工具栏、信息栏、标签页与状态栏。"""

from __future__ import annotations

import os
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from typing import Optional

from konabess import __app_name__, __version__, compression, dts, workspace
from konabess.bootimg import ImageKind, kind_display_name
from konabess.gpu_table import format_freq_hz

from . import theme
from .curve_view import CurvePanel
from .dialogs import HELP_TEXT, TextDialog
from .table_view import LevelTablePanel, VoltageTablePanel

WELCOME_TEXT = """KonaBess PC

骁龙 GPU 频率 / 电压表桌面编辑器

点击「打开文件」载入 boot.img / vendor_boot.img / dtbo.img，
或直接打开 .dtb / .dts 文件。

程序会自动识别芯片与 GPU 频率表，编辑完成后
点击「保存镜像」生成可刷入的镜像文件。
"""


class MainWindow(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.withdraw()
        self.title("%s %s" % (__app_name__, __version__))
        theme.apply_theme(self)

        # 默认尺寸适配屏幕（高 DPI 下按比例换算后仍不超出可用区域）
        screen_w = self.winfo_screenwidth()
        screen_h = self.winfo_screenheight()
        width = min(theme.px(1200), int(screen_w * 0.94))
        height = min(theme.px(780), int(screen_h * 0.88))
        pos_x = max(0, (screen_w - width) // 2)
        pos_y = max(0, (screen_h - height) // 3)
        self.geometry("%dx%d+%d+%d" % (width, height, pos_x, pos_y))
        self.minsize(min(theme.px(940), width), min(theme.px(620), height))

        self.session: Optional[workspace.Session] = None
        self._level_panels: list[LevelTablePanel] = []
        self._curve_panel: Optional[CurvePanel] = None
        self._volt_panel: Optional[VoltageTablePanel] = None
        self._dts_text: Optional[tk.Text] = None
        self._dts_loaded = False
        self._slot_combo: Optional[ttk.Combobox] = None
        self._slot_var = tk.StringVar()

        self._build_menu()
        self._build_toolbar()
        self._build_info_bar()
        self._build_body()
        self._build_status_bar()
        self._bind_keys()
        self._update_actions()

        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.deiconify()

    # ------------------------------------------------------------------
    # 界面构建
    # ------------------------------------------------------------------

    def _build_menu(self) -> None:
        c = theme.COLORS
        menubar = tk.Menu(self, background=c["panel_alt"], foreground=c["fg"],
                          activebackground=c["select_bg"], activeforeground="#ffffff",
                          borderwidth=0)

        file_menu = tk.Menu(menubar, tearoff=0, background=c["panel_alt"], foreground=c["fg"],
                            activebackground=c["select_bg"], activeforeground="#ffffff")
        file_menu.add_command(label="打开文件...", accelerator="Ctrl+O", command=self.open_file)
        file_menu.add_command(label="保存", accelerator="Ctrl+S", command=self.save_file)
        file_menu.add_command(label="另存为...", accelerator="Ctrl+Shift+S", command=self.save_file_as)
        file_menu.add_separator()
        file_menu.add_command(label="导出 DTS 文本...", command=self.export_dts)
        file_menu.add_command(label="导出 DTB 文件...", command=self.export_dtb)
        file_menu.add_separator()
        file_menu.add_command(label="导出调参方案 (JSON)...", command=self.export_profile)
        file_menu.add_command(label="导入调参方案 (JSON)...", command=self.import_profile)
        file_menu.add_separator()
        file_menu.add_command(label="退出", command=self._on_close)
        menubar.add_cascade(label="文件", menu=file_menu)

        edit_menu = tk.Menu(menubar, tearoff=0, background=c["panel_alt"], foreground=c["fg"],
                            activebackground=c["select_bg"], activeforeground="#ffffff")
        edit_menu.add_command(label="撤销", accelerator="Ctrl+Z", command=self.undo)
        edit_menu.add_command(label="重做", accelerator="Ctrl+Y", command=self.redo)
        edit_menu.add_separator()
        edit_menu.add_command(label="重新载入文件", command=self.reload_file)
        menubar.add_cascade(label="编辑", menu=edit_menu)

        help_menu = tk.Menu(menubar, tearoff=0, background=c["panel_alt"], foreground=c["fg"],
                            activebackground=c["select_bg"], activeforeground="#ffffff")
        help_menu.add_command(label="使用说明", accelerator="F1",
                              command=lambda: TextDialog(self, "使用说明", HELP_TEXT,
                                                         width=760, height=620))
        help_menu.add_command(label="压缩库状态", command=self._show_backend_status)
        help_menu.add_separator()
        help_menu.add_command(label="关于", command=self._show_about)
        menubar.add_cascade(label="帮助", menu=help_menu)

        self.configure(menu=menubar)

    def _build_toolbar(self) -> None:
        bar = ttk.Frame(self, style="Toolbar.TFrame", padding=(10, 8))
        bar.pack(fill="x")

        def button(text, command, style="TButton", width=None):
            btn = ttk.Button(bar, text=text, command=command, style=style)
            if width:
                btn.configure(width=width)
            btn.pack(side="left", padx=(0, 6))
            return btn

        self.btn_open = button("打开文件", self.open_file, "Accent.TButton")
        self.btn_save = button("保存镜像", self.save_file)
        self.btn_save_as = button("另存为", self.save_file_as)

        ttk.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=8)

        self.btn_undo = button("撤销", self.undo)
        self.btn_redo = button("重做", self.redo)

        ttk.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=8)

        self.btn_export_dts = button("导出 DTS", self.export_dts)
        self.btn_export_dtb = button("导出 DTB", self.export_dtb)
        self.btn_profile = button("调参方案", self.export_profile)

        self.btn_help = button("帮助", lambda: TextDialog(self, "使用说明", HELP_TEXT,
                                                          width=760, height=620))
        self.btn_help.pack_forget()
        self.btn_help.pack(side="right")

    def _build_info_bar(self) -> None:
        bar = ttk.Frame(self, style="Info.TFrame", padding=(14, 10))
        bar.pack(fill="x")

        self.chip_label = ttk.Label(bar, text="芯片: —", style="Panel.TLabel",
                                    font=theme.font(11, True))
        self.chip_label.pack(side="left")
        self.gpu_label = ttk.Label(bar, text="GPU: —", style="Dim.TLabel")
        self.gpu_label.pack(side="left", padx=(20, 0))
        self.volt_label = ttk.Label(bar, text="电压模式: —", style="Dim.TLabel")
        self.volt_label.pack(side="left", padx=(20, 0))
        self.file_label = ttk.Label(bar, text="", style="Dim.TLabel")
        self.file_label.pack(side="left", padx=(20, 0))

        self._slot_holder = ttk.Frame(bar, style="Info.TFrame")
        self._slot_holder.pack(side="right")

    def _build_body(self) -> None:
        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill="both", expand=True, padx=10, pady=(8, 0))
        self.notebook.bind("<<NotebookTabChanged>>", self._on_tab_changed)

        self._welcome = ttk.Frame(self.notebook)
        label = ttk.Label(self._welcome, text=WELCOME_TEXT, justify="center",
                          font=theme.font(11))
        label.place(relx=0.5, rely=0.45, anchor="center")
        self.notebook.add(self._welcome, text=" 欢迎 ")

    def _build_status_bar(self) -> None:
        bar = ttk.Frame(self, style="Toolbar.TFrame", padding=(12, 6))
        bar.pack(fill="x", side="bottom")
        self.status_label = ttk.Label(bar, text="就绪", style="Status.TLabel")
        self.status_label.pack(side="left")
        self.modified_label = ttk.Label(bar, text="", style="Status.TLabel")
        self.modified_label.pack(side="right")

    def _bind_keys(self) -> None:
        self.bind("<Control-o>", lambda _e: self.open_file())
        self.bind("<Control-s>", lambda _e: self.save_file())
        self.bind("<Control-S>", lambda _e: self.save_file_as())
        self.bind("<Control-z>", lambda _e: self.undo())
        self.bind("<Control-y>", lambda _e: self.redo())
        self.bind("<F1>", lambda _e: TextDialog(self, "使用说明", HELP_TEXT,
                                                width=760, height=620))

    # ------------------------------------------------------------------
    # 状态
    # ------------------------------------------------------------------

    def set_status(self, message: str) -> None:
        self.status_label.configure(text=message)

    def _update_actions(self) -> None:
        session = self.session
        has_session = session is not None
        has_table = has_session and session.table is not None

        state = "normal" if has_session else "disabled"
        for button in (self.btn_save, self.btn_save_as, self.btn_export_dtb):
            button.configure(state=state)
        self.btn_export_dts.configure(state="normal" if has_table else "disabled")
        self.btn_profile.configure(state="normal" if has_table else "disabled")
        self.btn_undo.configure(state="normal" if (has_session and session.can_undo) else "disabled")
        self.btn_redo.configure(state="normal" if (has_session and session.can_redo) else "disabled")

        self.modified_label.configure(
            text="● 有未保存的修改" if (has_session and session.modified) else "")

    def _update_info_bar(self) -> None:
        session = self.session
        table = session.table if session else None
        if table is None:
            self.chip_label.configure(text="芯片: —")
            self.gpu_label.configure(text="GPU: —")
            self.volt_label.configure(text="电压模式: —")
            self.file_label.configure(text="")
            return
        self.chip_label.configure(text="芯片: %s" % table.chip_display)
        self.gpu_label.configure(text="GPU: %s" % (table.gpu_model or "未知"))
        volt_text = {
            "INLINE_LEVEL": "内联 (qcom,level)",
            "OPP_TABLE": "独立电压表",
            "NONE": "无电压表",
        }.get(table.voltage_type, table.voltage_type)
        self.volt_label.configure(text="电压模式: %s" % volt_text)

        if session is not None and session.path:
            size = os.path.getsize(session.path) if os.path.exists(session.path) else 0
            kind = "文件"
            if session.source_kind == "image" and session.image is not None:
                kind = kind_display_name(session.image.kind)
            elif session.source_kind == "dts":
                kind = "DTS 文本"
            self.file_label.configure(
                text="%s · %s (%.2f MB)" % (os.path.basename(session.path), kind,
                                            size / 1048576.0))

    def _update_slot_selector(self) -> None:
        for child in self._slot_holder.winfo_children():
            child.destroy()
        session = self.session
        if session is None or len(session.slots) <= 1:
            return
        ttk.Label(self._slot_holder, text="设备树:", style="Dim.TLabel").pack(side="left",
                                                                          padx=(0, 6))
        labels = [slot.label for slot in session.slots]
        combo = ttk.Combobox(self._slot_holder, textvariable=self._slot_var,
                             values=labels, state="readonly", width=26)
        combo.pack(side="left")
        if 0 <= session.current_index < len(labels):
            combo.current(session.current_index)
        combo.bind("<<ComboboxSelected>>", self._on_slot_selected)
        self._slot_combo = combo

    # ------------------------------------------------------------------
    # 文件操作
    # ------------------------------------------------------------------

    def open_file(self, path: Optional[str] = None) -> None:
        if not path:
            path = filedialog.askopenfilename(
                title="打开镜像或设备树文件",
                filetypes=[
                    ("镜像与设备树", "*.img *.dtb *.dts *.bin"),
                    ("boot 镜像", "*.img"),
                    ("设备树", "*.dtb"),
                    ("DTS 文本", "*.dts *.dtsi *.txt"),
                    ("所有文件", "*.*"),
                ])
        if not path:
            return

        self.set_status("正在解析 %s ..." % os.path.basename(path))
        self.update_idletasks()
        try:
            session = workspace.Session()
            session.open(path)
        except workspace.SessionError as exc:
            messagebox.showerror("打开失败", str(exc), parent=self)
            self.set_status("打开失败")
            return
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("打开失败", "解析文件时出错:\n%s" % exc, parent=self)
            self.set_status("打开失败")
            return

        self.session = session
        self._dts_loaded = False
        self._rebuild_tabs()
        self._update_info_bar()
        self._update_slot_selector()
        self._update_actions()

        slot = session.current_slot
        if slot is not None and slot.has_table:
            self.set_status("已加载 %s" % os.path.basename(path))
        elif session.slots:
            self.set_status("已加载 %s，但未识别到 GPU 频率表" % os.path.basename(path))
        else:
            self.set_status("已加载 %s" % os.path.basename(path))

    def reload_file(self) -> None:
        if self.session is None or not self.session.path:
            return
        if self.session.modified and not messagebox.askyesno(
                "重新载入", "当前有未保存的修改，确定要放弃并重新载入吗？", parent=self):
            return
        self.open_file(self.session.path)

    def save_file(self) -> None:
        if self.session is None:
            return
        if not self.session.path:
            self.save_file_as()
            return
        directory = os.path.dirname(self.session.path)
        name = self.session.default_output_name()
        self._save_to(os.path.join(directory, name))

    def save_file_as(self) -> None:
        if self.session is None:
            return
        default_name = self.session.default_output_name()
        if self.session.source_kind == "dts":
            filetypes = [("DTS 文本", "*.dts"), ("所有文件", "*.*")]
        elif self.session.source_kind == "dtb":
            filetypes = [("设备树二进制", "*.dtb"), ("所有文件", "*.*")]
        else:
            filetypes = [("镜像文件", "*.img"), ("所有文件", "*.*")]
        path = filedialog.asksaveasfilename(title="保存输出文件", initialfile=default_name,
                                            filetypes=filetypes,
                                            initialdir=os.path.dirname(self.session.path) or ".")
        if not path:
            return
        self._save_to(path)

    def _save_to(self, path: str) -> None:
        session = self.session
        if session is None:
            return
        self.set_status("正在生成输出文件...")
        self.update_idletasks()
        try:
            session.save(path)
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("保存失败", "生成输出文件时出错:\n%s" % exc, parent=self)
            self.set_status("保存失败")
            return

        self._update_actions()
        self.set_status("已保存: %s" % path)

        if session.source_kind == "image":
            partition = "boot"
            name = os.path.basename(path).lower()
            if "vendor_boot" in name:
                partition = "vendor_boot"
            elif "dtbo" in name:
                partition = "dtbo"

            avb_note = ""
            trailer = session.image.trailer if session.image is not None else b""
            if len(trailer) >= 64 and b"AVBf" in trailer[-64:]:
                avb_note = ("\n\n注意: 原镜像带有 AVB 签名（已原样保留在文件尾部），"
                            "但你修改了镜像内容，签名校验将不匹配。\n"
                            "若设备开启了启动验证且刷入后无法开机，请刷回原镜像。")

            messagebox.showinfo(
                "保存完成",
                "已生成输出文件:\n%s\n\n请确认文件无误后刷入设备：\n\n"
                "  fastboot flash %s %s\n\n"
                "刷机前建议先备份原分区，并确保已解锁 Bootloader。%s"
                % (path, partition, os.path.basename(path), avb_note),
                parent=self)
        else:
            messagebox.showinfo("保存完成", "已导出:\n%s" % path, parent=self)

    def export_dts(self) -> None:
        if self.session is None or self.session.table is None:
            return
        default = os.path.splitext(os.path.basename(self.session.path))[0] + ".dts"
        path = filedialog.asksaveasfilename(title="导出 DTS 文本", initialfile=default,
                                            filetypes=[("DTS 文本", "*.dts"), ("所有文件", "*.*")])
        if not path:
            return
        try:
            self.session.export_dts(path)
            self.set_status("已导出 DTS: %s" % path)
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("导出失败", str(exc), parent=self)

    def export_dtb(self) -> None:
        if self.session is None:
            return
        default = os.path.splitext(os.path.basename(self.session.path))[0] + ".dtb"
        if self.session.source_kind == "dtb":
            default = self.session.default_output_name()
        path = filedialog.asksaveasfilename(title="导出 DTB 文件", initialfile=default,
                                            filetypes=[("设备树二进制", "*.dtb"), ("所有文件", "*.*")])
        if not path:
            return
        try:
            self.session.export_dtb(path)
            self.set_status("已导出 DTB: %s" % path)
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("导出失败", str(exc), parent=self)

    def export_profile(self) -> None:
        if self.session is None or self.session.table is None:
            return
        import json
        default = os.path.splitext(os.path.basename(self.session.path))[0] + "_profile.json"
        path = filedialog.asksaveasfilename(title="导出调参方案", initialfile=default,
                                            filetypes=[("JSON", "*.json"), ("所有文件", "*.*")])
        if not path:
            return
        try:
            profile = self.session.export_profile()
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(profile, handle, ensure_ascii=False, indent=2)
            self.set_status("已导出调参方案: %s" % path)
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("导出失败", str(exc), parent=self)

    def import_profile(self) -> None:
        if self.session is None or self.session.table is None:
            messagebox.showinfo("导入调参方案", "请先打开要应用方案的镜像文件。", parent=self)
            return
        import json
        path = filedialog.askopenfilename(title="导入调参方案",
                                          filetypes=[("JSON", "*.json"), ("所有文件", "*.*")])
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as handle:
                profile = json.load(handle)
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("读取失败", "无法读取方案文件:\n%s" % exc, parent=self)
            return
        if not isinstance(profile, dict):
            messagebox.showerror("格式错误", "方案文件格式不正确。", parent=self)
            return
        if not messagebox.askyesno(
                "应用调参方案",
                "即将把方案中的频率/电压应用到当前表（按等级索引匹配）。\n\n"
                "当前设备: %s\n方案设备: %s\n\n继续？"
                % (self.session.table.chip_display, profile.get("chip", "未知")),
                parent=self):
            return
        try:
            applied, warnings = self.session.apply_profile(profile)
        except workspace.SessionError as exc:
            messagebox.showerror("应用失败", str(exc), parent=self)
            return
        self.on_session_changed("应用调参方案")
        message = "已应用 %d 处修改。" % applied
        if warnings:
            message += "\n\n注意:\n" + "\n".join("· " + item for item in warnings)
        messagebox.showinfo("应用完成", message, parent=self)

    # ------------------------------------------------------------------
    # 编辑
    # ------------------------------------------------------------------

    def undo(self) -> None:
        if self.session is not None and self.session.undo():
            self.on_session_changed("撤销")

    def redo(self) -> None:
        if self.session is not None and self.session.redo():
            self.on_session_changed("重做")

    def on_session_changed(self, description: str = "") -> None:
        """编辑之后的统一回调：刷新所有面板与状态。"""
        self._refresh_panels()
        self._update_info_bar()
        self._update_actions()
        self._dts_loaded = False
        if description:
            self.set_status("%s · 记得保存镜像" % description)

    # ------------------------------------------------------------------
    # 标签页
    # ------------------------------------------------------------------

    def _rebuild_tabs(self) -> None:
        for tab_id in self.notebook.tabs():
            self.notebook.forget(tab_id)
        self._level_panels.clear()
        self._curve_panel = None
        self._volt_panel = None
        self._dts_text = None

        session = self.session
        if session is None or not session.slots:
            self.notebook.add(self._welcome, text=" 欢迎 ")
            return

        slot = session.current_slot
        table = session.table

        if slot is not None and not slot.editable:
            frame = ttk.Frame(self.notebook, padding=30)
            ttk.Label(frame, text="该设备树无法解析:\n%s" % slot.error,
                      font=theme.font(11), justify="center").pack(expand=True)
            self.notebook.add(frame, text=" 错误 ")
            return

        if table is None or (not table.bins and not table.opps):
            frame = ttk.Frame(self.notebook, padding=30)
            ttk.Label(frame, text="在该设备树中未找到 GPU 频率/电压表。\n\n"
                                  "可能是: 该 DTB 不包含 GPU 调频信息（例如某些机型的\n"
                                  "DTB 在其他分区），或使用了未支持的格式。",
                      font=theme.font(11), justify="center").pack(expand=True)
            self.notebook.add(frame, text=" 无数据 ")
            return

        for index in range(len(table.bins)):
            panel = LevelTablePanel(self.notebook, self, index)
            panel.refresh()
            self._level_panels.append(panel)
            bin_ = table.bins[index]
            self.notebook.add(panel, text=" %s " % bin_.display_name())

        self._curve_panel = CurvePanel(self.notebook, self)
        self._curve_panel.refresh()
        self.notebook.add(self._curve_panel, text=" 曲线 ")

        if table.opps:
            self._volt_panel = VoltageTablePanel(self.notebook, self)
            self._volt_panel.refresh()
            self.notebook.add(self._volt_panel, text=" 电压表 ")

        dts_frame = ttk.Frame(self.notebook, padding=(10, 8))
        c = theme.COLORS
        self._dts_text = tk.Text(dts_frame, wrap="none", font=theme.mono_font(9),
                                 background=c["entry_bg"], foreground=c["fg"],
                                 relief="flat", highlightthickness=1,
                                 highlightbackground=c["border"])
        scroll_y = ttk.Scrollbar(dts_frame, orient="vertical", command=self._dts_text.yview)
        scroll_x = ttk.Scrollbar(dts_frame, orient="horizontal", command=self._dts_text.xview)
        self._dts_text.configure(yscrollcommand=scroll_y.set, xscrollcommand=scroll_x.set)
        self._dts_text.grid(row=0, column=0, sticky="nsew")
        scroll_y.grid(row=0, column=1, sticky="ns")
        scroll_x.grid(row=1, column=0, sticky="ew")
        dts_frame.rowconfigure(0, weight=1)
        dts_frame.columnconfigure(0, weight=1)
        self.notebook.add(dts_frame, text=" DTS ")

    def _on_tab_changed(self, _event=None) -> None:
        if self._dts_text is not None and not self._dts_loaded:
            selected = self.notebook.select()
            widget = self.nametowidget(selected) if selected else None
            if isinstance(widget, ttk.Frame) and self._dts_text in widget.winfo_children():
                self._load_dts_preview()

    def _load_dts_preview(self) -> None:
        session = self.session
        slot = session.current_slot if session else None
        if slot is None or slot.fdt is None or self._dts_text is None:
            return
        self.set_status("正在生成 DTS 预览...")
        self.update_idletasks()
        try:
            text = dts.fdt_to_dts(slot.fdt)
        except Exception as exc:  # noqa: BLE001
            text = "生成 DTS 失败: %s" % exc
        self._dts_text.configure(state="normal")
        self._dts_text.delete("1.0", "end")
        self._dts_text.insert("1.0", text)
        self._dts_text.configure(state="disabled")
        self._dts_loaded = True
        self.set_status("DTS 预览已生成（只读）")

    def _refresh_panels(self) -> None:
        for panel in self._level_panels:
            panel.refresh()
        if self._curve_panel is not None:
            self._curve_panel.refresh()
        if self._volt_panel is not None:
            self._volt_panel.refresh()

    def _on_slot_selected(self, _event=None) -> None:
        if self.session is None or self._slot_combo is None:
            return
        index = self._slot_combo.current()
        if 0 <= index < len(self.session.slots):
            self.session.current_index = index
            self._dts_loaded = False
            self._rebuild_tabs()
            self._update_info_bar()
            self._update_actions()
            self.set_status("已切换设备树: %s" % self.session.slots[index].label)

    # ------------------------------------------------------------------
    # 其他
    # ------------------------------------------------------------------

    def _show_backend_status(self) -> None:
        messagebox.showinfo(
            "压缩库状态",
            "内核压缩格式支持情况:\n\n%s\n\n"
            "lz4 / zstd 为可选依赖（仅在处理对应压缩格式的内核时需要）。\n"
            "如缺失可执行:\n  pip install lz4 zstandard"
            % compression.format_support_status(),
            parent=self)

    def _show_about(self) -> None:
        from .dialogs import ABOUT_TEXT
        messagebox.showinfo("关于", ABOUT_TEXT, parent=self)

    def _on_close(self) -> None:
        if self.session is not None and self.session.modified:
            answer = messagebox.askyesnocancel(
                "退出", "当前有未保存的修改，是否保存后退出？", parent=self)
            if answer is None:
                return
            if answer:
                self.save_file()
                if self.session.modified:
                    return
        self.destroy()
