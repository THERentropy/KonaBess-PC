"""芯片识别与电压档位（Level）预设表。

移植自 KonaBess Next 的 ``LevelPresets`` / ``DtsScanner`` 逻辑：

- 电压档位命名表（RPMh corner 编号 → 名称，如 448 → TURBO_L3）
- 根据 DTS 中的型号字符串推断应使用的预设表
- 芯片代号 → 市场名称的辅助映射
"""

from __future__ import annotations

import re
from typing import Optional

# ---------------------------------------------------------------------------
# 电压档位预设
# ---------------------------------------------------------------------------

#: 大多数 416 档芯片共有的基础档位（索引 → "编号 - 名称"）
BASE: dict[int, str] = {
    15: "16 - RETENTION",
    47: "48 - MIN_SVS",
    55: "56 - LOW_SVS_D1",
    63: "64 - LOW_SVS",
    79: "80 - LOW_SVS_L1",
    95: "96 - LOW_SVS_L2",
    127: "128 - SVS",
    143: "144 - SVS_L0",
    191: "192 - SVS_L1",
    223: "224 - SVS_L2",
    255: "256 - NOM",
    319: "320 - NOM_L1",
    335: "336 - NOM_L2",
    351: "352 - NOM_L3",
    383: "384 - TURBO",
    399: "400 - TURBO_L0",
    415: "416 - TURBO_L1",
}

#: 480 档的上半部分扩展
UPPER_480: dict[int, str] = {
    431: "432 - TURBO_L2",
    447: "448 - TURBO_L3",
    463: "464 - SUPER_TURBO",
    479: "480 - SUPER_TURBO_NO_CPR",
}

#: Kalama 系列的额外细分级（D2/D0/P1）+ NOM_L0
KALAMA_EXTRA: dict[int, str] = {
    51: "52 - LOW_SVS_D2",
    59: "60 - LOW_SVS_D0",
    71: "72 - LOW_SVS_P1",
    287: "288 - NOM_L0",
}

#: Sun/Canoe 的极细分级（D3/D2.5/D1.5）+ TURBO_L4
SUN_EXTRA: dict[int, str] = {
    49: "50 - LOW_SVS_D3",
    50: "51 - LOW_SVS_D2_5",
    53: "54 - LOW_SVS_D1_5",
    451: "452 - TURBO_L4",
}

STANDARD_416: dict[int, str] = dict(BASE)

LAHAINA_464: dict[int, str] = {
    **BASE,
    431: "432 - TURBO_L2",
    447: "448 - SUPER_TURBO",
    463: "464 - SUPER_TURBO_NO_CPR",
}

KALAMA_480: dict[int, str] = {**BASE, **KALAMA_EXTRA, **UPPER_480}

PINEAPPLE_480: dict[int, str] = {
    15: "16 - RETENTION",
    31: "32 - MIN_SVS",
    47: "48 - LOW_SVS_D1",
    63: "64 - LOW_SVS",
    79: "80 - LOW_SVS_L1",
    95: "96 - LOW_SVS_L2",
    127: "128 - SVS",
    143: "144 - SVS_L0",
    191: "192 - SVS_L1",
    223: "224 - SVS_L2",
    255: "256 - NOM",
    287: "288 - NOM_L0",
    319: "320 - NOM_L1",
    335: "336 - NOM_L2",
    351: "352 - NOM_L3",
    383: "384 - TURBO",
    399: "400 - TURBO_L0",
    415: "416 - TURBO_L1",
    **UPPER_480,
}

SUN_480: dict[int, str] = {**BASE, **KALAMA_EXTRA, **SUN_EXTRA, **UPPER_480}

CLIFFS_MINIMAL: dict[int, str] = {
    15: "16 - RETENTION",
    255: "256 - NOM",
}

ALOR_480: dict[int, str] = {
    49: "50 - LOW_SVS_D3",
    50: "51 - LOW_SVS_D2_5",
    51: "52 - LOW_SVS_D2",
    53: "54 - LOW_SVS_D1_5",
    55: "56 - LOW_SVS_D1",
    59: "60 - LOW_SVS_D0",
    63: "64 - LOW_SVS",
    75: "76 - LOW_SVS_P1",
    79: "80 - LOW_SVS_L1",
    95: "96 - LOW_SVS_L2",
    127: "128 - SVS",
    143: "144 - SVS_L0",
    191: "192 - SVS_L1",
    223: "224 - SVS_L2",
    255: "256 - NOM",
    319: "320 - NOM_L1",
    383: "384 - NOM_L2",
    415: "416 - TURBO_L1",
    431: "432 - TURBO_L2",
    447: "448 - TURBO_L3",
    451: "452 - TURBO_L4",
}

#: 预设名 → 档位表
PRESETS: dict[str, dict[int, str]] = {
    "standard_416": STANDARD_416,
    "lahaina_464": LAHAINA_464,
    "kalama_480": KALAMA_480,
    "pineapple_480": PINEAPPLE_480,
    "sun_480": SUN_480,
    "cliffs_minimal": CLIFFS_MINIMAL,
    "alor_480": ALOR_480,
}

#: 预设名的中文说明
PRESET_LABELS: dict[str, str] = {
    "standard_416": "416 档标准预设（865/855/765G/690/778G/8 Gen 1 等）",
    "lahaina_464": "464 档预设（骁龙 888 多 bin）",
    "kalama_480": "480 档预设（8 Gen 2 / 7+ Gen 3 / Tuna）",
    "pineapple_480": "480 档预设（8 Gen 3，MIN_SVS 位置不同）",
    "sun_480": "480 档细分预设（8 Elite / 8 Elite Gen 5）",
    "cliffs_minimal": "极简预设（8s Gen 3，仅 RETENTION + NOM）",
    "alor_480": "480 档预设（8 Elite Gen 5 Alor）",
}


def resolve_preset(name: str) -> dict[int, str]:
    """预设名 → 档位表，未知名称返回空表。"""
    return PRESETS.get(name, {})


def level_label(levels: dict[int, str], value: int) -> Optional[str]:
    """把 ``qcom,level`` 的数值转换为标签。

    注意：DTS 中的数值 = 档位索引 + 1。
    """
    if value is None or value < 0:
        return None
    return levels.get(value - 1)


# ---------------------------------------------------------------------------
# 预设推断
# ---------------------------------------------------------------------------

_MODEL_PATTERNS: list[tuple[re.Pattern, str]] = [
    # Alor 布局独特，必须先于 Sun/Canoe 判断
    (re.compile(r"\bAlor\b", re.IGNORECASE), "alor_480"),
    (re.compile(r"\bCliffs\s+SoC\b", re.IGNORECASE), "cliffs_minimal"),
    (re.compile(r"\bCliffs\s+7\b", re.IGNORECASE), "kalama_480"),
    (re.compile(r"\b(Sun|Canoe)\b", re.IGNORECASE), "sun_480"),
    (re.compile(r"\bPineapple\b", re.IGNORECASE), "pineapple_480"),
    (re.compile(r"\b(Kalama|Tuna)\b", re.IGNORECASE), "kalama_480"),
    (re.compile(r"\bLahaina\b", re.IGNORECASE), "lahaina_464"),
    (re.compile(
        r"\b(kona|msmnile|Lito|Lagoon|Shima|Yupik|Waipio|Cape|Diwali|Ukee|Montague|Parrot|Ravelin)\b",
        re.IGNORECASE), "standard_416"),
]


def infer_preset(detected_model: Optional[str], level_count: int = 480) -> str:
    """推断应使用的电压档位预设。"""
    if detected_model:
        for pattern, preset in _MODEL_PATTERNS:
            if pattern.search(detected_model):
                return preset
    if level_count <= 416:
        return "standard_416"
    if level_count <= 464:
        return "lahaina_464"
    return "kalama_480"


# ---------------------------------------------------------------------------
# 芯片代号显示名
# ---------------------------------------------------------------------------

CODENAME_NAMES: dict[str, str] = {
    "msmnile": "骁龙 855 (SM8150)",
    "kona": "骁龙 865 (SM8250)",
    "lahaina": "骁龙 888 (SM8350)",
    "waipio": "骁龙 8 Gen 1 (SM8450)",
    "cape": "骁龙 8+ Gen 1 (SM8475)",
    "kalama": "骁龙 8 Gen 2 (SM8550)",
    "tuna": "骁龙 8 Gen 3 (SM8650 / Tuna)",
    "pineapple": "骁龙 8 Gen 3 (SM8650)",
    "cliffs": "骁龙 8s Gen 3 (SM8635)",
    "sun": "骁龙 8 Elite (SM8750)",
    "canoe": "骁龙 8 Elite Gen 5 (SM8850)",
    "alor": "骁龙 8 Elite Gen 5 变体",
    "lito": "骁龙 765G / 690 系列",
    "lagoon": "骁龙 690 系列",
    "yupik": "骁龙 778G 系列",
    "shima": "骁龙 7 Gen 1",
    "diwali": "骁龙 7 Gen 2",
}

#: SM 平台编号 → 市场名称（型号字符串里只有编号时的补充识别）
SM_NUMBER_NAMES: dict[str, str] = {
    "sm8150": "骁龙 855 / 860 系列 (SM8150)",
    "sm8250": "骁龙 865 系列 (SM8250)",
    "sm8350": "骁龙 888 系列 (SM8350)",
    "sm8450": "骁龙 8 Gen 1 (SM8450)",
    "sm8475": "骁龙 8+ Gen 1 (SM8475)",
    "sm8550": "骁龙 8 Gen 2 (SM8550)",
    "sm8650": "骁龙 8 Gen 3 (SM8650)",
    "sm8750": "骁龙 8 Elite (SM8750)",
    "sm8850": "骁龙 8 Elite Gen 5 (SM8850)",
    "sm7250": "骁龙 765G 系列 (SM7250)",
    "sm6350": "骁龙 690 (SM6350)",
    "sm7325": "骁龙 778G 系列 (SM7325)",
    "sm7350": "骁龙 780G (SM7350)",
    "sm7450": "骁龙 7 Gen 1 (SM7450)",
    "sm7475": "骁龙 7+ Gen 2 (SM7475)",
    "sm7675": "骁龙 7+ Gen 3 (SM7675)",
    "sm8635": "骁龙 8s Gen 3 (SM8635)",
}

_CODENAME_RE = re.compile(
    r"\b(msmnile|kona|lahaina|waipio|cape|kalama|tuna|pineapple|cliffs|sun|canoe|alor|"
    r"lito|lagoon|yupik|shima|diwali)\b", re.IGNORECASE)
_SM_NUMBER_RE = re.compile(r"\b(sm\d{4})\b", re.IGNORECASE)


def chip_display_name(model: str, codename: str = "") -> str:
    """生成界面展示用的芯片名。"""
    for source in (codename, model):
        if not source:
            continue
        match = _CODENAME_RE.search(source)
        if match:
            name = CODENAME_NAMES.get(match.group(1).lower(), "")
            if name:
                return name
        match = _SM_NUMBER_RE.search(source)
        if match:
            name = SM_NUMBER_NAMES.get(match.group(1).lower(), "")
            if name:
                return name
    return codename or model or "未知芯片"



