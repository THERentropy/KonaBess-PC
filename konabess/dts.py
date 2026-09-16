"""DTS 文本的受限编译与生成。

功能：

- :func:`compile_dts` —— 把 DTS 文本编译为设备树（:class:`~konabess.fdt.Fdt`）。
- :func:`fdt_to_dts` —— 把设备树生成 DTS 文本（用于导出与查看）。

**受限编译的边界**：支持 dtc 反编译输出（``dtc -I dtb -O dts``）所包含的全部语法
——节点、属性（cells / ``/bits/ N`` / 字符串 / 字节数组）、注释、``/memreserve/``
等，但不支持 label 引用（``&ref``）、label 定义与 ``/include/`` 预处理。这类
语法在导入时会给出明确报错，用户可先用 dtc 编译为 DTB 再导入。
"""

from __future__ import annotations

import re
from typing import Optional

from .fdt import Fdt, FdtNode, FdtProp

_IDENT_CHARS = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789,._+-@#?*")

_ESCAPES = {
    "a": "\a", "b": "\b", "f": "\f", "n": "\n", "r": "\r",
    "t": "\t", "v": "\v", "0": "\0", "\\": "\\", "'": "'", '"': '"',
}


class DtsCompileError(Exception):
    """DTS 文本无法编译（包含不支持的语法或格式错误）。"""


# ---------------------------------------------------------------------------
# 编译：DTS 文本 → 设备树
# ---------------------------------------------------------------------------

class _Compiler:
    def __init__(self, text: str):
        self.text = text
        self.pos = 0
        self.line = 1

    # -- 基础工具 --------------------------------------------------------

    def error(self, message: str) -> "DtsCompileError":
        return DtsCompileError("第 %d 行: %s" % (self.line, message))

    def eof(self) -> bool:
        return self.pos >= len(self.text)

    def peek(self) -> str:
        return self.text[self.pos] if self.pos < len(self.text) else ""

    def advance(self, count: int = 1) -> str:
        chunk = self.text[self.pos:self.pos + count]
        self.line += chunk.count("\n")
        self.pos += count
        return chunk

    def skip_ws(self) -> None:
        while not self.eof():
            ch = self.peek()
            if ch in " \t\r\n\f":
                self.advance()
            elif ch == "/" and self.text.startswith("/*", self.pos):
                end = self.text.find("*/", self.pos + 2)
                if end == -1:
                    raise self.error("注释未闭合")
                self.advance(end + 2 - self.pos)
            elif ch == "/" and self.text.startswith("//", self.pos):
                end = self.text.find("\n", self.pos)
                self.advance((end if end != -1 else len(self.text)) - self.pos)
            else:
                break

    def expect(self, literal: str) -> None:
        self.skip_ws()
        if self.text.startswith(literal, self.pos):
            self.advance(len(literal))
            return
        raise self.error("期望 '%s'，实际为 %r" % (literal, self.text[self.pos:self.pos + 20]))

    def read_ident(self) -> str:
        self.skip_ws()
        start = self.pos
        while not self.eof() and self.peek() in _IDENT_CHARS:
            self.advance()
        if start == self.pos:
            raise self.error("期望标识符，实际为 %r" % self.text[self.pos:self.pos + 20])
        return self.text[start:self.pos]

    def read_int(self) -> int:
        self.skip_ws()
        ch = self.peek()
        if ch == "'":  # 字符字面量 'a'
            if self.pos + 2 >= len(self.text) or self.text[self.pos + 2] != "'":
                raise self.error("无效的字符字面量")
            value = ord(self.text[self.pos + 1])
            self.advance(3)
            return value
        start = self.pos
        if self.text.startswith("0x", self.pos) or self.text.startswith("0X", self.pos):
            self.advance(2)
            while not self.eof() and (self.peek().isdigit() or self.peek().lower() in "abcdef") \
                    and self.peek() not in " \t\r\n,;>]}":
                self.advance()
        else:
            while not self.eof() and (self.peek().isdigit() or self.peek() in "abcdefABCDEF"):
                self.advance()
        token = self.text[start:self.pos]
        if not token:
            raise self.error("期望数值，实际为 %r" % self.text[self.pos:self.pos + 20])
        try:
            return int(token, 0)
        except ValueError:
            raise self.error("无效的数值: %s" % token)

    # -- 顶层 ------------------------------------------------------------

    def compile(self) -> Fdt:
        fdt = Fdt()
        saw_root = False
        while True:
            self.skip_ws()
            if self.eof():
                break
            ch = self.peek()
            if ch == "/":
                if self.text.startswith("/dts-v1/", self.pos):
                    self.advance(len("/dts-v1/"))
                    self.expect(";")
                elif self.text.startswith("/plugin/", self.pos):
                    raise self.error("不支持 /plugin/ 叠加语法")
                elif self.text.startswith("/memreserve/", self.pos):
                    self.advance(len("/memreserve/"))
                    addr = self.read_int()
                    size = self.read_int()
                    self.expect(";")
                    fdt.mem_rsv.append((addr, size))
                elif self.text.startswith("/delete-node/", self.pos) or \
                        self.text.startswith("/delete-property/", self.pos):
                    raise self.error("不支持 /delete-node/ 与 /delete-property/ 语法")
                elif self.text.startswith("/include/", self.pos):
                    raise self.error("不支持 /include/，请先展开后再导入")
                elif self.text.startswith("/*", self.pos) or self.text.startswith("//", self.pos):
                    self.skip_ws()
                else:
                    # 根节点
                    self.advance(1)
                    self.expect("{")
                    self.parse_node_body(fdt.root)
                    self.expect(";")
                    saw_root = True
            elif ch == "&":
                raise self.error("不支持 label 引用（&...）覆盖语法，请使用 dtc 先编译为 DTB")
            elif ch == "#":
                raise self.error("不支持 C 预处理指令（#include 等），请先展开后再导入")
            else:
                ident = self.read_ident()
                self.skip_ws()
                if self.peek() == ":":
                    raise self.error("不支持 label 定义（%s:）" % ident)
                raise self.error("顶层无法解析的内容: %s" % ident)

        if not saw_root:
            raise DtsCompileError("未找到根节点（/ { ... };）")
        return fdt

    # -- 节点 ------------------------------------------------------------

    def parse_node_body(self, node: FdtNode) -> None:
        while True:
            self.skip_ws()
            if self.eof():
                raise self.error("节点未闭合（缺少 }）")
            ch = self.peek()
            if ch == "}":
                self.advance()
                return
            if ch == "/" and (self.text.startswith("/delete-node/", self.pos)
                              or self.text.startswith("/delete-property/", self.pos)):
                raise self.error("不支持 /delete-node/ 与 /delete-property/ 语法")
            if ch == "&":
                raise self.error("不支持 label 引用（&...）")

            name = self.read_ident()
            self.skip_ws()
            if self.peek() == ":":
                raise self.error("不支持 label 定义（%s:）" % name)
            if self.peek() == "{":
                self.advance()
                child = FdtNode(name)
                node.add_child(child)
                self.parse_node_body(child)
                self.expect(";")
            elif self.peek() == ";":
                self.advance()
                node.props.append(FdtProp(name, b""))
            elif self.peek() == "=":
                self.advance()
                data = self.parse_value()
                self.expect(";")
                node.props.append(FdtProp(name, data))
            else:
                raise self.error("属性 %s 后期望 '='、';' 或子节点，实际为 %r"
                                 % (name, self.text[self.pos:self.pos + 20]))

    # -- 值 --------------------------------------------------------------

    def parse_value(self) -> bytes:
        parts: list[bytes] = []
        while True:
            self.skip_ws()
            ch = self.peek()
            if ch == "<":
                parts.append(self.parse_cells(4))
            elif ch == "[":
                parts.append(self.parse_byte_array())
            elif ch == '"':
                parts.append(self.parse_string())
            elif ch == "/":
                if self.text.startswith("/bits/", self.pos):
                    self.advance(len("/bits/"))
                    bits = self.read_int()
                    if bits <= 0 or bits % 8 or bits > 64:
                        raise self.error("无效的 /bits/ 宽度: %d" % bits)
                    parts.append(self.parse_cells(bits // 8))
                else:
                    raise self.error("无法解析的属性值")
            elif ch == "&":
                raise self.error("不支持 label 引用（&...）作为属性值")
            elif ch == "(":
                raise self.error("不支持算符表达式")
            else:
                raise self.error("无法解析的属性值: %r" % self.text[self.pos:self.pos + 20])
            self.skip_ws()
            if self.peek() == ",":
                self.advance()
                continue
            break
        return b"".join(parts)

    def parse_cells(self, width: int) -> bytes:
        self.expect("<")
        out = bytearray()
        mask = (1 << (width * 8)) - 1
        while True:
            self.skip_ws()
            ch = self.peek()
            if ch == ">":
                self.advance()
                return bytes(out)
            if ch == "":
                raise self.error("数组未闭合（缺少 >）")
            if ch == "&":
                raise self.error("不支持 label 引用（&...）作为单元格")
            value = self.read_int()
            out.extend((value & mask).to_bytes(width, "big", signed=False))

    def parse_byte_array(self) -> bytes:
        self.expect("[")
        out = bytearray()
        while True:
            self.skip_ws()
            ch = self.peek()
            if ch == "]":
                self.advance()
                return bytes(out)
            if ch == "":
                raise self.error("字节数组未闭合（缺少 ]）")
            start = self.pos
            while not self.eof() and self.peek() in "0123456789abcdefABCDEF":
                self.advance()
            token = self.text[start:self.pos]
            if not token:
                raise self.error("无效的字节数组内容")
            for i in range(0, len(token), 2):
                out.append(int(token[i:i + 2], 16))

    def parse_string(self) -> bytes:
        self.expect('"')
        out = bytearray()
        while True:
            if self.eof():
                raise self.error("字符串未闭合")
            ch = self.advance()
            if ch == '"':
                break
            if ch == "\\":
                nxt = self.advance()
                if nxt in _ESCAPES:
                    out.extend(_ESCAPES[nxt].encode("utf-8"))
                elif nxt == "x":
                    start = self.pos
                    while not self.eof() and self.peek() in "0123456789abcdefABCDEF":
                        self.advance()
                    token = self.text[start:self.pos]
                    if not token:
                        raise self.error("无效的 \\x 转义")
                    out.append(int(token, 16) & 0xFF)
                elif nxt.isdigit():
                    digits = nxt
                    for _ in range(2):
                        if self.peek().isdigit():
                            digits += self.advance()
                    out.append(int(digits, 8) & 0xFF)
                else:
                    out.extend(nxt.encode("utf-8"))
            else:
                out.extend(ch.encode("utf-8"))
        out.append(0)
        return bytes(out)


def compile_dts(text: str) -> Fdt:
    """把 DTS 文本编译为设备树。

    只支持 dtc 反编译输出级别的语法；遇到 label 引用等高级语法会抛出
    :class:`DtsCompileError`。
    """
    # 去掉 BOM
    if text.startswith("\ufeff"):
        text = text[1:]
    return _Compiler(text).compile()


# ---------------------------------------------------------------------------
# 生成：设备树 → DTS 文本
# ---------------------------------------------------------------------------

def _escape_string(text: str) -> str:
    out = []
    for ch in text:
        if ch == '"':
            out.append('\\"')
        elif ch == "\\":
            out.append("\\\\")
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\t":
            out.append("\\t")
        elif ch == "\r":
            out.append("\\r")
        elif ord(ch) < 32:
            out.append("\\x%02x" % ord(ch))
        else:
            out.append(ch)
    return "".join(out)


def _split_cstrings(data: bytes) -> Optional[list[str]]:
    """若数据是一或多个 NUL 结尾的可读字符串，返回字符串列表。"""
    if len(data) < 2 or not data.endswith(b"\x00"):
        return None
    parts: list[str] = []
    for chunk in data.split(b"\x00")[:-1]:
        if not chunk:
            return None
        if not all(32 <= b < 127 or b in (9, 10, 13) for b in chunk):
            return None
        parts.append(chunk.decode("utf-8", errors="replace"))
    return parts or None


def format_prop_value(prop: FdtProp) -> Optional[str]:
    """把属性数据格式化为 DTS 值文本；空属性返回 None。"""
    data = prop.data
    if not data:
        return None
    strings = _split_cstrings(data)
    if strings is not None:
        return ", ".join('"%s"' % _escape_string(s) for s in strings)
    if len(data) % 4 == 0:
        return "<" + " ".join("0x%x" % v for v in prop.as_u32_list()) + ">"
    return "[" + " ".join("%02x" % b for b in data) + "]"


def fdt_to_dts(fdt: Fdt, indent: str = "\t") -> str:
    """把设备树生成 DTS 文本。"""
    lines: list[str] = ["/dts-v1/;", ""]
    for addr, size in fdt.mem_rsv:
        lines.append("/memreserve/ 0x%x 0x%x;" % (addr, size))
    if fdt.mem_rsv:
        lines.append("")

    def write_node(node: FdtNode, depth: int) -> None:
        prefix = indent * depth
        name = node.name if node.name else "/"
        lines.append("%s%s {" % (prefix, name))
        for prop in node.props:
            value = format_prop_value(prop)
            if value is None:
                lines.append("%s%s%s;" % (prefix, indent, prop.name))
            else:
                lines.append("%s%s%s = %s;" % (prefix, indent, prop.name, value))
        for child in node.children:
            write_node(child, depth + 1)
        lines.append("%s};" % prefix)

    write_node(fdt.root, 0)
    return "\n".join(lines) + "\n"


def looks_like_dts(data: bytes) -> bool:
    """判断文件内容是否像 DTS 文本。"""
    head = data[:4096]
    if head.startswith(b"\xef\xbb\xbf"):
        head = head[3:]
    return b"/dts-v1/" in head or (b"{" in head and b"=" in head and b"\x00" not in head[:256])
