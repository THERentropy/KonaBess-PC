"""内嵌于内核镜像中的 DTB 处理。

部分骁龙平台（如骁龙 888）的 GPU 频率表位于内核镜像尾部内嵌的设备树中。
内核通常以 gzip / lz4 / zstd 压缩，本模块负责：

- 探测内核中内嵌的 DTB 及其压缩格式
- 替换 DTB 后按原格式重新压缩

处理方式与 magiskboot 一致：DTB 是追加在内核数据尾部的 FDT 数据，
第一个 FDT magic 之前的部分视为内核本体。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from . import compression
from .fdt import Fdt, FdtError, split_concatenated_dtbs


class KernelDtbError(Exception):
    pass


@dataclass
class KernelDtbInfo:
    """内核内嵌 DTB 的探测结果。"""

    fmt: str                       # 原始压缩格式（raw 表示未压缩）
    raw_kernel: bytes              # 解压后的内核数据
    dtb_offset: int                # 第一个 FDT 在 raw_kernel 中的偏移
    dtb_size: int                  # 从第一个 FDT 到结尾的字节数
    fdt_spans: list[tuple[int, int]] = field(default_factory=list)
    fdt: Optional[Fdt] = None      # 解析出的第一棵设备树

    @property
    def prefix(self) -> bytes:
        return self.raw_kernel[:self.dtb_offset]

    def dtb_bytes(self) -> bytes:
        return self.raw_kernel[self.dtb_offset:self.dtb_offset + self.dtb_size]

    @property
    def compressed(self) -> bool:
        return self.fmt != compression.FORMAT_RAW

    def format_label(self) -> str:
        return {
            compression.FORMAT_RAW: "未压缩",
            compression.FORMAT_GZIP: "gzip",
            compression.FORMAT_LZ4: "LZ4 (frame)",
            compression.FORMAT_LZ4_LEGACY: "LZ4 (legacy)",
            compression.FORMAT_ZSTD: "Zstandard",
            compression.FORMAT_XZ: "XZ",
            compression.FORMAT_LZMA: "LZMA",
            compression.FORMAT_BZIP2: "bzip2",
        }.get(self.fmt, self.fmt)


def inspect_kernel(kernel_data: bytes) -> Optional[KernelDtbInfo]:
    """探测内核镜像中内嵌的 DTB；没有则返回 None。"""
    if not kernel_data:
        return None

    raw, fmt = compression.maybe_decompress(kernel_data)
    all_spans = split_concatenated_dtbs(raw)
    if not all_spans:
        return None

    # 找到第一个合法的 FDT（内核本体里可能有巧合的 magic）
    dtb_offset = None
    first_fdt: Optional[Fdt] = None
    for offset, size in all_spans:
        try:
            fdt = Fdt.parse(raw[offset:offset + size])
        except FdtError:
            continue
        dtb_offset = offset
        first_fdt = fdt
        break
    if dtb_offset is None or first_fdt is None:
        return None

    # 只保留位于 DTB 区域的片段
    spans = [(offset, size) for offset, size in all_spans if offset >= dtb_offset]
    return KernelDtbInfo(
        fmt=fmt,
        raw_kernel=raw,
        dtb_offset=dtb_offset,
        dtb_size=len(raw) - dtb_offset,
        fdt_spans=spans,
        fdt=first_fdt,
    )


def rebuild_kernel(info: KernelDtbInfo, new_dtb: bytes) -> bytes:
    """用新的 DTB 数据重建内核镜像（保持原有压缩格式）。"""
    raw = info.prefix + new_dtb
    if not info.compressed:
        return raw
    return compression.compress(raw, info.fmt)


def replace_fdts_in_kernel(info: KernelDtbInfo,
                           replacements: dict[int, bytes]) -> bytes:
    """把若干索引的 FDT 替换为新数据，返回新的内核数据。

    :param replacements: ``{FDT 索引: 新 DTB 字节}``
    """
    spans = info.fdt_spans
    if not spans:
        raise KernelDtbError("内核中没有可替换的 DTB")

    pieces: list[bytes] = []
    cursor = 0
    for i, (offset, size) in enumerate(spans):
        pieces.append(info.raw_kernel[cursor:offset])
        pieces.append(replacements.get(i, info.raw_kernel[offset:offset + size]))
        cursor = offset + size
    pieces.append(info.raw_kernel[cursor:])
    return rebuild_kernel(info, b"".join(pieces)[info.dtb_offset:])


def replace_fdt_in_kernel(info: KernelDtbInfo, fdt: Fdt,
                          replaced_index: int = 0) -> bytes:
    """把指定索引的 FDT 替换为修改后的设备树，返回新的内核数据。"""
    if not info.fdt_spans or not (0 <= replaced_index < len(info.fdt_spans)):
        raise KernelDtbError("无效的 DTB 索引")
    return replace_fdts_in_kernel(info, {replaced_index: fdt.to_bytes()})
