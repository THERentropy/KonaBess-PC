"""内核/ramdisk 压缩格式探测与解压/压缩。

部分机型的 DTB 内嵌在压缩的内核镜像中（例如骁龙 888 平台），
编辑这类 DTB 需要先解压内核、替换内嵌 DTB、再按原格式压缩回去。

支持格式：

- gzip          （标准库，最常见）
- lz4 frame / legacy（需要 ``lz4`` 包）
- zstd          （Python 3.14 标准库 ``compression.zstd``，或 ``zstandard`` 包）
- xz / lzma / bzip2（标准库）
"""

from __future__ import annotations

import bz2
import gzip
import importlib
import io
import lzma
from typing import Optional

# -- 可选依赖探测 -----------------------------------------------------------

_lz4_frame = None
try:  # pragma: no cover - 依赖环境
    from lz4 import frame as _lz4_frame  # type: ignore
except Exception:
    _lz4_frame = None

_zstd = None
try:  # Python 3.14+ 内置
    from compression import zstd as _zstd  # type: ignore
except Exception:
    try:
        _zstd = importlib.import_module("zstandard")
    except Exception:
        _zstd = None


def lz4_available() -> bool:
    return _lz4_frame is not None


def zstd_available() -> bool:
    return _zstd is not None


# -- 格式探测 ---------------------------------------------------------------

FORMAT_GZIP = "gzip"
FORMAT_LZ4 = "lz4"           # frame 格式
FORMAT_LZ4_LEGACY = "lz4_legacy"
FORMAT_ZSTD = "zstd"
FORMAT_XZ = "xz"
FORMAT_LZMA = "lzma"
FORMAT_BZIP2 = "bzip2"
FORMAT_RAW = "raw"

_MAGICS: list[tuple[bytes, str]] = [
    (b"\x1f\x8b", FORMAT_GZIP),
    (b"\x04\x22\x4d\x18", FORMAT_LZ4),
    (b"\x02\x21\x4c\x18", FORMAT_LZ4_LEGACY),
    (b"\x28\xb5\x2f\xfd", FORMAT_ZSTD),
    (b"\xfd\x37\x7a\x58\x5a\x00", FORMAT_XZ),
    (b"\x42\x5a\x68", FORMAT_BZIP2),
    (b"\x5d\x00\x00", FORMAT_LZMA),
]


def detect_format(data: bytes) -> str:
    """根据文件头探测压缩格式，未识别时返回 raw。"""
    for magic, name in _MAGICS:
        if data.startswith(magic):
            return name
    return FORMAT_RAW


# -- 解压 -------------------------------------------------------------------

def decompress(data: bytes, fmt: Optional[str] = None) -> bytes:
    """解压数据；fmt 为 None 时自动探测。"""
    if fmt is None or fmt == FORMAT_RAW:
        fmt = detect_format(data)

    if fmt == FORMAT_GZIP:
        return gzip.decompress(data)
    if fmt == FORMAT_XZ:
        return lzma.decompress(data, format=lzma.FORMAT_XZ)
    if fmt == FORMAT_LZMA:
        return lzma.decompress(data, format=lzma.FORMAT_ALONE)
    if fmt == FORMAT_BZIP2:
        return bz2.decompress(data)
    if fmt == FORMAT_LZ4:
        if _lz4_frame is None:
            raise RuntimeError("需要 lz4 模块才能解压 LZ4 内核，请先执行: pip install lz4")
        return _lz4_frame.decompress(data)
    if fmt == FORMAT_LZ4_LEGACY:
        return _decompress_lz4_legacy(data)
    if fmt == FORMAT_ZSTD:
        return _decompress_zstd(data)
    raise RuntimeError("不支持的压缩格式: %s" % fmt)


def _decompress_lz4_legacy(data: bytes) -> bytes:
    """lz4 legacy 格式: magic(4) + 原始长度(4, LE) + lz4 block 流。"""
    if not lz4_available():
        raise RuntimeError("需要 lz4 模块才能解压 LZ4 内核，请先执行: pip install lz4")
    import struct
    from lz4 import block as lz4_block  # type: ignore

    if len(data) < 8:
        raise RuntimeError("lz4 legacy 数据太短")
    (raw_size,) = struct.unpack_from("<I", data, 4)
    payload = data[8:]
    try:
        return lz4_block.decompress(payload, uncompressed_size=raw_size)
    except Exception:
        # 某些实现没有长度前缀，回退为 block 自动解压
        return lz4_block.decompress(data[4:])


def _decompress_zstd(data: bytes) -> bytes:
    if _zstd is None:
        raise RuntimeError("需要 zstandard 模块才能解压 Zstandard 内核，请先执行: pip install zstandard")
    if hasattr(_zstd, "decompress"):  # Python 3.14 compression.zstd
        return _zstd.decompress(data)
    return _zstd.ZstdDecompressor().decompress(data, max_output_size=512 * 1024 * 1024)


# -- 压缩 -------------------------------------------------------------------

def compress(data: bytes, fmt: str) -> bytes:
    """按指定格式压缩数据（用于回写内核镜像）。"""
    if fmt in (FORMAT_RAW, ""):
        return data
    if fmt == FORMAT_GZIP:
        # mtime=0 保证可复现输出
        buf = io.BytesIO()
        with gzip.GzipFile(fileobj=buf, mode="wb", compresslevel=9, mtime=0) as gz:
            gz.write(data)
        return buf.getvalue()
    if fmt == FORMAT_XZ:
        return lzma.compress(data, format=lzma.FORMAT_XZ)
    if fmt == FORMAT_LZMA:
        return lzma.compress(data, format=lzma.FORMAT_ALONE)
    if fmt == FORMAT_BZIP2:
        return bz2.compress(data)
    if fmt == FORMAT_LZ4:
        if _lz4_frame is None:
            raise RuntimeError("需要 lz4 模块才能压缩为 LZ4 格式，请先执行: pip install lz4")
        return _lz4_frame.compress(data)
    if fmt == FORMAT_LZ4_LEGACY:
        if not lz4_available():
            raise RuntimeError("需要 lz4 模块才能压缩为 LZ4 格式，请先执行: pip install lz4")
        import struct
        from lz4 import block as lz4_block  # type: ignore

        payload = lz4_block.compress(data, mode="high_compression", store_size=False)
        return b"\x02\x21\x4c\x18" + struct.pack("<I", len(data)) + payload
    if fmt == FORMAT_ZSTD:
        if _zstd is None:
            raise RuntimeError("需要 zstandard 模块才能压缩为 Zstandard 格式，请先执行: pip install zstandard")
        if hasattr(_zstd, "compress"):  # Python 3.14 compression.zstd
            return _zstd.compress(data)
        return _zstd.ZstdCompressor().compress(data)
    raise RuntimeError("不支持的压缩格式: %s" % fmt)


def maybe_decompress(data: bytes) -> tuple[bytes, str]:
    """自动探测并解压；若非压缩数据则原样返回。"""
    fmt = detect_format(data)
    if fmt == FORMAT_RAW:
        return data, FORMAT_RAW
    try:
        return decompress(data, fmt), fmt
    except Exception:
        return data, FORMAT_RAW


def format_support_status() -> str:
    """返回可选压缩库的可用情况（用于界面提示）。"""
    parts = ["gzip ✓", "xz/lzma ✓", "bzip2 ✓"]
    parts.append("lz4 ✓" if lz4_available() else "lz4 ✗")
    parts.append("zstd ✓" if zstd_available() else "zstd ✗")
    return "  ".join(parts)
