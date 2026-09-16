"""Android 启动镜像解析与重组。

支持以下格式的读取与写回（不需要 magiskboot 等外部工具）：

- Android boot image v0 / v1 / v2 / v3 / v4 (boot.img)
- Android vendor boot image v3 / v4   (vendor_boot.img)
- Android DTBO image (dtbo.img，含表格式与纯拼接格式)
- 独立 DTB / 多个 DTB 拼接文件

对 boot / vendor_boot 镜像，解析时定位其中的 DTB 段，写回时只替换
对应段并重排布局、回填 header 字段，其余段（压缩的 kernel、ramdisk）
字节级原样保留，因此不涉及解压/重压缩问题。

若 DTB 内嵌在压缩内核中（如骁龙 888 平台），由 :mod:`konabess.kernel_dtb`
在处理层解压后替换，再按原格式压缩回去。
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from .fdt import is_fdt_magic, split_concatenated_dtbs

# -- 常量 -------------------------------------------------------------------

BOOT_MAGIC = b"ANDROID!"
VENDOR_BOOT_MAGIC = b"VNDRBOOT"
BOOT_SIGNATURE_MAGIC = b"BNDRBOOT"
DTBO_MAGIC = 0xD7B7AB1E

BOOT_HEADER_SIZES = {0: 1632, 1: 1648, 2: 1660, 3: 1580, 4: 1580}
VENDOR_BOOT_HEADER_SIZES = {3: 2112, 4: 2128}

DTBO_HEADER_SIZE = 32
DTBO_ENTRY_SIZE = 32

VENDOR_RAMDISK_TYPE_NONE = 0
VENDOR_RAMDISK_TYPE_PLATFORM = 1
VENDOR_RAMDISK_TYPE_RECOVERY = 2
VENDOR_RAMDISK_TYPE_DLKM = 3

VENDOR_RAMDISK_NAME_SIZE = 32
VENDOR_RAMDISK_TABLE_ENTRY_SIZE_V4 = 108


class ImageError(Exception):
    """镜像结构错误。"""


def align(n: int, alignment: int) -> int:
    if alignment <= 1:
        return n
    return (n + alignment - 1) // alignment * alignment


def _u32le(data: bytes, offset: int) -> int:
    return struct.unpack_from("<I", data, offset)[0]


def _put_u32le(buf: bytearray, offset: int, value: int) -> None:
    struct.pack_into("<I", buf, offset, value & 0xFFFFFFFF)


def _put_u64le(buf: bytearray, offset: int, value: int) -> None:
    struct.pack_into("<Q", buf, offset, value & 0xFFFFFFFFFFFFFFFF)


# -- 数据模型 ---------------------------------------------------------------

class ImageKind(str, Enum):
    BOOT = "boot"
    VENDOR_BOOT = "vendor_boot"
    DTBO = "dtbo"
    DTB = "dtb"


@dataclass
class Segment:
    """镜像中的一个区段。"""

    name: str
    data: bytes = b""
    offset: int = 0
    meta: dict = field(default_factory=dict)

    @property
    def size(self) -> int:
        return len(self.data)


@dataclass
class PackedImage:
    """镜像的统一内存表示。"""

    kind: ImageKind
    segments: list[Segment] = field(default_factory=list)
    page_size: int = 4096
    header_version: int = 0
    raw_header: bytes = b""
    meta: dict = field(default_factory=dict)

    # -- 查询 ------------------------------------------------------------

    def segment(self, name: str) -> Optional[Segment]:
        for seg in self.segments:
            if seg.name == name:
                return seg
        return None

    def dtb_segments(self) -> list[Segment]:
        """所有可能包含设备树的段（dtb 段 / ramdisk 段也可能含 DTB）。"""
        return [s for s in self.segments if s.name in ("dtb", "kernel_dtb")]

    # -- 打包 ------------------------------------------------------------

    def pack(self) -> bytes:
        if self.kind == ImageKind.BOOT:
            return _pack_boot(self)
        if self.kind == ImageKind.VENDOR_BOOT:
            return _pack_vendor_boot(self)
        if self.kind == ImageKind.DTBO:
            return _pack_dtbo(self)
        return _pack_raw_dtb(self)


# -- 探测 -------------------------------------------------------------------

def sniff_kind(data: bytes) -> Optional[ImageKind]:
    if data.startswith(BOOT_MAGIC):
        return ImageKind.BOOT
    if data.startswith(VENDOR_BOOT_MAGIC):
        return ImageKind.VENDOR_BOOT
    if len(data) >= 4 and struct.unpack_from(">I", data, 0)[0] == DTBO_MAGIC:
        return ImageKind.DTBO
    if is_fdt_magic(data):
        return ImageKind.DTB
    return None


# ---------------------------------------------------------------------------
# boot image (v0 - v4)
# ---------------------------------------------------------------------------

def _parse_boot(data: bytes) -> PackedImage:
    if len(data) < 1632:
        raise ImageError("boot 镜像过小")
    version = _u32le(data, 40)
    if version not in (0, 1, 2, 3, 4):
        version = 0

    if version in (3, 4):
        return _parse_boot_v3v4(data, version)
    return _parse_boot_legacy(data, version)


def _parse_boot_legacy(data: bytes, version: int) -> PackedImage:
    page = _u32le(data, 36)
    if page < 512 or page > 65536 or page % 512 != 0:
        page = 4096

    kernel_size = _u32le(data, 8)
    ramdisk_size = _u32le(data, 16)
    second_size = _u32le(data, 24)

    img = PackedImage(kind=ImageKind.BOOT, page_size=page, header_version=version,
                      raw_header=bytes(data[:page]))

    pos = page
    sections: list[tuple[str, int, bool]] = [
        ("kernel", kernel_size, True),
        ("ramdisk", ramdisk_size, True),
        ("second", second_size, True),
    ]

    if version >= 1:
        rec_size = _u32le(data, 1632)
        rec_offset = struct.unpack_from("<Q", data, 1636)[0] if len(data) >= 1644 else 0
        sections.append(("recovery_dtbo", rec_size, True))
        if rec_size and 0 < rec_offset < len(data) and rec_offset + rec_size <= len(data):
            pos = rec_offset  # 使用 header 中记录的实际偏移

    if version >= 2:
        dtb_size = _u32le(data, 1648)
        dtb_addr = struct.unpack_from("<Q", data, 1652)[0] if len(data) >= 1660 else 0
        sections.append(("dtb", dtb_size, True))
        img.meta["dtb_addr"] = dtb_addr
    img.meta["kernel_addr"] = _u32le(data, 12)
    img.meta["ramdisk_addr"] = _u32le(data, 20)
    img.meta["second_addr"] = _u32le(data, 28)
    img.meta["tags_addr"] = _u32le(data, 32)
    img.meta["os_version"] = _u32le(data, 44)

    for name, size, _aligned in sections:
        seg = Segment(name=name, offset=pos, data=bytes(data[pos:pos + size]))
        if size and len(seg.data) != size:
            seg.data = seg.data + b"\x00" * (size - len(seg.data))
        img.segments.append(seg)
        pos = align(pos + size, page)

    # 某些 v2 镜像 header 中的 dtb_size 为 0，但文件尾部实际带 DTB，做兜底探测
    if version >= 2 and not img.segment("dtb").data:
        found = _scan_for_dtb(data, page)
        if found is not None:
            offset, size = found
            img.segments = [s for s in img.segments if s.name != "dtb"]
            img.segments.append(Segment(name="dtb", offset=offset, data=bytes(data[offset:offset + size])))
    return img


def _parse_boot_v3v4(data: bytes, version: int) -> PackedImage:
    header_size = _u32le(data, 20)
    if header_size < 1580 or header_size > 65536:
        header_size = 4096
    page = 4096

    kernel_size = _u32le(data, 8)
    ramdisk_size = _u32le(data, 12)

    img = PackedImage(kind=ImageKind.BOOT, page_size=page, header_version=version,
                      raw_header=bytes(data[:min(len(data), 4096)]))
    img.meta["os_version"] = _u32le(data, 16)

    kernel_off = align(header_size, page)
    ramdisk_off = align(kernel_off + kernel_size, page)

    img.segments.append(Segment("kernel", bytes(data[kernel_off:kernel_off + kernel_size]), kernel_off))
    img.segments.append(Segment("ramdisk", bytes(data[ramdisk_off:ramdisk_off + ramdisk_size]), ramdisk_off))

    # v4 之后可能有 boot signature（16KB），保留
    sig_off = align(ramdisk_off + ramdisk_size, page)
    if sig_off + 16 <= len(data) and data[sig_off:sig_off + 8] == BOOT_SIGNATURE_MAGIC:
        sig_size = _u32le(data, sig_off + 8)
        if 0 < sig_size <= len(data) - sig_off:
            img.segments.append(Segment("boot_signature", bytes(data[sig_off:sig_off + sig_size]), sig_off))
    return img


def _pack_boot(img: PackedImage) -> bytes:
    if img.header_version in (3, 4):
        return _pack_boot_v3v4(img)
    return _pack_boot_legacy(img)


def _pack_boot_legacy(img: PackedImage) -> bytes:
    page = img.page_size
    header = bytearray(img.raw_header)
    if len(header) < page:
        header.extend(b"\x00" * (page - len(header)))

    out = bytearray()
    out.extend(header)

    def place(name: str, size_field: Optional[int], addr_field: Optional[tuple[int, str, bool]] = None) -> None:
        seg = img.segment(name)
        if seg is None or not seg.data:
            if size_field is not None:
                _put_u32le(out, size_field, 0)
            return
        _pad_to_page(out, page)
        offset = len(out)
        if size_field is not None:
            _put_u32le(out, size_field, len(seg.data))
        if addr_field is not None:
            field_offset, meta_key, is_u64 = addr_field
            if meta_key in img.meta:
                if is_u64:
                    _put_u64le(out, field_offset, img.meta[meta_key])
                else:
                    _put_u32le(out, field_offset, img.meta[meta_key])
        out.extend(seg.data)
        seg.offset = offset

    place("kernel", 8, (12, "kernel_addr", False))
    place("ramdisk", 16, (20, "ramdisk_addr", False))
    place("second", 24, (28, "second_addr", False))

    if img.header_version >= 1:
        seg = img.segment("recovery_dtbo")
        if seg is not None and seg.data:
            _pad_to_page(out, page)
            _put_u32le(out, 1632, len(seg.data))
            _put_u64le(out, 1636, len(out))
            seg.offset = len(out)
            out.extend(seg.data)
        else:
            _put_u32le(out, 1632, 0)

    if img.header_version >= 2:
        place("dtb", 1648, (1652, "dtb_addr", True))

    _pad_to_page(out, page)
    return bytes(out)


def _pack_boot_v3v4(img: PackedImage) -> bytes:
    page = 4096
    header = bytearray(img.raw_header)
    if len(header) < page:
        header.extend(b"\x00" * (page - len(header)))

    kernel = img.segment("kernel")
    ramdisk = img.segment("ramdisk")
    _put_u32le(header, 8, kernel.size if kernel else 0)
    _put_u32le(header, 12, ramdisk.size if ramdisk else 0)

    out = bytearray(header)
    _pad_to_page(out, page)
    if kernel is not None:
        kernel.offset = len(out)
        out.extend(kernel.data)
        _pad_to_page(out, page)
    if ramdisk is not None:
        ramdisk.offset = len(out)
        out.extend(ramdisk.data)
        _pad_to_page(out, page)
    sig = img.segment("boot_signature")
    if sig is not None and sig.data:
        sig.offset = len(out)
        out.extend(sig.data)
        _pad_to_page(out, page)
    return bytes(out)


# ---------------------------------------------------------------------------
# vendor_boot image (v3 / v4)
# ---------------------------------------------------------------------------

def _parse_vendor_boot(data: bytes) -> PackedImage:
    if len(data) < 2112:
        raise ImageError("vendor_boot 镜像过小")
    version = _u32le(data, 8)
    if version not in (3, 4):
        raise ImageError("不支持的 vendor_boot 版本: %d" % version)

    page = _u32le(data, 12)
    if page < 512 or page > 65536 or page % 512 != 0:
        page = 4096
    header_size = _u32le(data, 2096)
    if header_size < VENDOR_BOOT_HEADER_SIZES.get(version, 2112) or header_size > 65536:
        header_size = VENDOR_BOOT_HEADER_SIZES.get(version, 2112)

    vendor_ramdisk_size = _u32le(data, 24)
    dtb_size = _u32le(data, 2100)
    dtb_addr = struct.unpack_from("<Q", data, 2104)[0]

    img = PackedImage(kind=ImageKind.VENDOR_BOOT, page_size=page, header_version=version,
                      raw_header=bytes(data[:min(len(data), align(header_size, page))]))
    img.meta["dtb_addr"] = dtb_addr
    img.meta["kernel_addr"] = _u32le(data, 16)
    img.meta["ramdisk_addr"] = _u32le(data, 20)
    img.meta["tags_addr"] = _u32le(data, 2076)

    ramdisk_off = align(header_size, page)
    img.segments.append(Segment("vendor_ramdisk", bytes(data[ramdisk_off:ramdisk_off + vendor_ramdisk_size]), ramdisk_off))

    table_seg = None
    if version == 4:
        table_size = _u32le(data, 2112)
        entry_num = _u32le(data, 2116)
        entry_size = _u32le(data, 2120)
        bootconfig_size = _u32le(data, 2124)
        img.meta["bootconfig_size"] = bootconfig_size
        if entry_size == 0:
            entry_size = VENDOR_RAMDISK_TABLE_ENTRY_SIZE_V4
        table_off = _locate_vendor_ramdisk_table(data, ramdisk_off + vendor_ramdisk_size, entry_num, entry_size)
        table_data = bytes(data[table_off:table_off + table_size])
        table_seg = Segment("vendor_ramdisk_table", table_data, table_off)
        img.segments.append(table_seg)
        img.meta["table_entry_size"] = entry_size

        # ramdisk fragments（每个 fragment 的 offset 记录在表项中）
        last_end = table_off + table_size
        for i in range(min(entry_num, table_size // entry_size if entry_size else 0)):
            base = i * entry_size
            r_size, r_offset, r_type = struct.unpack_from("<III", table_data, base)
            name_raw = table_data[base + 12:base + 44]
            board_raw = table_data[base + 44:base + 108]
            seg = Segment("ramdisk_fragment_%d" % i,
                          bytes(data[r_offset:r_offset + r_size]), r_offset,
                          meta={"type": r_type, "name_raw": name_raw, "board_raw": board_raw})
            img.segments.append(seg)
            last_end = max(last_end, r_offset + r_size)

        dtb_off = _locate_dtb_after(data, last_end, dtb_size, page)

        if dtb_size and bootconfig_size:
            bc_off = _locate_bootconfig(data, dtb_off + dtb_size, bootconfig_size, page)
            if 0 < bc_off <= len(data) - bootconfig_size:
                img.segments.append(Segment("bootconfig", bytes(data[bc_off:bc_off + bootconfig_size]), bc_off))
    else:
        dtb_off = _locate_dtb_after(data, ramdisk_off + vendor_ramdisk_size, dtb_size, page)

    if dtb_size:
        img.segments.append(Segment("dtb", bytes(data[dtb_off:dtb_off + dtb_size]), dtb_off))

    return img


def _locate_vendor_ramdisk_table(data: bytes, expected: int, entry_num: int, entry_size: int) -> int:
    """定位 vendor_ramdisk 表：优先使用公式位置，否则在附近校验字段合理性。"""
    candidates = [expected]
    candidates.append(align(expected, 4096))
    for off in candidates:
        if off + 12 > len(data):
            continue
        size, num, esize = struct.unpack_from("<III", data, off)
        if 0 < num <= 4096 and 0 < esize <= 4096 and size == num * esize:
            return off
    return expected


def _locate_dtb_after(data: bytes, expected: int, dtb_size: int, page: int) -> int:
    """在 expected 附近定位 DTB 段（校验 FDT magic 与长度）。"""
    if not dtb_size:
        return expected
    candidates = [expected, align(expected, page)]
    for off in candidates:
        if off + 40 <= len(data) and is_fdt_magic(data, off):
            totalsize = struct.unpack_from(">I", data, off + 4)[0]
            if 40 <= totalsize <= dtb_size:
                return off
    # 回退：在 [expected - page, expected + page] 范围内搜索 FDT magic
    for off in range(max(0, expected - page), min(len(data) - 40, expected + page)):
        if is_fdt_magic(data, off):
            return off
    return expected


def _locate_bootconfig(data: bytes, after: int, bootconfig_size: int, page: int) -> int:
    """定位 bootconfig：位于 DTB 之后、按页对齐的最后一段。"""
    if not bootconfig_size:
        return after
    candidates = [align(after, page), after, len(data) - bootconfig_size]
    for candidate in candidates:
        if after <= candidate <= len(data) - bootconfig_size:
            return candidate
    return max(0, len(data) - bootconfig_size)


def _pack_vendor_boot(img: PackedImage) -> bytes:
    page = img.page_size
    header_size = VENDOR_BOOT_HEADER_SIZES.get(img.header_version, 2112)
    header = bytearray(img.raw_header)
    if len(header) < align(header_size, page):
        header.extend(b"\x00" * (align(header_size, page) - len(header)))

    out = bytearray(header)
    _pad_to_page(out, page)

    ramdisk = img.segment("vendor_ramdisk")
    ramdisk_off = len(out)
    ramdisk_size = ramdisk.size if ramdisk else 0
    if ramdisk is not None:
        out.extend(ramdisk.data)
        ramdisk.offset = ramdisk_off

    _put_u32le(out, 24, ramdisk_size)

    dtb = img.segment("dtb")
    dtb_size = dtb.size if dtb else 0

    if img.header_version == 4:
        entry_size = img.meta.get("table_entry_size", VENDOR_RAMDISK_TABLE_ENTRY_SIZE_V4)
        fragments = [s for s in img.segments if s.name.startswith("ramdisk_fragment_")]
        table = bytearray()
        for seg in fragments:
            table.extend(struct.pack("<III", seg.size, 0, seg.meta.get("type", VENDOR_RAMDISK_TYPE_NONE)))
            table.extend(seg.meta.get("name_raw", b"\x00" * VENDOR_RAMDISK_NAME_SIZE).ljust(VENDOR_RAMDISK_NAME_SIZE, b"\x00")[:VENDOR_RAMDISK_NAME_SIZE])
            table.extend(seg.meta.get("board_raw", b"\x00" * 64).ljust(64, b"\x00")[:64])
            if len(table) % entry_size:
                table.extend(b"\x00" * (entry_size - len(table) % entry_size))
        table_off = len(out)
        out.extend(table)
        # 回填 fragment offset
        for i, seg in enumerate(fragments):
            _pad_to_page(out, page)
            struct.pack_into("<I", out, table_off + i * entry_size + 4, len(out))
            seg.offset = len(out)
            out.extend(seg.data)

        if dtb is not None and dtb.data:
            _pad_to_page(out, page)
            dtb.offset = len(out)
            out.extend(dtb.data)
        _pad_to_page(out, page)

        bootconfig = img.segment("bootconfig")
        if bootconfig is not None and bootconfig.data:
            bootconfig.offset = len(out)
            out.extend(bootconfig.data)
            _put_u32le(out, 2124, bootconfig.size)
        else:
            _put_u32le(out, 2124, 0)

        _put_u32le(out, 2112, len(table))
        _put_u32le(out, 2116, len(fragments))
        _put_u32le(out, 2120, entry_size)
    else:
        if dtb is not None and dtb.data:
            _pad_to_page(out, page)
            dtb.offset = len(out)
            out.extend(dtb.data)
        _pad_to_page(out, page)

    _put_u32le(out, 2100, dtb_size)
    _put_u64le(out, 2104, img.meta.get("dtb_addr", 0))
    _pad_to_page(out, page)
    return bytes(out)


# ---------------------------------------------------------------------------
# dtbo image
# ---------------------------------------------------------------------------

def _parse_dtbo(data: bytes) -> PackedImage:
    magic, total_size, header_size, entry_size, entry_count, entries_offset, page, _ver = \
        struct.unpack_from(">8I", data, 0)
    if magic != DTBO_MAGIC:
        raise ImageError("DTBO magic 错误")
    if entry_size == 0:
        entry_size = DTBO_ENTRY_SIZE

    if header_size < DTBO_HEADER_SIZE or header_size > len(data):
        header_size = DTBO_HEADER_SIZE
    img = PackedImage(kind=ImageKind.DTBO, page_size=page or 4096, header_version=0,
                      raw_header=bytes(data[:header_size]))
    img.meta["uses_table"] = True
    img.meta["header_size"] = header_size
    img.meta["entry_size"] = entry_size
    img.meta["entries_offset"] = entries_offset

    for i in range(entry_count):
        base = entries_offset + i * entry_size
        if base + DTBO_ENTRY_SIZE > len(data):
            break
        dt_size, dt_offset, dt_id, dt_rev = struct.unpack_from(">4I", data, base)
        custom = list(struct.unpack_from(">4I", data, base + 16))
        if dt_size == 0 or dt_offset + dt_size > len(data):
            continue
        img.segments.append(Segment(
            "dtb_%d" % i, bytes(data[dt_offset:dt_offset + dt_size]), dt_offset,
            meta={"id": dt_id, "rev": dt_rev, "custom": custom}))
    return img


def _parse_raw_dtb(data: bytes) -> PackedImage:
    img = PackedImage(kind=ImageKind.DTB)
    img.meta["uses_table"] = False
    spans = split_concatenated_dtbs(data)
    if not spans:
        raise ImageError("未找到 DTB 数据")
    for i, (offset, size) in enumerate(spans):
        img.segments.append(Segment("dtb_%d" % i, bytes(data[offset:offset + size]), offset))
    return img


def _pack_dtbo(img: PackedImage) -> bytes:
    page = img.page_size or 4096
    header_size = img.meta.get("header_size", DTBO_HEADER_SIZE)
    entry_size = img.meta.get("entry_size", DTBO_ENTRY_SIZE)
    entries_offset = img.meta.get("entries_offset", DTBO_HEADER_SIZE)

    header_block = max(header_size, entries_offset, DTBO_HEADER_SIZE)
    entry_block = entry_size * len(img.segments)
    first_chunk = align(header_block + entry_block, page)

    chunks = bytearray()
    entries = bytearray()
    cursor = first_chunk
    for seg in img.segments:
        entries.extend(struct.pack(">4I", seg.size, cursor, seg.meta.get("id", 0), seg.meta.get("rev", 0)))
        entries.extend(struct.pack(">4I", *seg.meta.get("custom", [0, 0, 0, 0])[:4]))
        if len(entries) % entry_size:
            entries.extend(b"\x00" * (entry_size - len(entries) % entry_size))
        padded = align(seg.size, page)
        chunks.extend(seg.data)
        chunks.extend(b"\x00" * (padded - seg.size))
        cursor += padded

    total = first_chunk + len(chunks)
    out = bytearray(total)
    struct.pack_into(">8I", out, 0, DTBO_MAGIC, total, header_size, entry_size,
                     len(img.segments), entries_offset, page, 0)
    out[entries_offset:entries_offset + len(entries)] = entries
    out[first_chunk:first_chunk + len(chunks)] = chunks
    return bytes(out)


def _pack_raw_dtb(img: PackedImage) -> bytes:
    return b"".join(seg.data for seg in img.segments)


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------

def _pad_to_page(out: bytearray, page: int) -> None:
    while len(out) % page:
        out.append(0)


def _scan_for_dtb(data: bytes, min_offset: int) -> Optional[tuple[int, int]]:
    """在镜像尾部扫描一段可用的 DTB（返回 offset/size）。"""
    for off in range(min_offset, max(0, len(data) - 40)):
        if is_fdt_magic(data, off):
            totalsize = struct.unpack_from(">I", data, off + 4)[0]
            if 40 <= totalsize <= len(data) - off:
                return off, totalsize
    return None


def load_image(data: bytes, kind: Optional[ImageKind] = None) -> PackedImage:
    """解析镜像数据为 :class:`PackedImage`。"""
    kind = kind or sniff_kind(data)
    if kind is None:
        raise ImageError("无法识别的文件格式（不是 boot/vendor_boot/dtbo/dtb）")
    if kind == ImageKind.BOOT:
        return _parse_boot(data)
    if kind == ImageKind.VENDOR_BOOT:
        return _parse_vendor_boot(data)
    if kind == ImageKind.DTBO:
        return _parse_dtbo(data)
    return _parse_raw_dtb(data)


def kind_display_name(kind: ImageKind) -> str:
    return {
        ImageKind.BOOT: "boot 镜像",
        ImageKind.VENDOR_BOOT: "vendor_boot 镜像",
        ImageKind.DTBO: "dtbo 镜像",
        ImageKind.DTB: "DTB 文件",
    }.get(kind, str(kind))
