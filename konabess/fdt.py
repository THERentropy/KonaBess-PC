"""FDT (Flattened Device Tree) 二进制解析与序列化。

实现对设备树 blob (.dtb) 的完整读写能力：

- 解析 FDT header、memory reservation block、structure block、strings block
- 提供树形节点 API（查找、读取、修改属性）
- 序列化为标准 FDT 二进制

与手机版 (KonaBess Next) 依赖外部 dtc 工具不同，电脑版直接对 DTB
二进制做结构化编辑，因此不需要反编译为文本再重新编译，避免了
dtc 依赖，并保证未修改部分字节级别保持不变。

FDT 二进制布局::

    +----------------------+
    | fdt_header (40 字节) |
    +----------------------+
    | memory reservation   |
    +----------------------+
    | structure block      |  (节点/属性 token 流)
    +----------------------+
    | strings block        |  (属性名字符串池)
    +----------------------+
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import Iterator, Optional

FDT_MAGIC = 0xD00DFEED
FDT_BEGIN_NODE = 1
FDT_END_NODE = 2
FDT_PROP = 3
FDT_NOP = 4
FDT_END = 9

HEADER_SIZE = 40
#: version, last_comp_version, boot_cpuid_phys, size_dt_strings, size_dt_struct
HEADER_FMT = ">10I"


class FdtError(Exception):
    """DTB 数据格式错误。"""


def align4(n: int) -> int:
    return (n + 3) & ~3


def is_fdt_magic(data: bytes | bytearray, offset: int = 0) -> bool:
    """检查 offset 处是否是一段 FDT 的 magic。"""
    return len(data) >= offset + 4 and data[offset:offset + 4] == b"\xd0\x0d\xfe\xed"


# ---------------------------------------------------------------------------
# 属性
# ---------------------------------------------------------------------------

@dataclass
class FdtProp:
    """设备树属性：名字 + 原始字节数据。"""

    name: str
    data: bytes

    # -- 读取 ------------------------------------------------------------

    @property
    def size(self) -> int:
        return len(self.data)

    def is_string(self) -> bool:
        """启发式判断是否为 NUL 结尾的可读字符串。

        注意：4/8 字节宽度（常见 32/64 位整数）优先按数值处理，
        避免把 ``<0x45243200>`` 这类十六进制恰好可打印的属性误判为字符串。
        """
        if len(self.data) < 2 or not self.data.endswith(b"\x00"):
            return False
        body = self.data[:-1]
        if not body or b"\x00" in body or len(body) in (3, 7):
            return False
        return all(32 <= b < 127 or b in (9, 10, 13) for b in body)

    def as_u32(self, cell: int = 0) -> Optional[int]:
        """按大端 32 位无符号整数读取第 cell 个单元。"""
        off = cell * 4
        if len(self.data) < off + 4:
            return None
        return struct.unpack_from(">I", self.data, off)[0]

    def as_u32_list(self) -> list[int]:
        """按大端 32 位读成整数列表。"""
        count = len(self.data) // 4
        if count == 0:
            return []
        return list(struct.unpack_from(">%dI" % count, self.data, 0))

    def as_u64(self) -> Optional[int]:
        """按大端 64 位读取（8 字节）。"""
        if len(self.data) == 4:
            return self.as_u32()
        if len(self.data) < 8:
            return None
        return struct.unpack_from(">Q", self.data, 0)[0]

    def as_str(self) -> str:
        """解码为字符串（去除结尾 NUL）。"""
        raw = self.data
        if raw.endswith(b"\x00"):
            raw = raw[:-1]
        return raw.decode("utf-8", errors="replace")

    def summary(self) -> str:
        """用于界面显示的简短描述。"""
        if self.data == b"":
            return "(空)"
        if self.is_string():
            return '"%s"' % self.as_str()
        if len(self.data) % 4 == 0 and len(self.data) <= 32:
            return "<" + " ".join("0x%x" % v for v in self.as_u32_list()) + ">"
        return "<%d 字节>" % len(self.data)

    # -- 写入 ------------------------------------------------------------

    def set_u32(self, value: int, cell: int = 0) -> None:
        """按原长度写入大端 32 位值（第 cell 个单元）。"""
        off = cell * 4
        if len(self.data) < off + 4:
            raise FdtError("属性 %s 长度不足，无法写入 32 位单元" % self.name)
        data = bytearray(self.data)
        struct.pack_into(">I", data, off, value & 0xFFFFFFFF)
        self.data = bytes(data)

    def set_u64(self, value: int) -> None:
        """按原长度写入大端 64 位值（兼容 4 字节存储）。"""
        if len(self.data) == 4:
            self.set_u32(value)
            return
        if len(self.data) < 8:
            raise FdtError("属性 %s 长度不足，无法写入 64 位值" % self.name)
        data = bytearray(self.data)
        struct.pack_into(">Q", data, 0, value & 0xFFFFFFFFFFFFFFFF)
        self.data = bytes(data)


# ---------------------------------------------------------------------------
# 节点
# ---------------------------------------------------------------------------

@dataclass
class FdtNode:
    """设备树节点（属性按顺序保存，子节点按顺序保存）。"""

    name: str
    props: list[FdtProp] = field(default_factory=list)
    children: list["FdtNode"] = field(default_factory=list)
    parent: Optional["FdtNode"] = field(default=None, repr=False, compare=False)

    # -- 查找 ------------------------------------------------------------

    def get_prop(self, name: str) -> Optional[FdtProp]:
        """返回同名属性中的最后一个（与设备树语义一致）。"""
        for prop in reversed(self.props):
            if prop.name == name:
                return prop
        return None

    def find_child(self, name: str) -> Optional["FdtNode"]:
        for child in self.children:
            if child.name == name:
                return child
        return None

    def child_by_prefix(self, prefix: str) -> Optional["FdtNode"]:
        for child in self.children:
            if child.name.startswith(prefix):
                return child
        return None

    def walk(self) -> Iterator["FdtNode"]:
        """深度优先遍历自身及所有后代。"""
        yield self
        for child in self.children:
            yield from child.walk()

    def path(self) -> str:
        parts: list[str] = []
        node: Optional[FdtNode] = self
        while node is not None:
            parts.append(node.name if node.name else "/")
            node = node.parent
        parts.reverse()
        return "/".join(parts)

    # -- 修改 ------------------------------------------------------------

    def set_prop(self, prop: FdtProp) -> None:
        """替换或追加属性（同名属性会被替换，保持原位置）。"""
        for i, existing in enumerate(self.props):
            if existing.name == prop.name:
                self.props[i] = prop
                return
        self.props.append(prop)

    def set_prop_u32(self, name: str, value: int) -> None:
        """写入 32 位整数属性；不存在时新建 4 字节属性。"""
        prop = self.get_prop(name)
        if prop is None:
            prop = FdtProp(name, b"\x00\x00\x00\x00")
            self.props.append(prop)
        prop.set_u32(value)

    def set_prop_bits(self, name: str, value: int) -> None:
        """写入整数属性，按原有存储长度（4 或 8 字节）自动选择。"""
        prop = self.get_prop(name)
        if prop is None:
            prop = FdtProp(name, b"\x00\x00\x00\x00")
            self.props.append(prop)
        if len(prop.data) >= 8:
            prop.set_u64(value)
        else:
            prop.set_u32(value)

    def remove_prop(self, name: str) -> bool:
        before = len(self.props)
        self.props = [p for p in self.props if p.name != name]
        return len(self.props) != before

    def add_child(self, child: "FdtNode", index: Optional[int] = None) -> None:
        child.parent = self
        if index is None:
            self.children.append(child)
        else:
            self.children.insert(index, child)

    def remove_child(self, child: "FdtNode") -> None:
        if child in self.children:
            self.children.remove(child)

    def deep_copy(self) -> "FdtNode":
        clone = FdtNode(self.name)
        clone.props = [FdtProp(p.name, bytes(p.data)) for p in self.props]
        for child in self.children:
            clone.add_child(child.deep_copy())
        return clone


# ---------------------------------------------------------------------------
# FDT
# ---------------------------------------------------------------------------

@dataclass
class Fdt:
    """一棵完整的设备树。"""

    root: FdtNode = field(default_factory=lambda: FdtNode(""))
    version: int = 17
    last_comp_version: int = 16
    boot_cpuid_phys: int = 0
    mem_rsv: list[tuple[int, int]] = field(default_factory=list)

    # -- 解析 ------------------------------------------------------------

    @classmethod
    def parse(cls, data: bytes | bytearray) -> "Fdt":
        if len(data) < HEADER_SIZE:
            raise FdtError("数据太短，不是有效的 DTB")
        (magic, totalsize, off_struct, off_strings, off_rsvmap,
         version, last_comp, boot_cpuid, size_strings, size_struct) = struct.unpack_from(
            HEADER_FMT, data, 0)

        if magic != FDT_MAGIC:
            raise FdtError("FDT magic 错误: 0x%08X" % magic)
        if totalsize < HEADER_SIZE or totalsize > len(data):
            # 有些 dtb 的 totalsize 字段异常，以实际可用长度为准
            totalsize = len(data)
        if off_struct + size_struct > len(data):
            raise FdtError("structure block 越界")
        if off_strings + size_strings > len(data):
            size_strings = max(0, len(data) - off_strings)

        strings = bytes(data[off_strings:off_strings + size_strings])

        # memory reservation block
        mem_rsv: list[tuple[int, int]] = []
        if off_rsvmap + 16 <= len(data):
            pos = off_rsvmap
            while pos + 16 <= len(data):
                addr, size = struct.unpack_from(">QQ", data, pos)
                pos += 16
                if addr == 0 and size == 0:
                    break
                mem_rsv.append((addr, size))

        # structure block
        fdt = cls(root=FdtNode(""), version=version, last_comp_version=last_comp,
                  boot_cpuid_phys=boot_cpuid, mem_rsv=mem_rsv)
        pos = off_struct
        end = off_struct + size_struct
        stack: list[FdtNode] = []
        current: Optional[FdtNode] = None

        def read_string(offset: int) -> str:
            if offset < 0 or offset >= len(strings):
                return ""
            nul = strings.find(b"\x00", offset)
            if nul == -1:
                nul = len(strings)
            return strings[offset:nul].decode("utf-8", errors="replace")

        while pos + 4 <= end:
            (token,) = struct.unpack_from(">I", data, pos)
            pos += 4

            if token == FDT_BEGIN_NODE:
                nul = data.find(b"\x00", pos, end)
                if nul == -1:
                    raise FdtError("节点名未终止")
                name = bytes(data[pos:nul]).decode("utf-8", errors="replace")
                pos = align4(nul + 1)
                node = FdtNode(name)
                if current is None:
                    fdt.root = node
                else:
                    current.add_child(node)
                stack.append(node)
                current = node

            elif token == FDT_END_NODE:
                if stack:
                    stack.pop()
                current = stack[-1] if stack else None

            elif token == FDT_PROP:
                (length, nameoff) = struct.unpack_from(">II", data, pos)
                pos += 8
                if pos + length > len(data):
                    raise FdtError("属性数据越界")
                payload = bytes(data[pos:pos + length])
                pos = align4(pos + length)
                if current is not None:
                    current.props.append(FdtProp(read_string(nameoff), payload))

            elif token == FDT_NOP:
                continue

            elif token == FDT_END:
                break

            else:
                raise FdtError("未知 token: %d (offset 0x%x)" % (token, pos - 4))

        return fdt

    # -- 序列化 ----------------------------------------------------------

    def to_bytes(self) -> bytes:
        """序列化为标准 FDT 二进制。"""
        # 1) 收集属性名，构建 strings block
        strings = bytearray()
        name_offsets: dict[str, int] = {}

        def intern(name: str) -> int:
            if name in name_offsets:
                return name_offsets[name]
            offset = len(strings)
            name_offsets[name] = offset
            strings.extend(name.encode("utf-8", errors="replace"))
            strings.append(0)
            return offset

        struct_block = bytearray()

        def write_node(node: FdtNode) -> None:
            struct_block.extend(struct.pack(">I", FDT_BEGIN_NODE))
            name_bytes = node.name.encode("utf-8", errors="replace")
            struct_block.extend(name_bytes)
            struct_block.append(0)
            while len(struct_block) % 4:
                struct_block.append(0)
            for prop in node.props:
                struct_block.extend(struct.pack(">I", FDT_PROP))
                struct_block.extend(struct.pack(">II", len(prop.data), intern(prop.name)))
                struct_block.extend(prop.data)
                while len(struct_block) % 4:
                    struct_block.append(0)
            for child in node.children:
                write_node(child)
            struct_block.extend(struct.pack(">I", FDT_END_NODE))

        write_node(self.root)
        struct_block.extend(struct.pack(">I", FDT_END))

        # 2) 布局：header | mem_rsv | struct | strings
        rsv_block = bytearray()
        for addr, size in self.mem_rsv:
            rsv_block.extend(struct.pack(">QQ", addr, size))
        rsv_block.extend(b"\x00" * 16)

        off_rsvmap = HEADER_SIZE
        off_struct = off_rsvmap + len(rsv_block)
        off_strings = off_struct + len(struct_block)
        totalsize = align4(off_strings + len(strings))

        out = bytearray(totalsize)
        struct.pack_into(
            HEADER_FMT, out, 0,
            FDT_MAGIC, totalsize, off_struct, off_strings, off_rsvmap,
            self.version, self.last_comp_version, self.boot_cpuid_phys,
            len(strings), len(struct_block),
        )
        out[off_rsvmap:off_rsvmap + len(rsv_block)] = rsv_block
        out[off_struct:off_struct + len(struct_block)] = struct_block
        out[off_strings:off_strings + len(strings)] = strings
        return bytes(out)

    # -- 便捷方法 --------------------------------------------------------

    def find_node(self, predicate) -> Optional[FdtNode]:
        for node in self.root.walk():
            if predicate(node):
                return node
        return None

    def find_by_compatible(self, needle: str) -> Optional[FdtNode]:
        for node in self.root.walk():
            prop = node.get_prop("compatible")
            if prop is not None and needle in prop.as_str():
                return node
        return None

    def __str__(self) -> str:  # pragma: no cover - 调试用
        def dump(node: FdtNode, indent: int) -> list[str]:
            lines = ["%s%s {" % ("  " * indent, node.name or "/")]
            for prop in node.props:
                lines.append("%s  %s = %s;" % ("  " * indent, prop.name, prop.summary()))
            for child in node.children:
                lines.extend(dump(child, indent + 1))
            lines.append("%s};" % ("  " * indent))
            return lines

        return "\n".join(dump(self.root, 0))


# ---------------------------------------------------------------------------
# 多 DTB 拼接（boot.img 的 dtb 段是若干 DTB 的顺序拼接）
# ---------------------------------------------------------------------------

def split_concatenated_dtbs(data: bytes | bytearray) -> list[tuple[int, int]]:
    """扫描数据中所有 FDT 的位置，返回 [(offset, size), ...]。

    与手机版一致的算法：命中 magic 后从 header 读取 totalsize 跳到下一段。
    """
    result: list[tuple[int, int]] = []
    i = 0
    total = len(data)
    while i + 8 <= total:
        chunk = bytes(data[i:i + 4])
        if chunk == b"\xd0\x0d\xfe\xed":
            (totalsize,) = struct.unpack_from(">I", data, i + 4)
            if 40 <= totalsize <= total - i:
                result.append((i, totalsize))
                i += totalsize
                continue
        i += 1
    return result
