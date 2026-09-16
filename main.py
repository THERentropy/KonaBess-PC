"""KonaBess PC 启动入口。

用法::

    python main.py [镜像文件]

也可以直接双击 run.bat（Windows）。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


def enable_high_dpi() -> float:
    """启用高 DPI 感知并返回缩放因子（1.0 = 100%）。"""
    if sys.platform != "win32":
        return 1.0
    try:
        import ctypes
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            ctypes.windll.user32.SetProcessDPIAware()
        try:
            dpi = ctypes.windll.user32.GetDpiForSystem()
        except Exception:
            dpi = 96
        return max(1.0, dpi / 96.0)
    except Exception:
        return 1.0


def self_check() -> int:
    """自检：验证核心引擎与依赖完整性（不启动界面）。"""
    from konabess import __version__, compression
    from konabess import dts
    from konabess.fdt import Fdt
    from konabess.gpu_table import parse_table_from_fdt

    sample = """
/dts-v1/;
/ {
    model = "Qualcomm Technologies, Inc. Selfcheck SoC";
    qcom,kgsl-3d0 {
        compatible = "qcom,kgsl-3d0";
        qcom,gpu-model = "Adreno 999";
        qcom,gpu-pwrlevel-bins {
            qcom,gpu-pwrlevels-0 {
                qcom,speed-bin = <0x0>;
                qcom,initial-pwrlevel = <0x0>;
                qcom,gpu-pwrlevel@0 {
                    qcom,gpu-freq = <0x3b9aca00>;
                    qcom,level = <0x100>;
                    reg = <0x0>;
                };
            };
        };
    };
};
"""
    fdt = dts.compile_dts(sample)
    table = parse_table_from_fdt(fdt)
    assert table is not None and table.bins, "频率表解析失败"
    level = table.bins[0].levels[0]
    assert level.freq == 1_000_000_000 and level.volt == 0x100, "数值解析错误"

    data = fdt.to_bytes()
    assert Fdt.parse(data).to_bytes() == data, "序列化往返不一致"

    print("KonaBess PC %s 自检通过" % __version__)
    print("芯片识别: %s" % table.chip_display)
    print("压缩支持: %s" % compression.format_support_status())
    return 0


def main() -> int:
    if "--check" in sys.argv or "--selftest" in sys.argv:
        return self_check()

    from ui import theme
    from ui.app import MainWindow

    theme.set_scale(enable_high_dpi())
    app = MainWindow()
    if len(sys.argv) > 1:
        target = sys.argv[1]
        if Path(target).exists():
            app.after(300, lambda: app.open_file(target))
    app.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
