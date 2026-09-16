"""GPU 频率/电压表：数据模型、DTB 解析与编辑操作。

对应 DTS 中的两处结构：

1. **频率表（内联电压）** —— 骁龙 8 系新平台::

       qcom,gpu-pwrlevel-bins {
           compatible = "qcom,gpu-pwrlevels-bins";
           qcom,gpu-pwrlevels-0 {              // 一个 speed bin
               qcom,speed-bin = <0x0>;
               qcom,initial-pwrlevel = <0xa>;  // bin header（指针）
               qcom,gpu-pwrlevel@0 {           // 一个频率等级
                   qcom,gpu-freq = <0x45243200>;   // 频率 (Hz)
                   qcom,level = <0x1c0>;           // 电压角 (RPMh corner)
                   qcom,bus-min / bus-max / bus-freq = <0xb>;
                   qcom,acd-level = <0xa8285ffd>;
                   reg = <0x0>;
               };
               ...
           };
           qcom,gpu-pwrlevels-1 { ... };
       };

2. **独立电压表（OPP table）** —— 骁龙 855/865 等旧平台::

       gpu-opp-table {
           compatible = "operating-points-v2";
           opp-600000000 { opp-hz = /bits/ 64 <0x0 0x23c34600>; opp-microvolt = <0x181>; };
           ...
       };
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from . import chips
from .fdt import Fdt, FdtNode, FdtProp

#: 电压属性名（优先 qcom,level，其次 qcom,cx-level）
VOLT_KEYS = ("qcom,level", "qcom,cx-level")

#: 总线档位属性
BUS_KEYS = ("qcom,bus-min", "qcom,bus-max", "qcom,bus-freq")

_BIN_NAME_RE = re.compile(r"^qcom,gpu-pwrlevels?(-\d+)?$")
_LEVEL_NAME_RE = re.compile(r"^qcom,gpu-pwrlevel@(\d+)$")


# ---------------------------------------------------------------------------
# 数据模型
# ---------------------------------------------------------------------------

@dataclass
class Level:
    """一个 GPU 频率等级（qcom,gpu-pwrlevel@N）。"""

    index: int = 0
    freq: int = -1            # Hz（-1 表示属性不存在）
    volt: int = -1            # 电压角数值（-1 表示无内联电压）
    volt_key: str = ""        # 实际使用的电压属性名
    bus_min: int = -1
    bus_max: int = -1
    bus_freq: int = -1
    extra: dict[str, int] = field(default_factory=dict)
    source: Any = None        # FdtNode（DTB 模式）或行号区间（DTS 模式）

    @property
    def freq_mhz(self) -> float:
        return self.freq / 1_000_000.0 if self.freq and self.freq > 0 else 0.0

    @property
    def is_valid(self) -> bool:
        """占位节点（频率为 0）不算有效等级。"""
        return bool(self.freq and self.freq > 0)

    def format_freq(self) -> str:
        if not self.is_valid:
            return "—"
        value = self.freq / 1_000_000.0
        if value >= 1000:
            return "%.2f GHz" % (value / 1000.0)
        return "%.0f MHz" % value


@dataclass
class Bin:
    """一个 speed bin（qcom,gpu-pwrlevels-N）。"""

    id: int = 0
    header: dict[str, int] = field(default_factory=dict)
    levels: list[Level] = field(default_factory=list)
    source: Any = None        # FdtNode 或 None

    @property
    def speed_bin(self) -> Optional[int]:
        return self.header.get("qcom,speed-bin")

    @property
    def initial_pwrlevel(self) -> Optional[int]:
        return self.header.get("qcom,initial-pwrlevel")

    def display_name(self) -> str:
        parts = ["Bin %d" % self.id]
        if self.speed_bin is not None:
            parts.append("speed-bin=0x%x" % self.speed_bin)
        return " / ".join(parts)


@dataclass
class OppEntry:
    """独立电压表中的一条记录。"""

    freq: int = 0
    volt: int = 0
    source: Any = None        # FdtNode 或 None

    def format_freq(self) -> str:
        value = self.freq / 1_000_000.0
        if value >= 1000:
            return "%.2f GHz" % (value / 1000.0)
        return "%.0f MHz" % value


@dataclass
class GpuTable:
    """一个 DTB 中解析出的 GPU 调频数据。"""

    bins: list[Bin] = field(default_factory=list)
    opps: list[OppEntry] = field(default_factory=list)
    gpu_model: str = ""
    chip_id: int = 0
    detected_model: str = ""
    codename: str = ""
    compatible: str = ""
    voltage_type: str = "NONE"      # INLINE_LEVEL / OPP_TABLE / NONE
    level_count: int = 480
    bin_container: Any = None       # bins 容器节点（DTB 模式）
    opp_container: Any = None       # OPP 表节点（DTB 模式）
    gpu_node: Any = None

    # -- 展示信息 --------------------------------------------------------

    @property
    def chip_display(self) -> str:
        return chips.chip_display_name(self.detected_model, self.codename)

    @property
    def is_multi_bin(self) -> bool:
        return len(self.bins) > 1

    @property
    def preset_name(self) -> str:
        return chips.infer_preset(self.detected_model or self.codename, self.level_count)

    def level_map(self) -> dict[int, str]:
        return chips.resolve_preset(self.preset_name)

    def volt_label(self, value: int) -> Optional[str]:
        return chips.level_label(self.level_map(), value)

    def volt_text(self, value: int, opp_freq: Optional[int] = None) -> str:
        """生成下拉/表格中显示的电压文本。"""
        if value is None or value < 0:
            if opp_freq is not None:
                for opp in self.opps:
                    if opp.freq == opp_freq:
                        return str(opp.volt)
            return ""
        label = self.volt_label(value)
        return label if label else str(value)

    @property
    def voltage_key(self) -> str:
        """当前使用的电压属性名（用于写回）。"""
        for bin_ in self.bins:
            for level in bin_.levels:
                if level.volt_key:
                    return level.volt_key
        return VOLT_KEYS[0]

    def all_levels(self) -> list[Level]:
        result: list[Level] = []
        for bin_ in self.bins:
            result.extend(bin_.levels)
        return result

    def summary(self) -> str:
        lines = [
            "芯片: %s" % self.chip_display,
            "GPU: %s" % (self.gpu_model or "未知"),
            "电压模式: %s" % {
                "INLINE_LEVEL": "内联电压（qcom,level）",
                "OPP_TABLE": "独立电压表（OPP table）",
                "NONE": "无电压表",
            }.get(self.voltage_type, self.voltage_type),
            "Bins: %d" % len(self.bins),
        ]
        for bin_ in self.bins:
            lines.append("  %s: %d 个等级" % (bin_.display_name(), len(bin_.levels)))
        if self.opps:
            lines.append("电压表: %d 条记录" % len(self.opps))
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# 从 FDT 解析
# ---------------------------------------------------------------------------

def _prop_int(node: FdtNode, name: str) -> Optional[int]:
    prop = node.get_prop(name)
    if prop is None:
        return None
    return prop.as_u64()


def find_gpu_node(root: FdtNode) -> Optional[FdtNode]:
    """定位 GPU 节点（kgsl-3d0）。"""
    for node in root.walk():
        if "kgsl-3d0" in node.name:
            return node
        compat = node.get_prop("compatible")
        if compat is not None and "qcom,kgsl-3d0" in compat.as_str():
            return node
    for node in root.walk():
        if node.name.startswith("gpu@"):
            return node
    for node in root.walk():
        compat = node.get_prop("compatible")
        if compat is not None and "adreno" in compat.as_str().lower():
            return node
    return None


def _is_bin_container(node: FdtNode) -> bool:
    if node.name in ("qcom,gpu-pwrlevel-bins", "qcom,gpu-pwrlevels-bins"):
        return True
    compat = node.get_prop("compatible")
    if compat is not None and "gpu-pwrlevels-bins" in compat.as_str():
        return True
    return False


def _is_bin_node(node: FdtNode) -> bool:
    if _BIN_NAME_RE.match(node.name):
        return True
    compat = node.get_prop("compatible")
    if compat is not None:
        text = compat.as_str()
        if "qcom,gpu-pwrlevels" in text and "bins" not in text:
            return True
    return False


def _is_level_node(node: FdtNode) -> bool:
    return _LEVEL_NAME_RE.match(node.name) is not None


def _level_sort_key(node: FdtNode) -> int:
    reg = _prop_int(node, "reg")
    if reg is not None:
        return reg
    match = _LEVEL_NAME_RE.match(node.name)
    return int(match.group(1)) if match else 0


def _parse_level(node: FdtNode, index: int) -> Level:
    level = Level(index=index, source=node)
    for prop in node.props:
        if prop.name == "qcom,gpu-freq":
            value = prop.as_u64()
            level.freq = value if value is not None else -1
        elif prop.name in VOLT_KEYS:
            value = prop.as_u64()
            if value is not None and (level.volt < 0 or prop.name == VOLT_KEYS[0]):
                level.volt = value
                level.volt_key = prop.name
        elif prop.name == "qcom,bus-min":
            level.bus_min = prop.as_u64() or -1
        elif prop.name == "qcom,bus-max":
            level.bus_max = prop.as_u64() or -1
        elif prop.name == "qcom,bus-freq":
            level.bus_freq = prop.as_u64() or -1
        elif prop.name == "reg" or prop.name.startswith("#"):
            continue
        elif len(prop.data) in (4, 8):
            value = prop.as_u64()
            if value is not None:
                level.extra[prop.name] = value
    return level


def _find_bin_nodes(root: FdtNode) -> tuple[list[FdtNode], Optional[FdtNode]]:
    """返回 (bin 节点列表, bins 容器节点)。"""
    container: Optional[FdtNode] = None
    for node in root.walk():
        if _is_bin_container(node):
            container = node
            break
    if container is not None:
        bins = [child for child in container.children if _is_bin_node(child)]
        return bins, container

    bins: list[FdtNode] = []

    def recurse(node: FdtNode) -> None:
        if _is_bin_node(node):
            bins.append(node)
            return
        for child in node.children:
            recurse(child)

    recurse(root)
    return bins, None


def _is_opp_table(node: FdtNode) -> bool:
    name = node.name.lower()
    compat = node.get_prop("compatible")
    compat_text = compat.as_str().lower() if compat is not None else ""
    return ("opp-table" in name or "opp-table" in compat_text
            or "operating-points" in compat_text or "operating-points" in name)


def _is_gpu_opp_table(node: FdtNode, gpu_node: Optional[FdtNode]) -> bool:
    """判断 OPP 表是否与 GPU 相关（位于 GPU 子树或命名含 gpu/gfx）。"""
    if gpu_node is not None:
        probe = node
        while probe is not None:
            if probe is gpu_node:
                return True
            probe = probe.parent
    name = node.name.lower()
    compat = node.get_prop("compatible")
    compat_text = compat.as_str().lower() if compat is not None else ""
    return "gpu" in name or "gfx" in name or "gpu" in compat_text


def _find_opp_table(root: FdtNode, gpu_node: Optional[FdtNode]) -> tuple[Optional[FdtNode], bool]:
    """定位独立电压表，返回 (节点, 是否与 GPU 相关)。

    优先选择 GPU 子树内或命名含 gpu/gfx 的表，避免把 SD 卡控制器等
    无关的 opp-table 误判为 GPU 电压表。
    """
    for node in root.walk():
        if _is_opp_table(node) and _is_gpu_opp_table(node, gpu_node):
            return node, True
    for node in root.walk():
        if _is_opp_table(node):
            return node, False
    return None, False


def _parse_opps(table_node: FdtNode) -> list[OppEntry]:
    opps: list[OppEntry] = []
    for child in table_node.children:
        hz = _prop_int(child, "opp-hz")
        if hz is None:
            continue
        volt = _prop_int(child, "opp-microvolt")
        opps.append(OppEntry(freq=hz, volt=volt if volt is not None else 0, source=child))
    opps.sort(key=lambda o: o.freq, reverse=True)
    return opps


def parse_table_from_fdt(fdt: Fdt) -> Optional[GpuTable]:
    """从设备树中解析 GPU 频率/电压表；找不到时返回 None。"""
    table = GpuTable()
    root = fdt.root

    model_prop = root.get_prop("model")
    if model_prop is not None:
        table.detected_model = model_prop.as_str()
    compat_prop = root.get_prop("compatible")
    if compat_prop is not None:
        table.compatible = compat_prop.as_str()
        match = re.search(r"qcom,([a-z0-9\-]+)", table.compatible)
        if match:
            table.codename = match.group(1)

    gpu_node = find_gpu_node(root)
    if gpu_node is not None:
        table.gpu_node = gpu_node
        model_prop = gpu_node.get_prop("qcom,gpu-model")
        if model_prop is not None:
            if model_prop.is_string():
                table.gpu_model = model_prop.as_str()
            else:
                value = model_prop.as_u64()
                if value is not None:
                    table.gpu_model = str(value)
        chip_id = _prop_int(gpu_node, "qcom,chipid")
        if chip_id is not None:
            table.chip_id = chip_id

    bin_nodes, container = _find_bin_nodes(root)
    table.bin_container = container
    max_level = 0
    for bin_index, bin_node in enumerate(bin_nodes):
        suffix_match = re.search(r"-(\d+)$", bin_node.name)
        bin_id = int(suffix_match.group(1)) if suffix_match else bin_index
        bin_ = Bin(id=bin_id, source=bin_node)

        for prop in bin_node.props:
            if prop.name.startswith("#") or prop.name == "compatible":
                continue
            if len(prop.data) in (4, 8):
                value = prop.as_u64()
                if value is not None:
                    bin_.header[prop.name] = value

        level_nodes = sorted((c for c in bin_node.children if _is_level_node(c)), key=_level_sort_key)
        for index, level_node in enumerate(level_nodes):
            level = _parse_level(level_node, index)
            bin_.levels.append(level)
            if level.volt > max_level:
                max_level = level.volt

        table.bins.append(bin_)

    opp_node, gpu_related = _find_opp_table(root, gpu_node)
    opps: list[OppEntry] = []
    if opp_node is not None:
        opps = _parse_opps(opp_node)

    has_inline = any(level.volt > 0 for level in table.all_levels())

    if opps and (gpu_related or not has_inline):
        table.opp_container = opp_node
        table.opps = opps
        table.voltage_type = "OPP_TABLE"
    elif has_inline:
        table.voltage_type = "INLINE_LEVEL"
    else:
        table.voltage_type = "NONE"

    table.level_count = max_level if max_level > 0 else 480

    if not table.bins and not table.opps:
        return None
    if not table.bins:
        # 仅识别到电压表，仍可编辑（部分机型）
        return table
    return table


# ---------------------------------------------------------------------------
# DTB 编辑操作
# ---------------------------------------------------------------------------

class DtbEditor:
    """在 FDT 树上执行的编辑操作（值为整数，直接写入原始字节）。"""

    # -- 基础写入 --------------------------------------------------------

    @staticmethod
    def set_level_param(level: Level, key: str, value: int) -> bool:
        node = level.source
        if not isinstance(node, FdtNode):
            return False
        if key in VOLT_KEYS and node.get_prop(key) is None:
            # 写电压时自动选择节点中已存在的电压属性，否则新建 qcom,level
            fallback = VOLT_KEYS[0]
            node.set_prop_bits(fallback, value)
            level.volt_key = fallback
            return True
        node.set_prop_bits(key, value)
        if key in VOLT_KEYS:
            level.volt_key = key
        return True

    @staticmethod
    def set_bin_header_param(bin_: Bin, key: str, value: int) -> bool:
        node = bin_.source
        if not isinstance(node, FdtNode):
            return False
        node.set_prop_bits(key, value)
        bin_.header[key] = value
        return True

    @staticmethod
    def set_opp_volt(opp: OppEntry, value: int) -> bool:
        node = opp.source
        if not isinstance(node, FdtNode):
            return False
        node.set_prop_bits("opp-microvolt", value)
        opp.volt = value
        return True

    # -- 结构操作 --------------------------------------------------------

    @staticmethod
    def add_level(bin_: Bin, at_top: bool = False) -> bool:
        """复制一个有效等级插入到 bin 中（参照手机版逻辑选择模板）。"""
        bin_node = bin_.source
        if not isinstance(bin_node, FdtNode):
            return False

        valid = [lv for lv in bin_.levels if lv.is_valid and isinstance(lv.source, FdtNode)]
        if not valid:
            return False
        template = valid[0] if at_top else valid[-1]
        clone = template.source.deep_copy()

        level_children = [c for c in bin_node.children if _is_level_node(c)]
        if not level_children:
            bin_node.add_child(clone)
        elif at_top:
            index = bin_node.children.index(level_children[0])
            bin_node.add_child(clone, index)
        else:
            index = bin_node.children.index(level_children[-1]) + 1
            bin_node.add_child(clone, index)

        _renumber_levels(bin_node)
        if at_top:
            _shift_pointers(bin_node, lambda v: v + 1)
        return True

    @staticmethod
    def delete_level(bin_: Bin, index: int) -> bool:
        if index < 0 or index >= len(bin_.levels):
            return False
        level = bin_.levels[index]
        bin_node = bin_.source
        if not isinstance(bin_node, FdtNode) or not isinstance(level.source, FdtNode):
            return False
        bin_node.remove_child(level.source)
        _renumber_levels(bin_node)
        _shift_pointers(bin_node, lambda v: v - 1 if v > index else v)
        return True

    @staticmethod
    def duplicate_level(bin_: Bin, index: int) -> bool:
        """在指定等级后复制一份。"""
        if index < 0 or index >= len(bin_.levels):
            return False
        bin_node = bin_.source
        level = bin_.levels[index]
        if not isinstance(bin_node, FdtNode) or not isinstance(level.source, FdtNode):
            return False
        clone = level.source.deep_copy()
        position = bin_node.children.index(level.source) + 1
        bin_node.add_child(clone, position)
        _renumber_levels(bin_node)
        _shift_pointers(bin_node, lambda v: v + 1 if v > index else v)
        return True


_POINTER_RE = re.compile(r"pwrlevel(?!s)")


def _is_pointer_property(name: str) -> bool:
    """bin header 中指向某个等级索引的指针属性。"""
    return bool(_POINTER_RE.search(name))


def _shift_pointers(bin_node: FdtNode, transform: Callable[[int], int]) -> None:
    level_count = sum(1 for c in bin_node.children if _is_level_node(c))
    for prop in bin_node.props:
        if not _is_pointer_property(prop.name):
            continue
        value = prop.as_u64()
        if value is None:
            continue
        new_value = transform(value)
        new_value = max(0, min(new_value, max(0, level_count - 1)))
        if new_value != value:
            prop.set_u32(new_value)


def _renumber_levels(bin_node: FdtNode) -> None:
    """按当前顺序重写 level 节点名与 reg（与手机版行为一致）。"""
    levels = [c for c in bin_node.children if _is_level_node(c)]
    for index, node in enumerate(levels):
        node.name = "qcom,gpu-pwrlevel@%d" % index
        reg = node.get_prop("reg")
        if reg is not None:
            reg.set_u32(index)
        else:
            node.set_prop(FdtProp("reg", b"\x00\x00\x00\x00"))
            node.get_prop("reg").set_u32(index)


# ---------------------------------------------------------------------------
# 数值工具
# ---------------------------------------------------------------------------

def parse_freq_input(text: str) -> Optional[int]:
    """解析用户输入的频率（支持 1160000000 / 1160MHz / 1.16GHz）。"""
    text = text.strip().replace(" ", "")
    if not text:
        return None
    match = re.match(r"^([0-9]*\.?[0-9]+)\s*(ghz|mhz|khz|hz)?$", text, re.IGNORECASE)
    if not match:
        return None
    value = float(match.group(1))
    unit = (match.group(2) or "").lower()
    if unit == "ghz":
        value *= 1_000_000_000
    elif unit == "mhz":
        value *= 1_000_000
    elif unit == "khz":
        value *= 1_000
    elif unit == "" and value < 10000:
        # 无单位时按 MHz 处理（GPU 频率习惯）
        value *= 1_000_000
    return int(round(value))


def format_freq_hz(hz: int) -> str:
    if hz is None or hz <= 0:
        return "—"
    mhz = hz / 1_000_000.0
    if mhz >= 1000:
        return "%.2f GHz" % (mhz / 1000.0)
    return "%.0f MHz" % mhz
