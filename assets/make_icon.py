"""生成应用图标 assets/icon.ico（深色底 + 红色芯片图形）。"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parent / "icon.ico"

BG = (25, 27, 31, 255)
ACCENT = (232, 80, 58, 255)
ACCENT_DIM = (168, 59, 43, 255)


def draw_icon(size: int = 256) -> Image.Image:
    scale = size / 256.0

    def s(value: float) -> float:
        return value * scale

    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # 背景圆角方块
    draw.rounded_rectangle([s(6), s(6), s(250), s(250)], radius=s(52), fill=BG)

    # 芯片引脚
    for i in range(4):
        offset = s(86 + i * 24)
        draw.rectangle([s(34), offset, s(66), offset + s(13)], fill=ACCENT_DIM)
        draw.rectangle([s(190), offset, s(222), offset + s(13)], fill=ACCENT_DIM)
        x = s(86 + i * 24)
        draw.rectangle([x, s(34), x + s(13), s(66)], fill=ACCENT_DIM)
        draw.rectangle([x, s(190), x + s(13), s(222)], fill=ACCENT_DIM)

    # 芯片主体
    draw.rounded_rectangle([s(64), s(64), s(192), s(192)], radius=s(26), fill=ACCENT)
    # 内部镂空
    draw.rounded_rectangle([s(96), s(96), s(160), s(160)], radius=s(14), fill=BG)
    # 中心点
    draw.rounded_rectangle([s(118), s(118), s(138), s(138)], radius=s(6), fill=ACCENT)

    return img


def main() -> None:
    base = draw_icon(256)
    sizes = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
    base.save(OUT, format="ICO", sizes=sizes)
    print("已生成:", OUT, OUT.stat().st_size, "字节")


if __name__ == "__main__":
    main()
