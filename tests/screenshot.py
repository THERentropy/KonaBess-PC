"""界面截图脚本（开发验证用）。

运行后会在 tests/_tmp/ 下生成若干 PNG 截图。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PIL import ImageGrab  # noqa: E402

from main import enable_high_dpi  # noqa: E402
from ui import theme  # noqa: E402

theme.set_scale(enable_high_dpi())

OUT_DIR = ROOT / "tests" / "_tmp"


def grab_window(window, name: str) -> None:
    for _ in range(6):
        window.update_idletasks()
        window.update()
    x = window.winfo_rootx()
    y = window.winfo_rooty()
    w = window.winfo_width()
    h = window.winfo_height()
    img = ImageGrab.grab(bbox=(x, y, x + w, y + h))
    path = OUT_DIR / ("ui_%s.png" % name)
    img.save(path)
    print("saved:", path, img.size)


def main() -> int:
    OUT_DIR.mkdir(exist_ok=True)
    boot = OUT_DIR / "ui_test_boot.img"
    if not boot.exists():
        print("请先运行 tests/test_ui.py 生成测试镜像")
        return 1

    from ui.app import MainWindow
    from ui.dialogs import NumberDialog, VoltageDialog

    app = MainWindow()
    app.geometry("%dx%d+20+10" % (theme.px(1280), theme.px(770)))
    app.attributes("-topmost", True)
    app.update_idletasks()
    app.update()
    print("window size:", app.winfo_width(), app.winfo_height())

    def step_loaded() -> None:
        app.open_file(str(boot))
        grab_window(app, "01_levels")

    def step_curve() -> None:
        if app._curve_panel is not None:
            app.notebook.select(app._curve_panel)
            app.update()
            app._curve_panel.redraw()
            grab_window(app, "02_curve")

    def step_voltage_dialog() -> None:
        dialog = VoltageDialog(app, app.session.table, 448, title="选择电压档位")
        dialog.after(500, lambda: grab_window(dialog, "03_voltage"))
        dialog.after(800, dialog.destroy)
        dialog.show()

    def step_freq_dialog() -> None:
        from ui.dialogs import FreqDialog
        dialog = FreqDialog(app, 1_160_000_000)
        dialog.after(500, lambda: grab_window(dialog, "04_freq"))
        dialog.after(800, dialog.destroy)
        dialog.show()

    def step_dts() -> None:
        app.notebook.select(app.notebook.tabs()[-1])
        app.update()
        app._load_dts_preview()
        grab_window(app, "05_dts")

    def step_help() -> None:
        from ui.dialogs import HELP_TEXT, TextDialog
        dialog = TextDialog(app, "使用说明", HELP_TEXT)
        dialog.after(600, lambda: grab_window(dialog, "06_help"))
        dialog.after(900, dialog.destroy)
        dialog.show()

    app.after(500, step_loaded)
    app.after(1600, step_curve)
    app.after(2500, step_voltage_dialog)
    app.after(3600, step_freq_dialog)
    app.after(4600, step_dts)
    app.after(6200, step_help)
    app.after(7600, app.destroy)
    app.mainloop()
    print("截图完成")
    return 0


if __name__ == "__main__":
    sys.exit(main())
