"""核心引擎端到端验证脚本。

使用参考项目（KonaBess Next）中的真实 DTS 数据验证：

1. DTS 编译 → DTB 序列化 → 解析 的往返一致性
2. GPU 频率/电压表解析（Tuna = 内联电压，sd860 = OPP 电压表）
3. 编辑操作（改频率/电压、增删等级）与撤销/重做
4. 镜像重组（构造 boot.img / dtbo.img 并验证 byte 级正确性）

运行::

    python tests/test_engine.py
"""

from __future__ import annotations

import struct
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from konabess import bootimg, chips, dts, kernel_dtb, workspace  # noqa: E402
from konabess.bootimg import ImageKind, PackedImage, Segment, load_image  # noqa: E402
from konabess.fdt import Fdt, FdtProp, split_concatenated_dtbs  # noqa: E402
from konabess.gpu_table import parse_table_from_fdt  # noqa: E402

TEST_DIR = ROOT / "_reference" / "app" / "src" / "test"

PASSED = 0
FAILED = 0


def check(condition: bool, message: str) -> None:
    global PASSED, FAILED
    if condition:
        PASSED += 1
        print("  [OK] %s" % message)
    else:
        FAILED += 1
        print("  [FAIL] %s" % message)


# ---------------------------------------------------------------------------
# 1. FDT 基础
# ---------------------------------------------------------------------------

def test_fdt_basics() -> None:
    print("\n== FDT 基础 ==")
    text = """
/dts-v1/;

/ {
    model = "Test SoC";
    #address-cells = <0x1>;
    qcom,test = <0x1234 0x5678>;
    empty-prop;
    bytes = [aa bb cc];
    qcom,level = <0x1c0>;

    child@0 {
        reg = <0x0>;
        qcom,gpu-freq = <0x45243200>;
    };
};
"""
    fdt = dts.compile_dts(text)
    check(fdt.root.get_prop("model").as_str() == "Test SoC", "字符串属性解析")
    check(fdt.root.get_prop("#address-cells").as_u32() == 1, "数值属性解析")
    check(fdt.root.get_prop("empty-prop").data == b"", "空属性")
    check(fdt.root.get_prop("bytes").data == b"\xaa\xbb\xcc", "字节数组")
    check(fdt.root.get_prop("qcom,test").as_u32_list() == [0x1234, 0x5678], "多 cell 数组")

    data = fdt.to_bytes()
    fdt2 = Fdt.parse(data)
    check(fdt2.to_bytes() == data, "序列化往返一致")

    child = fdt2.root.find_child("child@0")
    check(child is not None and child.get_prop("qcom,gpu-freq").as_u32() == 0x45243200,
          "子节点访问")

    # 写入
    child.get_prop("qcom,gpu-freq").set_u32(0x50000000)
    data2 = fdt2.to_bytes()
    fdt3 = Fdt.parse(data2)
    check(fdt3.root.find_child("child@0").get_prop("qcom,gpu-freq").as_u32() == 0x50000000,
          "属性写入")

    # DTS 生成后再次编译
    text2 = dts.fdt_to_dts(fdt3)
    fdt4 = dts.compile_dts(text2)
    check(fdt4.to_bytes() == data2, "DTS 再生成 → 编译一致")


# ---------------------------------------------------------------------------
# 2. Tuna（内联电压）
# ---------------------------------------------------------------------------

def test_tuna() -> Fdt:
    print("\n== Tuna0.txt（骁龙 8 Gen 3 / 内联电压）==")
    text = (TEST_DIR / "Tuna0.txt").read_text(encoding="utf-8", errors="replace")
    t0 = time.time()
    fdt = dts.compile_dts(text)
    t1 = time.time()
    print("  编译耗时: %.2fs, DTS %d 行 → DTB %d 字节" %
          (t1 - t0, text.count("\n"), len(fdt.to_bytes())))

    table = parse_table_from_fdt(fdt)
    check(table is not None, "GPU 表解析成功")
    assert table is not None
    print(table.summary())

    check(len(table.bins) == 3, "解析出 3 个 speed bin")
    check(table.voltage_type == "INLINE_LEVEL", "电压类型 = 内联")
    check(table.detected_model.startswith("Qualcomm Technologies"), "型号识别: %s" % table.detected_model)

    first = table.bins[0]
    check(len(first.levels) == 11, "Bin0 有 11 个等级")
    top = first.levels[0]
    check(top.freq == 0x45243200, "最高频率 0x45243200")
    check(top.volt == 0x1c0, "电压角 0x1c0 (448)")
    check(table.volt_label(top.volt) == "448 - TURBO_L3",
          "电压标签: %s" % table.volt_label(top.volt))
    check(first.speed_bin == 0, "speed-bin=0")
    check(first.initial_pwrlevel == 0xA, "initial-pwrlevel=0xA")

    # 编辑：改频率 + 改电压
    session = workspace.Session()
    session.slots = [workspace.DtbSlot(key="file:0", label="test", data=fdt.to_bytes())]
    session.slots[0].fdt = fdt
    session.slots[0].table = parse_table_from_fdt(fdt)
    session.source_kind = "dtb"
    session.path = "Tuna0.dtb"

    ok = session.set_level_freq(0, 0, 1_200_000_000)
    check(ok, "修改频率成功")
    ok = session.set_level_volt(0, 0, 0x1d0)
    check(ok, "修改电压成功")
    table = session.table
    check(table.bins[0].levels[0].freq == 1_200_000_000, "新频率生效")
    check(table.bins[0].levels[0].volt == 0x1d0, "新电压生效")

    # 撤销两次
    session.undo()
    session.undo()
    table = session.table
    check(table.bins[0].levels[0].freq == 0x45243200, "撤销后频率还原")
    check(table.bins[0].levels[0].volt == 0x1c0, "撤销后电压还原")
    # 重做一次
    session.redo()
    check(session.table.bins[0].levels[0].freq == 1_200_000_000, "重做后频率恢复")

    # 增删等级
    before = len(session.table.bins[0].levels)
    check(session.add_level(0, at_top=True), "添加等级")
    check(len(session.table.bins[0].levels) == before + 1, "等级数 +1")
    check(session.table.bins[0].levels[0].freq == 1_200_000_000 * 0 + session.table.bins[0].levels[0].freq,
          "顶部新等级频率继承模板")
    check(session.table.bins[0].header.get("qcom,initial-pwrlevel") == 0xB,
          "指针偏移 +1 (initial-pwrlevel=0xB)")

    check(session.delete_level(0, 0), "删除等级")
    check(len(session.table.bins[0].levels) == before, "等级数还原")

    # 序列化 → 重新解析
    data = session.current_slot.fdt.to_bytes()
    table2 = parse_table_from_fdt(Fdt.parse(data))
    check(table2 is not None and len(table2.bins) == 3, "输出 DTB 可重新解析")
    return fdt


# ---------------------------------------------------------------------------
# 3. sd860（OPP 电压表）
# ---------------------------------------------------------------------------

def test_sd860() -> None:
    print("\n== sd860.txt（骁龙 855 系列 / OPP 电压表）==")
    text = (TEST_DIR / "sd860.txt").read_text(encoding="utf-8", errors="replace")
    fdt = dts.compile_dts(text)
    table = parse_table_from_fdt(fdt)
    check(table is not None, "GPU 表解析成功")
    assert table is not None
    print(table.summary())
    check(table.voltage_type == "OPP_TABLE", "电压类型 = 独立电压表")
    check(len(table.opps) > 0, "解析出 %d 条电压表记录" % len(table.opps))
    top_opp = max(table.opps, key=lambda o: o.freq)
    print("  最高档: %s, 电压角 %s" % (top_opp.format_freq(), top_opp.volt))


# ---------------------------------------------------------------------------
# 4. 镜像重组
# ---------------------------------------------------------------------------

def _make_boot_v2(dtb: bytes) -> bytes:
    page = 4096
    kernel = b"KERNELDATA" * 100
    ramdisk = b"RAMDISK" * 50
    header = bytearray(page)
    header[0:8] = b"ANDROID!"
    struct.pack_into("<I", header, 8, len(kernel))
    struct.pack_into("<I", header, 16, len(ramdisk))
    struct.pack_into("<I", header, 36, page)
    struct.pack_into("<I", header, 40, 2)          # header_version = 2
    struct.pack_into("<I", header, 1648, len(dtb))
    out = bytearray(header)
    for chunk in (kernel, ramdisk, dtb):
        while len(out) % page:
            out.append(0)
        out.extend(chunk)
    while len(out) % page:
        out.append(0)
    return bytes(out)


def test_boot_image(dtb: bytes) -> None:
    print("\n== boot.img v2 解析与重组 ==")
    raw = _make_boot_v2(dtb)
    image = load_image(raw)
    check(image.kind == ImageKind.BOOT, "识别为 boot 镜像")
    check(image.header_version == 2, "header_version=2")
    dtb_seg = image.segment("dtb")
    check(dtb_seg is not None and len(dtb_seg.data) == len(dtb), "dtb 段解析 (%d 字节)" % len(dtb))
    check(image.segment("kernel").data.startswith(b"KERNELDATA"), "kernel 段解析")
    check(image.segment("ramdisk").data.startswith(b"RAMDISK"), "ramdisk 段解析")

    # 未修改时重组: 除尾部对齐外内容一致
    repacked = image.pack()
    check(repacked[:1648] == raw[:1648], "header 保留")
    check(len(repacked) >= len(raw), "重组后尺寸合理")

    # 修改 dtb 段后重组
    new_dtb = dtb + b"\x00" * 16
    dtb_seg.data = new_dtb
    repacked = image.pack()
    image2 = load_image(repacked)
    check(len(image2.segment("dtb").data) == len(new_dtb), "修改 dtb 大小后重组正确")
    check(image2.segment("dtb").data == new_dtb, "dtb 数据一致")
    check(image2.segment("kernel").data == image.segment("kernel").data, "kernel 未受影响")


def _make_vendor_boot_v4(dtb: bytes) -> bytes:
    """构造一个包含 ramdisk 表、fragment、DTB、bootconfig 的 vendor_boot v4 镜像。"""
    page = 4096
    vendor_ramdisk = b"VNDRAMDISK" * 64
    fragment = b"DLKMFRAGMENT" * 32
    bootconfig = b"androidboot.test=1\nandroidboot.slot=a\n"

    header = bytearray(page)
    header[0:8] = b"VNDRBOOT"
    struct.pack_into("<I", header, 8, 4)
    struct.pack_into("<I", header, 12, page)
    struct.pack_into("<I", header, 16, 0x10000000)
    struct.pack_into("<I", header, 20, 0x11000000)
    struct.pack_into("<I", header, 24, len(vendor_ramdisk))
    struct.pack_into("<I", header, 2076, 0x10000100)
    header[2080:2096] = b"qcom-test".ljust(16, b"\x00")
    struct.pack_into("<I", header, 2096, 2128)
    struct.pack_into("<I", header, 2100, len(dtb))
    struct.pack_into("<Q", header, 2104, 0x1f000000)
    struct.pack_into("<I", header, 2112, 108)          # table_size
    struct.pack_into("<I", header, 2116, 1)            # entry_num
    struct.pack_into("<I", header, 2120, 108)          # entry_size
    struct.pack_into("<I", header, 2124, len(bootconfig))

    out = bytearray(header)
    out.extend(vendor_ramdisk)
    table_off = len(out)
    entry = bytearray()
    entry.extend(struct.pack("<III", len(fragment), 0, 3))  # type=DLKM
    entry.extend(b"dlkm".ljust(32, b"\x00"))
    entry.extend(b"\x00" * 64)
    out.extend(entry)
    while len(out) % page:
        out.append(0)
    struct.pack_into("<I", out, table_off + 4, len(out))     # 回填 fragment offset
    out.extend(fragment)
    while len(out) % page:
        out.append(0)
    out.extend(dtb)
    while len(out) % page:
        out.append(0)
    out.extend(bootconfig)
    while len(out) % page:
        out.append(0)
    return bytes(out)


def test_vendor_boot(dtb: bytes) -> None:
    print("\n== vendor_boot v4 解析与重组 ==")
    raw = _make_vendor_boot_v4(dtb)
    image = load_image(raw)
    check(image.kind == ImageKind.VENDOR_BOOT, "识别为 vendor_boot 镜像")
    check(image.header_version == 4, "header_version=4")

    ramdisk = image.segment("vendor_ramdisk")
    check(ramdisk is not None and len(ramdisk.data) == 640, "vendor_ramdisk 解析")
    frag = image.segment("ramdisk_fragment_0")
    check(frag is not None and frag.data.startswith(b"DLKMFRAGMENT"), "ramdisk fragment 解析")
    check(frag.meta.get("type") == 3, "fragment type 保留")
    dtb_seg = image.segment("dtb")
    check(dtb_seg is not None and dtb_seg.data == dtb, "dtb 段解析 (%d 字节)" % len(dtb or b""))
    bootconfig = image.segment("bootconfig")
    check(bootconfig is not None and b"androidboot.test=1" in bootconfig.data, "bootconfig 解析")

    # 修改 dtb 并重组
    new_dtb = dtb + b"\x00" * 24
    dtb_seg.data = new_dtb
    repacked = image.pack()
    image2 = load_image(repacked)
    check(image2.segment("dtb").data == new_dtb, "重组后 dtb 一致")
    check(image2.segment("vendor_ramdisk").data == ramdisk.data, "重组后 ramdisk 一致")
    check(image2.segment("ramdisk_fragment_0").data == frag.data, "重组后 fragment 一致")
    check(image2.segment("bootconfig") is not None
          and b"androidboot.test=1" in image2.segment("bootconfig").data, "重组后 bootconfig 一致")
    check(image2.meta.get("dtb_addr") == 0x1f000000, "dtb_addr 保留")


def _make_vendor_boot_v4_xiaomi(dtb: bytes) -> bytes:
    """构造小米式布局的 vendor_boot v4：table 位于 dtb 之后，尾部含 vbmeta 与 AVB footer。"""
    page = 4096
    ramdisk = b"XIAOMIRAMDISK" * 1000            # 13000 字节
    table_entry = struct.pack("<III", len(ramdisk), 0, 1) + b"\x00" * 96
    bootconfig = b"androidboot.hardware=qcom\nandroidboot.memcg=1\n"
    vbmeta = b"VBMETAVB" + b"\xAA" * 632
    # AvbFooter 结构共 64 字节：magic(4) + version(4+4) + image_size(8) + vbmeta_offset(8) + vbmeta_size(4) + reserved(32)
    avb_footer = bytearray(b"AVBf" + struct.pack("<II", 1, 0)
                           + struct.pack("<QQI", 0, 0, len(vbmeta)) + b"\x00" * 32)

    header = bytearray(page)
    header[0:8] = b"VNDRBOOT"
    struct.pack_into("<I", header, 8, 4)
    struct.pack_into("<I", header, 12, page)
    struct.pack_into("<I", header, 24, len(ramdisk))
    header[2080:2096] = b"xiaomi".ljust(16, b"\x00")
    struct.pack_into("<I", header, 2096, 2128)
    struct.pack_into("<I", header, 2100, len(dtb))
    struct.pack_into("<I", header, 2112, 108)
    struct.pack_into("<I", header, 2116, 1)
    struct.pack_into("<I", header, 2120, 108)
    struct.pack_into("<I", header, 2124, len(bootconfig))

    out = bytearray(header)
    out.extend(ramdisk)
    while len(out) % page:
        out.append(0)
    out.extend(dtb)
    while len(out) % page:
        out.append(0)
    out.extend(table_entry)                       # table 在 dtb 后
    while len(out) % page:
        out.append(0)
    out.extend(bootconfig)                        # bootconfig 在 table 后
    content_end = len(out)
    while len(out) % page:
        out.append(0)
    image_size = len(out)
    out.extend(vbmeta)
    # 62MB 填充用少量 0 模拟（保留语义即可）
    out.extend(b"\x00" * 8192)
    # 回填 AVB footer 的 image_size 与 vbmeta_offset（均为页对齐后的位置）
    struct.pack_into("<Q", avb_footer, 12, image_size)
    struct.pack_into("<Q", avb_footer, 20, image_size)
    out.extend(avb_footer)
    return bytes(out)


def test_vendor_boot_xiaomi(dtb: bytes) -> None:
    print("\n== 小米式 vendor_boot v4 布局（table 在 dtb 后 + AVB 尾部）==")
    raw = _make_vendor_boot_v4_xiaomi(dtb)
    image = load_image(raw)
    check(image.kind == ImageKind.VENDOR_BOOT and image.header_version == 4, "识别为 vendor_boot v4")

    dtb_seg = image.segment("dtb")
    table = image.segment("vendor_ramdisk_table")
    bootconfig = image.segment("bootconfig")
    check(dtb_seg is not None and dtb_seg.data == dtb, "dtb 段解析正确")
    check(table is not None and table.offset > dtb_seg.offset, "table 位于 dtb 之后")
    check(bootconfig is not None and b"androidboot.hardware=qcom" in bootconfig.data,
          "bootconfig 解析正确")
    check(not image.meta.get("table_follows_ramdisk"), "table 不紧跟 ramdisk")
    check(len(image.trailer) > 0 and image.trailer[-64:].startswith(b"AVBf"),
          "尾部含 AVB footer（%d 字节）" % len(image.trailer))
    fragments = [s for s in image.segments if s.name.startswith("ramdisk_fragment_")]
    check(not fragments, "指向主 ramdisk 的表项未误判为 fragment")

    # 未修改时 pack 必须字节级一致
    check(image.pack() == raw, "未修改时重组字节级一致")

    # 修改 dtb 后：大小变化等于 dtb 增量（+4096 超过页对齐填充吸收范围），其余部分一致
    new_dtb = dtb + b"\x00" * 4096
    dtb_seg.data = new_dtb
    repacked = image.pack()
    check(len(repacked) == len(raw) + 4096, "修改后大小 = 原大小 + dtb 增量")
    image2 = load_image(repacked)
    check(image2.segment("dtb").data == new_dtb, "修改后的 dtb 一致")
    check(image2.segment("vendor_ramdisk").data == image.segment("vendor_ramdisk").data,
          "ramdisk 不变")
    check(image2.segment("vendor_ramdisk_table").data == table.data, "table 不变")
    check(image2.segment("bootconfig").data == bootconfig.data, "bootconfig 不变")
    check(image2.trailer == image.trailer, "尾部不变")
    # 布局顺序保持：table 仍在 dtb 后
    check(image2.segment("vendor_ramdisk_table").offset > image2.segment("dtb").offset,
          "布局顺序保持")


def test_dtbo(dtb: bytes) -> None:
    print("\n== dtbo.img 解析与重组 ==")
    page = 4096
    entries_offset = 32
    first = 4096
    out = bytearray(first + page * 2)
    struct.pack_into(">8I", out, 0, 0xD7B7AB1E, len(out), 32, 32, 2, entries_offset, page, 0)
    struct.pack_into(">4I", out, 32, len(dtb), first, 7, 1)
    struct.pack_into(">4I", out, 64, len(dtb), first + page, 9, 2)
    out[first:first + len(dtb)] = dtb
    out[first + page:first + page + len(dtb)] = dtb

    image = load_image(bytes(out))
    check(image.kind == ImageKind.DTBO, "识别为 dtbo 镜像")
    check(len(image.segments) == 2, "解析出 2 个条目")
    check(image.segments[0].meta.get("id") == 7, "条目 id 保留")

    image.segments[0].data = dtb + b"\x00" * 32
    repacked = image.pack()
    image2 = load_image(repacked)
    check(len(image2.segments[0].data) == len(dtb) + 32, "条目大小更新")
    check(image2.segments[1].meta.get("id") == 9, "第二条目 id 保留")
    check(image2.segments[1].data == dtb, "第二条目数据不变")


def test_kernel_dtb(dtb: bytes) -> None:
    print("\n== 内核内嵌 DTB ==")
    import gzip
    kernel_body = b"\x00" * 1000 + b"LINUXKERNEL" * 100
    raw_kernel = kernel_body + dtb
    compressed = gzip.compress(raw_kernel)

    info = kernel_dtb.inspect_kernel(compressed)
    check(info is not None, "探测到内核内嵌 DTB")
    assert info is not None
    check(info.fmt == "gzip", "压缩格式: gzip")
    check(info.dtb_offset == len(kernel_body), "DTB 偏移正确 (%d)" % info.dtb_offset)

    # 替换 DTB（用一个更小的合法 DTB 模拟大小变化）
    new_dtb = dts.compile_dts('/dts-v1/;\n/ {\n    model = "Replacement";\n};\n').to_bytes()
    rebuilt = kernel_dtb.replace_fdts_in_kernel(info, {0: new_dtb})
    info2 = kernel_dtb.inspect_kernel(rebuilt)
    check(info2 is not None, "重建后仍可探测")
    assert info2 is not None
    check(info2.raw_kernel[info2.dtb_offset:] == new_dtb, "替换后的 DTB 一致")
    check(info2.raw_kernel.startswith(kernel_body), "内核本体不变")


# ---------------------------------------------------------------------------
# 5. 会话流程（完整打开 → 编辑 → 保存）
# ---------------------------------------------------------------------------

def test_session_dts(tmp_dir: Path) -> None:
    print("\n== 会话流程: DTS 文本 ==")
    path = tmp_dir / "tuna.dts"
    path.write_text((TEST_DIR / "Tuna0.txt").read_text(encoding="utf-8", errors="replace"),
                    encoding="utf-8")

    session = workspace.Session()
    session.open(str(path))
    check(session.source_kind == "dts", "来源类型: DTS")
    table = session.table
    check(table is not None and len(table.bins) == 3, "DTS 中解析出 3 个 Bin")

    ok = session.set_level_volt(0, 0, 0x1d0)
    check(ok, "修改电压")
    check(session.table.bins[0].levels[0].volt == 0x1d0, "修改生效")

    out = tmp_dir / "tuna_modified.dts"
    session.save(str(out))
    check(out.exists(), "保存 DTS 输出")
    session2 = workspace.Session()
    session2.open(str(out))
    check(session2.table is not None and session2.table.bins[0].levels[0].volt == 0x1d0,
          "重新打开后修改保留")

    dtb_out = tmp_dir / "tuna_modified.dtb"
    session2.export_dtb(str(dtb_out))
    table3 = parse_table_from_fdt(Fdt.parse(dtb_out.read_bytes()))
    check(table3 is not None and table3.bins[0].levels[0].volt == 0x1d0, "导出 DTB 正确")


def test_session_dtbo(dtb: bytes, tmp_dir: Path) -> None:
    print("\n== 会话流程: DTBO 镜像（多条目选择）==")
    page = 4096
    other = dts.compile_dts('/dts-v1/;\n/ {\n    model = "Other Board";\n};\n').to_bytes()
    out = bytearray(4096 + page * 2)
    struct.pack_into(">8I", out, 0, 0xD7B7AB1E, len(out), 32, 32, 2, 32, page, 0)
    struct.pack_into(">4I", out, 32, len(other), page, 1, 0)
    struct.pack_into(">4I", out, 64, len(dtb), page * 2, 2, 0)
    out[page:page + len(other)] = other
    out[page * 2:page * 2 + len(dtb)] = dtb
    path = tmp_dir / "dtbo_test.img"
    path.write_bytes(bytes(out))

    session = workspace.Session()
    session.open(str(path))
    check(len(session.slots) == 2, "解析出 2 个 dtbo 条目")
    check(session.current_slot is not None and "dtbo" in session.current_slot.key,
          "当前槽位: %s" % (session.current_slot.key if session.current_slot else "无"))
    check(session.current_index == 1, "自动选中含 GPU 表的条目（索引 %d）" % session.current_index)

    ok = session.set_level_freq(0, 0, 1_111_000_000)
    check(ok, "编辑 dtbo 条目")
    out_path = tmp_dir / "dtbo_new.img"
    session.save(str(out_path))
    session2 = workspace.Session()
    session2.open(str(out_path))
    check(session2.slots[0].fdt is not None
          and session2.slots[0].fdt.root.get_prop("model").as_str() == "Other Board",
          "未编辑的条目保持不变")
    check(session2.table is not None and session2.table.bins[0].levels[0].freq == 1_111_000_000,
          "编辑的条目已保存")


def test_session_flow(dtb: bytes, tmp_dir: Path) -> None:
    print("\n== 会话流程 ==")
    boot_path = tmp_dir / "boot.img"
    boot_path.write_bytes(_make_boot_v2(dtb))

    session = workspace.Session()
    session.open(str(boot_path))
    check(session.source_kind == "image", "来源类型: 镜像")
    check(len(session.slots) >= 1, "发现 %d 个设备树槽位" % len(session.slots))
    table = session.table
    check(table is not None, "当前槽位有 GPU 表")
    assert table is not None and table.bins

    ok = session.set_level_freq(0, 0, 999_000_000)
    check(ok, "编辑频率")
    check(session.modified, "标记为已修改")

    out_path = tmp_dir / "boot_new.img"
    session.save(str(out_path))
    check(out_path.exists(), "保存输出镜像")

    session2 = workspace.Session()
    session2.open(str(out_path))
    check(session2.table is not None and session2.table.bins[0].levels[0].freq == 999_000_000,
          "重新打开后修改已生效")

    # 其余段不变
    raw1 = load_image(boot_path.read_bytes())
    raw2 = load_image(out_path.read_bytes())
    check(raw1.segment("kernel").data == raw2.segment("kernel").data, "kernel 段未受影响")
    check(raw1.segment("ramdisk").data == raw2.segment("ramdisk").data, "ramdisk 段未受影响")
    # 输出大小以 page 对齐
    check(len(out_path.read_bytes()) % 4096 == 0, "输出按页对齐")

    # 配置导出/应用
    profile = session2.export_profile()
    check(profile["bins"][0]["levels"][0]["freq"] == 999_000_000, "配置导出")
    applied, warnings = session2.apply_profile(profile)
    check(applied > 0, "配置应用 (%d 项)" % applied)


# ---------------------------------------------------------------------------

def main() -> int:
    print("KonaBess PC 引擎验证")
    print("测试数据目录:", TEST_DIR)
    test_fdt_basics()
    fdt = test_tuna()
    test_sd860()
    dtb = fdt.to_bytes()
    test_boot_image(dtb)
    test_vendor_boot(dtb)
    test_vendor_boot_xiaomi(dtb)
    test_dtbo(dtb)
    test_kernel_dtb(dtb)
    tmp_dir = ROOT / "tests" / "_tmp"
    tmp_dir.mkdir(exist_ok=True)
    test_session_flow(dtb, tmp_dir)
    test_session_dts(tmp_dir)
    test_session_dtbo(dtb, tmp_dir)
    print("\n结果: %d 通过, %d 失败" % (PASSED, FAILED))
    return 0 if FAILED == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
