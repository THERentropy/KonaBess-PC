"""KonaBess PC —— 骁龙 GPU 频率/电压表桌面编辑器。

参考 KonaBess Next（Android）重新实现，可在电脑上直接完成：

1. 打开 boot.img / vendor_boot.img / dtbo.img / .dtb / .dts
2. 解析并图形化编辑 GPU 频率表与电压表
3. 导出可刷入的镜像（fastboot flash）
"""

__version__ = "1.0.0"
__app_name__ = "KonaBess PC"

__all__ = ["__version__", "__app_name__"]
