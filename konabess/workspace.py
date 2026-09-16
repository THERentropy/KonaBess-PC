"""编辑会话管理：打开文件、解析、编辑、撤销/重做与导出。

会话（:class:`Session`）是一切的协调层：

- 打开 boot / vendor_boot / dtbo / dtb / dts 文件
- 定位其中所有可编辑的设备树（:class:`DtbSlot`），解析 GPU 频率/电压表
- 提供编辑操作（自动快照，支持撤销/重做）
- 保存时只替换被编辑的设备树，其余数据字节级原样保留
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from . import bootimg, dts, kernel_dtb
from .bootimg import ImageKind, PackedImage, load_image, sniff_kind
from .fdt import Fdt, FdtError, split_concatenated_dtbs
from .gpu_table import Bin, DtbEditor, GpuTable, Level, OppEntry, parse_table_from_fdt
from .kernel_dtb import KernelDtbInfo


class SessionError(Exception):
    """会话操作失败。"""


# ---------------------------------------------------------------------------
# 可编辑设备树槽位
# ---------------------------------------------------------------------------

@dataclass
class DtbSlot:
    """镜像/文件中的一个可编辑设备树。"""

    key: str                      # 唯一键
    label: str                    # 显示名
    data: bytes = b""             # 原始字节
    segment_name: str = ""        # 所属段（dtb / dtb_0 / kernel ...）
    kernel_index: int = -1        # 内核内嵌时的 FDT 索引
    fdt: Optional[Fdt] = None
    table: Optional[GpuTable] = None
    error: str = ""

    @property
    def editable(self) -> bool:
        return self.fdt is not None

    @property
    def has_table(self) -> bool:
        return self.table is not None and (bool(self.table.bins) or bool(self.table.opps))

    def reparse(self) -> None:
        self.fdt = None
        self.table = None
        self.error = ""
        try:
            self.fdt = Fdt.parse(self.data)
        except FdtError as exc:
            self.error = str(exc)
            return
        self.table = parse_table_from_fdt(self.fdt)


# ---------------------------------------------------------------------------
# 会话
# ---------------------------------------------------------------------------

@dataclass
class _HistoryEntry:
    description: str
    slot_key: str
    fdt_bytes: bytes


class Session:
    """一次编辑会话。"""

    MAX_HISTORY = 100

    def __init__(self) -> None:
        self.path: str = ""
        self.source_kind: str = ""            # image / dtb / dts
        self.source_data: bytes = b""
        self.image: Optional[PackedImage] = None
        self.kernel_info: Optional[KernelDtbInfo] = None
        self.slots: list[DtbSlot] = []
        self.current_index: int = 0
        self.modified: bool = False
        self.history: list[_HistoryEntry] = []
        self.redo_stack: list[_HistoryEntry] = []

    # -- 打开 ------------------------------------------------------------

    def open(self, path: str) -> None:
        file_path = Path(path)
        data = file_path.read_bytes()
        self.__init__()  # 重置
        self.path = str(file_path)
        self.source_data = data

        kind = sniff_kind(data)
        if kind is None:
            if dts.looks_like_dts(data):
                self._open_dts(data)
            else:
                raise SessionError(
                    "无法识别的文件格式。\n支持: boot.img / vendor_boot.img / dtbo.img / "
                    ".dtb / .dts")
        elif kind == ImageKind.DTB:
            self._open_dtb_file(data)
        else:
            self._open_image(data, kind)

        self._select_best_slot()

    # -- 各类型打开 ------------------------------------------------------

    def _open_dts(self, data: bytes) -> None:
        for encoding in ("utf-8", "gbk", "latin-1"):
            try:
                text = data.decode(encoding)
                break
            except UnicodeDecodeError:
                continue
        else:  # pragma: no cover
            raise SessionError("无法解码 DTS 文本")
        try:
            fdt = dts.compile_dts(text)
        except dts.DtsCompileError as exc:
            raise SessionError(
                "DTS 编译失败：%s\n\n提示：包含 label 引用等高级语法时，"
                "请先用 dtc 工具编译为 DTB 后导入。" % exc)
        self.source_kind = "dts"
        self.slots = [DtbSlot(key="file:0", label="DTS 设备树", data=fdt.to_bytes())]
        self.slots[0].fdt = fdt
        self.slots[0].table = parse_table_from_fdt(fdt)

    def _open_dtb_file(self, data: bytes) -> None:
        self.source_kind = "dtb"
        spans = split_concatenated_dtbs(data)
        if not spans:
            raise SessionError("文件中未找到 DTB 数据")
        for i, (offset, size) in enumerate(spans):
            label = "DTB #%d" % i
            if len(spans) > 1:
                label += " (0x%x, %d 字节)" % (offset, size)
            slot = DtbSlot(key="file:%d" % i, label=label, data=data[offset:offset + size])
            slot.reparse()
            self.slots.append(slot)

    def _open_image(self, data: bytes, kind: ImageKind) -> None:
        self.source_kind = "image"
        image = load_image(data, kind)
        self.image = image

        if image.kind == ImageKind.DTBO:
            for i, seg in enumerate(image.segments):
                meta = seg.meta
                label = "dtbo 条目 %d (id=0x%x rev=0x%x)" % (i, meta.get("id", 0), meta.get("rev", 0))
                slot = DtbSlot(key="dtbo:%d" % i, label=label, data=seg.data,
                               segment_name=seg.name)
                slot.reparse()
                self.slots.append(slot)
            return

        # boot / vendor_boot：优先 dtb 段
        dtb_seg = image.segment("dtb")
        if dtb_seg is not None and dtb_seg.data:
            spans = split_concatenated_dtbs(dtb_seg.data)
            if not spans:
                spans = [(0, len(dtb_seg.data))]
            for i, (offset, size) in enumerate(spans):
                label = "dtb 段 #%d" % i if len(spans) > 1 else "dtb 段"
                slot = DtbSlot(key="segment:%d" % i, label=label,
                               data=dtb_seg.data[offset:offset + size],
                               segment_name="dtb")
                slot.reparse()
                self.slots.append(slot)

        # 没有可用 dtb 段时，尝试内核内嵌 DTB
        if not any(slot.has_table for slot in self.slots):
            kernel_seg = image.segment("kernel")
            if kernel_seg is not None and kernel_seg.data:
                try:
                    info = kernel_dtb.inspect_kernel(kernel_seg.data)
                except Exception:
                    info = None
                if info is not None:
                    self.kernel_info = info
                    for i, (offset, size) in enumerate(info.fdt_spans):
                        label = "内核内嵌 DTB #%d" % i
                        if len(info.fdt_spans) > 1:
                            label += " (%s)" % info.format_label()
                        slot = DtbSlot(key="kernel:%d" % i, label=label,
                                       data=info.raw_kernel[offset:offset + size],
                                       segment_name="kernel", kernel_index=i)
                        slot.reparse()
                        self.slots.append(slot)

    # -- 槽位管理 --------------------------------------------------------

    def _select_best_slot(self) -> None:
        for i, slot in enumerate(self.slots):
            if slot.has_table and slot.table and slot.table.bins:
                self.current_index = i
                return
        for i, slot in enumerate(self.slots):
            if slot.has_table:
                self.current_index = i
                return
        self.current_index = 0

    @property
    def current_slot(self) -> Optional[DtbSlot]:
        if not self.slots:
            return None
        if self.current_index >= len(self.slots):
            self.current_index = 0
        return self.slots[self.current_index]

    @property
    def table(self) -> Optional[GpuTable]:
        slot = self.current_slot
        return slot.table if slot else None

    def select_slot(self, key: str) -> bool:
        for i, slot in enumerate(self.slots):
            if slot.key == key:
                self.current_index = i
                return True
        return False

    def _slot_by_key(self, key: str) -> Optional[DtbSlot]:
        for slot in self.slots:
            if slot.key == key:
                return slot
        return None

    # -- 编辑框架 --------------------------------------------------------

    def _apply(self, action: Callable[[DtbSlot, GpuTable], bool], description: str) -> bool:
        slot = self.current_slot
        table = self.table
        if slot is None or slot.fdt is None or table is None:
            return False
        try:
            snapshot = slot.fdt.to_bytes()
        except FdtError:
            return False
        try:
            ok = action(slot, table)
        except Exception:
            ok = False
        if not ok:
            # 回滚到快照
            try:
                slot.fdt = Fdt.parse(snapshot)
            except FdtError:
                pass
            slot.reparse()
            return False

        self.history.append(_HistoryEntry(description, slot.key, snapshot))
        if len(self.history) > self.MAX_HISTORY:
            self.history.pop(0)
        self.redo_stack.clear()
        self.modified = True
        self._refresh_slot(slot)
        return True

    def _refresh_slot(self, slot: DtbSlot) -> None:
        """编辑之后重建解析结果（保留 fdt，重新生成 table）。"""
        slot.table = parse_table_from_fdt(slot.fdt) if slot.fdt else None

    # -- 编辑操作 --------------------------------------------------------

    def _bin(self, table: GpuTable, bin_index: int) -> Optional[Bin]:
        if 0 <= bin_index < len(table.bins):
            return table.bins[bin_index]
        return None

    def _level(self, table: GpuTable, bin_index: int, level_index: int) -> Optional[Level]:
        bin_ = self._bin(table, bin_index)
        if bin_ is None or not (0 <= level_index < len(bin_.levels)):
            return None
        return bin_.levels[level_index]

    def set_level_freq(self, bin_index: int, level_index: int, freq: int) -> bool:
        def action(slot: DtbSlot, table: GpuTable) -> bool:
            level = self._level(table, bin_index, level_index)
            if level is None:
                return False
            return DtbEditor.set_level_param(level, "qcom,gpu-freq", freq)

        return self._apply(action, "修改频率")

    def set_level_volt(self, bin_index: int, level_index: int, volt: int) -> bool:
        def action(slot: DtbSlot, table: GpuTable) -> bool:
            level = self._level(table, bin_index, level_index)
            if level is None:
                return False
            key = level.volt_key or table.voltage_key
            return DtbEditor.set_level_param(level, key, volt)

        return self._apply(action, "修改电压")

    def set_level_bus(self, bin_index: int, level_index: int, key: str, value: int) -> bool:
        def action(slot: DtbSlot, table: GpuTable) -> bool:
            level = self._level(table, bin_index, level_index)
            if level is None:
                return False
            return DtbEditor.set_level_param(level, key, value)

        return self._apply(action, "修改总线档位")

    def set_level_param(self, bin_index: int, level_index: int, key: str, value: int) -> bool:
        def action(slot: DtbSlot, table: GpuTable) -> bool:
            level = self._level(table, bin_index, level_index)
            if level is None:
                return False
            return DtbEditor.set_level_param(level, key, value)

        return self._apply(action, "修改 %s" % key)

    def update_levels_batch(self, updates: list[tuple[int, int, dict[str, int]]]) -> bool:
        """批量修改多个等级（一次撤销步骤）。"""
        def action(slot: DtbSlot, table: GpuTable) -> bool:
            count = 0
            for bin_index, level_index, params in updates:
                level = self._level(table, bin_index, level_index)
                if level is None:
                    continue
                for key, value in params.items():
                    if key in ("qcom,level", "qcom,cx-level") and not level.volt_key:
                        key = table.voltage_key
                    if DtbEditor.set_level_param(level, key, value):
                        count += 1
            return count > 0

        return self._apply(action, "批量修改 (%d 项)" % len(updates))

    def add_level(self, bin_index: int, at_top: bool = False) -> bool:
        def action(slot: DtbSlot, table: GpuTable) -> bool:
            bin_ = self._bin(table, bin_index)
            if bin_ is None:
                return False
            return DtbEditor.add_level(bin_, at_top)

        return self._apply(action, "添加等级")

    def delete_level(self, bin_index: int, level_index: int) -> bool:
        def action(slot: DtbSlot, table: GpuTable) -> bool:
            bin_ = self._bin(table, bin_index)
            if bin_ is None:
                return False
            return DtbEditor.delete_level(bin_, level_index)

        return self._apply(action, "删除等级")

    def duplicate_level(self, bin_index: int, level_index: int) -> bool:
        def action(slot: DtbSlot, table: GpuTable) -> bool:
            bin_ = self._bin(table, bin_index)
            if bin_ is None:
                return False
            return DtbEditor.duplicate_level(bin_, level_index)

        return self._apply(action, "复制等级")

    def set_opp_volt(self, opp_index: int, volt: int) -> bool:
        def action(slot: DtbSlot, table: GpuTable) -> bool:
            if not (0 <= opp_index < len(table.opps)):
                return False
            return DtbEditor.set_opp_volt(table.opps[opp_index], volt)

        return self._apply(action, "修改电压表")

    def set_bin_header(self, bin_index: int, key: str, value: int) -> bool:
        def action(slot: DtbSlot, table: GpuTable) -> bool:
            bin_ = self._bin(table, bin_index)
            if bin_ is None:
                return False
            return DtbEditor.set_bin_header_param(bin_, key, value)

        return self._apply(action, "修改 %s" % key)

    # -- 撤销 / 重做 -----------------------------------------------------

    @property
    def can_undo(self) -> bool:
        return bool(self.history)

    @property
    def can_redo(self) -> bool:
        return bool(self.redo_stack)

    def undo(self) -> bool:
        slot = self.current_slot
        if not self.history or slot is None:
            return False
        entry = self.history.pop()
        target = self._slot_by_key(entry.slot_key)
        if target is None or target.fdt is None:
            return False
        if target is slot:
            self.redo_stack.append(_HistoryEntry(entry.description, entry.slot_key,
                                                 slot.fdt.to_bytes()))
        else:
            self.redo_stack.append(_HistoryEntry(entry.description, entry.slot_key,
                                                 target.fdt.to_bytes()))
        try:
            target.fdt = Fdt.parse(entry.fdt_bytes)
        except FdtError:
            return False
        self._refresh_slot(target)
        self.select_slot(entry.slot_key)
        self.modified = True
        return True

    def redo(self) -> bool:
        slot = self.current_slot
        if not self.redo_stack or slot is None:
            return False
        entry = self.redo_stack.pop()
        target = self._slot_by_key(entry.slot_key)
        if target is None or target.fdt is None:
            return False
        self.history.append(_HistoryEntry(entry.description, entry.slot_key,
                                          target.fdt.to_bytes()))
        try:
            target.fdt = Fdt.parse(entry.fdt_bytes)
        except FdtError:
            return False
        self._refresh_slot(target)
        self.select_slot(entry.slot_key)
        self.modified = True
        return True

    def last_action(self) -> str:
        return self.history[-1].description if self.history else ""

    # -- 保存 / 导出 -----------------------------------------------------

    def build_output(self) -> bytes:
        """生成当前会话的输出数据（与源文件类型对应）。"""
        if self.source_kind == "image":
            return self._build_image()
        if self.source_kind == "dtb":
            return self._build_dtb_file()
        return self._build_dts_text().encode("utf-8")

    def default_output_name(self) -> str:
        if not self.path:
            return "output.img"
        stem = Path(self.path).stem
        suffix = Path(self.path).suffix or ".img"
        if self.source_kind == "image":
            if self.image is not None and self.image.kind == ImageKind.DTBO:
                return "%s_new%s" % (stem, suffix)
            return "%s_new%s" % (stem, suffix)
        if self.source_kind == "dts":
            return "%s_modified%s" % (stem, suffix)
        return "%s_modified%s" % (stem, suffix)

    def _build_image(self) -> bytes:
        image = self.image
        if image is None:
            raise SessionError("没有可导出的镜像")

        # 内核内嵌 DTB：统一重建 kernel 段
        if self.kernel_info is not None:
            kernel_seg = image.segment("kernel")
            if kernel_seg is not None:
                replacements: dict[int, bytes] = {}
                for slot in self.slots:
                    if slot.kernel_index >= 0 and slot.fdt is not None:
                        replacements[slot.kernel_index] = slot.fdt.to_bytes()
                if replacements:
                    kernel_seg.data = kernel_dtb.replace_fdts_in_kernel(
                        self.kernel_info, replacements)

        # dtb 段（可能由多个 DTB 拼接）
        dtb_slots = [s for s in self.slots if s.segment_name == "dtb" and s.fdt is not None]
        if dtb_slots:
            seg = image.segment("dtb")
            if seg is not None:
                seg.data = b"".join(s.fdt.to_bytes() for s in dtb_slots)

        # dtbo 条目
        if image.kind == ImageKind.DTBO:
            for slot in self.slots:
                if slot.fdt is None:
                    continue
                seg = image.segment(slot.segment_name)
                if seg is not None:
                    seg.data = slot.fdt.to_bytes()

        return image.pack()

    def _build_dtb_file(self) -> bytes:
        chunks: list[bytes] = []
        for slot in self.slots:
            chunks.append(self._serialize_slot(slot))
        return b"".join(chunks)

    def _build_dts_text(self) -> str:
        slot = self.current_slot
        if slot is None or slot.fdt is None:
            raise SessionError("没有可导出的设备树")
        return dts.fdt_to_dts(slot.fdt)

    def _serialize_slot(self, slot: DtbSlot) -> bytes:
        """把槽位的当前设备树序列化为字节。"""
        if slot.fdt is None:
            return slot.data
        return slot.fdt.to_bytes()

    def save(self, path: str) -> None:
        data = self.build_output()
        out = Path(path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(data)
        self.modified = False

    def export_dts(self, path: str) -> None:
        """把当前设备树导出为 DTS 文本。"""
        text = self._build_dts_text()
        Path(path).write_text(text, encoding="utf-8")

    def export_dtb(self, path: str) -> None:
        """把当前设备树导出为 DTB 二进制。"""
        slot = self.current_slot
        if slot is None or slot.fdt is None:
            raise SessionError("没有可导出的设备树")
        Path(path).write_bytes(slot.fdt.to_bytes())

    # -- 配置（调参方案）导入导出 -----------------------------------------

    def export_profile(self) -> dict:
        table = self.table
        if table is None:
            raise SessionError("没有可导出的频率表")
        return {
            "version": 1,
            "chip": table.chip_display,
            "gpu": table.gpu_model,
            "preset": table.preset_name,
            "level_count": table.level_count,
            "voltage_type": table.voltage_type,
            "bins": [
                {
                    "id": bin_.id,
                    "speed_bin": bin_.speed_bin,
                    "levels": [
                        {
                            "freq": level.freq,
                            "volt": level.volt,
                            "bus_min": level.bus_min,
                            "bus_max": level.bus_max,
                            "bus_freq": level.bus_freq,
                        }
                        for level in bin_.levels
                    ],
                }
                for bin_ in table.bins
            ],
            "opps": [{"freq": opp.freq, "volt": opp.volt} for opp in table.opps],
        }

    def apply_profile(self, profile: dict) -> tuple[int, list[str]]:
        """把调参方案应用到当前表：返回 (应用数量, 警告列表)。"""
        table = self.table
        if table is None:
            raise SessionError("没有可应用的频率表")

        warnings: list[str] = []
        updates: list[tuple[int, int, dict[str, int]]] = []

        profile_bins = profile.get("bins") or []
        for bin_index, bin_data in enumerate(profile_bins):
            if bin_index >= len(table.bins):
                warnings.append("方案包含 %d 个 bin，但当前只有 %d 个，多余的已忽略"
                                % (len(profile_bins), len(table.bins)))
                break
            levels = bin_data.get("levels") or []
            current = table.bins[bin_index].levels
            if len(levels) != len(current):
                warnings.append("Bin %d 等级数不一致（方案 %d，当前 %d），按索引覆盖"
                                % (bin_index, len(levels), len(current)))
            for level_index, level_data in enumerate(levels):
                if level_index >= len(current):
                    break
                params: dict[str, int] = {}
                freq = level_data.get("freq")
                volt = level_data.get("volt")
                if isinstance(freq, int) and freq > 0:
                    params["qcom,gpu-freq"] = freq
                if isinstance(volt, int) and volt > 0:
                    level = current[level_index]
                    if table.voltage_type == "OPP_TABLE" and not level.volt_key:
                        pass  # 电压在独立表中处理
                    else:
                        params[level.volt_key or table.voltage_key] = volt
                for key in ("qcom,bus-min", "qcom,bus-max", "qcom,bus-freq"):
                    value = level_data.get(key)
                    if isinstance(value, int) and value >= 0:
                        params[key] = value
                if params:
                    updates.append((bin_index, level_index, params))

        if table.opps:
            profile_opps = {int(item["freq"]): int(item["volt"])
                            for item in (profile.get("opps") or [])
                            if isinstance(item.get("freq"), int)}
            for opp_index, opp in enumerate(table.opps):
                if opp.freq in profile_opps and profile_opps[opp.freq] > 0:
                    updates.append((-1, opp_index, {"opp": profile_opps[opp.freq]}))

        applied = 0
        if updates:
            def action(slot: DtbSlot, table_: GpuTable) -> bool:
                nonlocal applied
                for bin_index, level_index, params in updates:
                    if bin_index == -1:
                        if 0 <= level_index < len(table_.opps):
                            volt = params.get("opp")
                            if volt and DtbEditor.set_opp_volt(table_.opps[level_index], volt):
                                applied += 1
                        continue
                    level = self._level(table_, bin_index, level_index)
                    if level is None:
                        continue
                    for key, value in params.items():
                        if DtbEditor.set_level_param(level, key, value):
                            applied += 1
                return applied > 0

            if not self._apply(action, "应用调参方案"):
                raise SessionError("应用调参方案失败")

        return applied, warnings
