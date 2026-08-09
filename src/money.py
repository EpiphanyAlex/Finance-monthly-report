"""金额格式化。

只在"要展示 / 要写进报表"的最后一步用它。中间计算全程保留完整精度 ——
周付订阅折算成月是 ×52/12,提前四舍五入会让几十个订阅的误差累积起来。
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

CENTS = Decimal("0.01")


def quantize(amount: Decimal) -> Decimal:
    return amount.quantize(CENTS, rounding=ROUND_HALF_UP)


def from_minor(amount: Decimal) -> str:
    """最小货币单位(分)→ 两位小数字符串。"""
    return str(quantize(amount / 100))


def fmt(amount: Decimal | None) -> str:
    """已经是主单位的金额 → 两位小数字符串。"""
    return "" if amount is None else str(quantize(amount))
