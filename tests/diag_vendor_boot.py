"""诊断 vendor_boot 原文件与导出文件的差异。

对比 header 字段、段清单、数据覆盖范围，定位丢失的数据。
"""

from __future__ import annotations

import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from konabess.bootimg import load_image  # noqa: E402
from konabess.fdt import is_fdt_magic  # noqa: E402

ORIG = Path(r"C:\Users\WESTON\Desktop\ADB\1\vendor_boot_o.img")
NEW = Path(r"C:\Users\WESTON\Desktop\ADB\1\vendor_boot_new.img")


def dump_header(data: bytes, title: str) -> dict:
    print("\n== %s (%.2f MB, %d 字节) ==" % (title, len(data) / 1048576, len(data)))
    if not data.startswith(b"VNDRBOOT"):
        print("  不是 vendor_boot! magic =", data[:8])
        return {}
    version, page = struct.unpack_from("<II", data, 8)
    vendor_ramdisk_size = struct.unpack_from("<I", data, 24)[0]
    header_size = struct.unpack_from("<I", data, 2096)[0]
    dtb_size = struct.unpack_from("<I", data, 2100)[0]
    dtb_addr = struct.unpack_from("<Q", data, 2104)[0]
    fields = {
        "version": version,
        "page_size": page,
        "vendor_ramdisk_size": vendor_ramdisk_size,
        "header_size": header_size,
        "dtb_size": dtb_size,
        "dtb_addr": dtb_addr,
    }
    if version == 4:
        fields.update({
            "table_size": struct.unpack_from("<I", data, 2112)[0],
            "entry_num": struct.unpack_from("<I", data, 2116)[0],
            "entry_size": struct.unpack_from("<I", data, 2120)[0],
            "bootconfig_size": struct.unpack_from("<I", data, 2124)[0],
        })
    for key, value in fields.items():
        print("  %-20s = %d (0x%x)" % (key, value, value))
    return fields


def dump_segments(path: Path, title: str) -> dict[str, bytes]:
    data = path.read_bytes()
    print("\n== %s 段清单 ==" % title)
    try:
        image = load_image(data)
    except Exception as exc:  # noqa: BLE001
        print("  解析失败:", exc)
        return {}
    covered = 0
    for seg in image.segments:
        end = seg.offset + seg.size
        covered += seg.size
        print("  %-24s offset=0x%-10x size=%12d  (结束 0x%x)"
              % (seg.name, seg.offset, seg.size, end))
    print("  段数量: %d, 段数据总计: %.2f MB" % (len(image.segments), covered / 1048576))

    # 覆盖分析: 找出未被任何段覆盖的区域
    intervals = sorted((seg.offset, seg.offset + seg.size) for seg in image.segments)
    merged: list[list[int]] = []
    for start, end in intervals:
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    print("  -- 未覆盖区域 --")
    cursor = 0
    for start, end in merged:
        if start > cursor:
            print("     [0x%x .. 0x%x)  %12d 字节 (%.2f MB)"
                  % (cursor, start, start - cursor, (start - cursor) / 1048576))
        cursor = max(cursor, end)
    if cursor < len(data):
        print("     [0x%x .. 文件尾)  %12d 字节 (%.2f MB)"
              % (cursor, len(data) - cursor, (len(data) - cursor) / 1048576))

    return {seg.name: seg.data for seg in image.segments}


def main() -> int:
    for path in (ORIG, NEW):
        if not path.exists():
            print("文件不存在:", path)
            return 1

    orig_data = ORIG.read_bytes()
    new_data = NEW.read_bytes()
    dump_header(orig_data, "原文件 vendor_boot_o.img")
    dump_header(new_data, "导出 vendor_boot_new.img")

    orig_segs = dump_segments(ORIG, "原文件")
    new_segs = dump_segments(NEW, "导出")

    print("\n== 同名段数据对比 ==")
    for name in orig_segs:
        a = orig_segs[name]
        b = new_segs.get(name)
        if b is None:
            print("  %-24s 导出中缺失!" % name)
        else:
            same = a == b if name != "dtb" else "大小 %d vs %d" % (len(a), len(b))
            print("  %-24s %s" % (name, same))

    # 原文件中最后 4KB 的内容预览（判断尾部数据是什么）
    print("\n== 原文件尾部特征 ==")
    tail = orig_data[-65536:]
    for i in range(0, min(len(tail) - 16, 4096)):
        if tail[i:i + 4] == b"\x1f\x8b\x08" or tail[i:i + 4] == b"\x04\x22\x4d\x18" \
                or tail[i:i + 4] == b"\x28\xb5\x2f\xfd":
            print("  尾部发现压缩流 magic: %s @ -%d" % (tail[i:i + 4].hex(), len(tail) - i))
            break
    print("  尾部 16 字节: %s" % tail[-16:].hex())
    print("  尾部前部是否为 0 填充: %s" % (set(tail[:512]) <= {0}))

    return 0


if __name__ == "__main__":
    sys.exit(main())
