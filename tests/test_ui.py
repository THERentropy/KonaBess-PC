"""界面冒烟测试：构建窗口、加载真实镜像、刷新面板、保存输出。

运行::

    python tests/test_ui.py
"""

from __future__ import annotations

import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from konabess import bootimg, dts  # noqa: E402
from konabess.fdt import Fdt  # noqa: E402

TEST_DIR = ROOT / "_reference" / "app" / "src" / "test"


def build_test_boot_image() -> bytes:
    """用参考项目的 Tuna DTS 构造一个 boot.img v2。"""
    text = (TEST_DIR / "Tuna0.txt").read_text(encoding="utf-8", errors="replace")
    dtb = dts.compile_dts(text).to_bytes()

    page = 4096
    kernel = b"K" * 1024
    ramdisk = b"R" * 2048
    header = bytearray(page)
    header[0:8] = b"ANDROID!"
    struct.pack_into("<I", header, 8, len(kernel))
    struct.pack_into("<I", header, 16, len(ramdisk))
    struct.pack_into("<I", header, 36, page)
    struct.pack_into("<I", header, 40, 2)
    struct.pack_into("<I", header, 1648, len(dtb))
    out = bytearray(header)
    for chunk in (kernel, ramdisk, dtb):
        while len(out) % page:
            out.append(0)
        out.extend(chunk)
    while len(out) % page:
        out.append(0)
    return bytes(out)


def main() -> int:
    tmp = ROOT / "tests" / "_tmp"
    tmp.mkdir(exist_ok=True)
    boot_path = tmp / "ui_test_boot.img"
    boot_path.write_bytes(build_test_boot_image())
    print("测试镜像: %s (%.2f MB)" % (boot_path, boot_path.stat().st_size / 1048576))

    from ui.app import MainWindow

    app = MainWindow()
    app.geometry("1100x720")
    results: list[str] = []

    def step_load() -> None:
        app.open_file(str(boot_path))
        session = app.session
        assert session is not None, "会话为空"
        assert session.table is not None, "未解析出表"
        results.append("加载镜像: %d 个 Bin, %d 个面板" %
                       (len(session.table.bins), len(app._level_panels)))
        assert len(app._level_panels) == len(session.table.bins)

    def step_edit() -> None:
        session = app.session
        ok = session.set_level_freq(0, 0, 1_234_000_000)
        assert ok, "编辑失败"
        app.on_session_changed("测试编辑")
        value = app.session.table.bins[0].levels[0].freq
        assert value == 1_234_000_000, "编辑值未生效: %s" % value
        results.append("编辑频率并刷新面板 OK")

    def step_curve() -> None:
        panel = app._curve_panel
        assert panel is not None, "曲线面板缺失"
        app.notebook.select(panel)
        app.update()
        panel.redraw()
        results.append("曲线重绘: %d 个点" % len(panel._points))
        assert len(panel._points) > 0

    def step_dts_preview() -> None:
        app._load_dts_preview()
        text = app._dts_text.get("1.0", "end")
        assert len(text) > 1000, "DTS 预览太短"
        results.append("DTS 预览: %d 字符" % len(text))

    def step_undo() -> None:
        app.undo()
        value = app.session.table.bins[0].levels[0].freq
        assert value != 1_234_000_000, "撤销未生效"
        app.redo()
        value = app.session.table.bins[0].levels[0].freq
        assert value == 1_234_000_000, "重做未生效"
        results.append("撤销/重做 OK")

    def step_save() -> None:
        out = tmp / "ui_test_out.img"
        app.session.save(str(out))
        assert out.exists()
        reopened = bootimg.load_image(out.read_bytes())
        dtb_seg = reopened.segment("dtb")
        table = Fdt.parse(dtb_seg.data)
        results.append("保存输出: %.2f MB" % (out.stat().st_size / 1048576))
        assert reopened.segment("kernel").data == b"K" * 1024

    steps = [step_load, step_edit, step_curve, step_dts_preview, step_undo, step_save]

    def run_steps(index: int = 0) -> None:
        if index >= len(steps):
            print("\n".join("  [OK] " + item for item in results))
            print("\nUI 冒烟测试全部通过")
            app.destroy()
            return
        try:
            steps[index]()
        except Exception as exc:  # noqa: BLE001
            print("  [FAIL] %s: %s" % (steps[index].__name__, exc))
            import traceback
            traceback.print_exc()
            app.destroy()
            sys.exit(1)
        app.after(200, lambda: run_steps(index + 1))

    app.after(300, run_steps)
    app.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
