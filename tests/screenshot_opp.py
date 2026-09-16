"""生成 sd860（独立 OPP 电压表）场景的界面截图。"""

from __future__ import annotations

import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PIL import ImageGrab  # noqa: E402

from konabess import dts  # noqa: E402
from main import enable_high_dpi  # noqa: E402
from ui import theme  # noqa: E402

theme.set_scale(enable_high_dpi())
TMP = ROOT / "tests" / "_tmp"
TMP.mkdir(exist_ok=True)


def build_sd860_boot() -> Path:
    text = (ROOT / "_reference" / "app" / "src" / "test" / "sd860.txt").read_text(
        encoding="utf-8", errors="replace")
    dtb = dts.compile_dts(text).to_bytes()
    page = 4096
    header = bytearray(page)
    header[0:8] = b"ANDROID!"
    struct.pack_into("<I", header, 8, 2048)
    struct.pack_into("<I", header, 16, 1024)
    struct.pack_into("<I", header, 36, page)
    struct.pack_into("<I", header, 40, 2)
    struct.pack_into("<I", header, 1648, len(dtb))
    out = bytearray(header)
    for chunk in (b"K" * 2048, b"R" * 1024, dtb):
        while len(out) % page:
            out.append(0)
        out.extend(chunk)
    path = TMP / "sd860_boot.img"
    path.write_bytes(bytes(out))
    print("sd860 测试镜像:", path, "%.2f MB" % (len(out) / 1048576))
    return path


def main() -> int:
    boot = build_sd860_boot()

    from ui.app import MainWindow

    app = MainWindow()
    app.geometry("%dx%d+20+10" % (theme.px(1280), theme.px(770)))
    app.attributes("-topmost", True)

    def grab(name: str) -> None:
        for _ in range(5):
            app.update_idletasks()
            app.update()
        x, y = app.winfo_rootx(), app.winfo_rooty()
        w, h = app.winfo_width(), app.winfo_height()
        ImageGrab.grab(bbox=(x, y, x + w, y + h)).save(TMP / ("%s.png" % name))
        print("saved:", name)

    def step_levels() -> None:
        app.open_file(str(boot))
        grab("sd860_levels")

    def step_voltage() -> None:
        if app._volt_panel is None:
            print("警告: 没有电压表面板")
            return
        app.notebook.select(app._volt_panel)
        app.update()
        grab("sd860_voltage")

    app.after(500, step_levels)
    app.after(1700, step_voltage)
    app.after(2800, app.destroy)
    app.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
