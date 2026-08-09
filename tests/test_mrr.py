"""MRR 计算的单测 —— 纯函数,不联网。

这是整个项目里最值得测的地方:算错了你不会收到报错,
只会每个月安安静静收到一个错的数字。
    pytest -q
"""

from decimal import Decimal

import pytest

from src.stripe_metrics import apply_discount, normalize_to_monthly, subscription_mrr


def sub(items, currency="aud", discount=None, sub_id="sub_test"):
    """造一个最小的 Stripe 订阅对象。金额单位是分。"""
    return {
        "id": sub_id,
        "currency": currency,
        "discount": discount,
        "items": {
            "data": [
                {
                    "quantity": qty,
                    "price": {
                        "id": f"price_{i}",
                        "unit_amount": amount,
                        "recurring": {"interval": interval, "interval_count": count},
                    },
                }
                for i, (amount, qty, interval, count) in enumerate(items)
            ]
        },
    }


class TestNormalize:
    def test_monthly_passthrough(self):
        assert normalize_to_monthly(10000, 1, "month", 1) == Decimal(10000)

    def test_annual_divides_by_twelve(self):
        assert normalize_to_monthly(120000, 1, "year", 1) == Decimal(10000)

    def test_quarterly_divides_by_three(self):
        assert normalize_to_monthly(30000, 1, "month", 3) == Decimal(10000)

    def test_quantity_multiplies(self):
        assert normalize_to_monthly(5000, 4, "month", 1) == Decimal(20000)

    def test_weekly(self):
        # 每周 25.00 → 每月 25 * 52 / 12 ≈ 108.33
        assert normalize_to_monthly(2500, 1, "week", 1) == pytest.approx(
            Decimal(2500) * 52 / 12, rel=Decimal("0.0001")
        )

    def test_unknown_interval_raises(self):
        with pytest.raises(ValueError):
            normalize_to_monthly(1000, 1, "fortnight", 1)


class TestDiscount:
    def test_no_discount(self):
        assert apply_discount(Decimal(10000), None) == Decimal(10000)

    def test_percent_off(self):
        assert apply_discount(Decimal(10000), {"coupon": {"percent_off": 20}}) == Decimal(8000)

    def test_amount_off(self):
        assert apply_discount(Decimal(10000), {"coupon": {"amount_off": 1500}}) == Decimal(8500)

    def test_never_negative(self):
        assert apply_discount(Decimal(1000), {"coupon": {"amount_off": 9999}}) == Decimal(0)


class TestSubscriptionMrr:
    def test_multi_item_sums(self):
        amount, warnings = subscription_mrr(
            sub([(10000, 1, "month", 1), (120000, 1, "year", 1)])
        )
        assert amount == Decimal(20000)
        assert warnings == []

    def test_discount_applies_to_total(self):
        amount, _ = subscription_mrr(
            sub([(10000, 2, "month", 1)], discount={"coupon": {"percent_off": 50}})
        )
        assert amount == Decimal(10000)

    def test_tiered_price_warns_not_silently_skipped(self):
        tiered = sub([(10000, 1, "month", 1)])
        tiered["items"]["data"].append(
            {
                "quantity": 1,
                "price": {
                    "id": "price_tiered",
                    "unit_amount": None,
                    "recurring": {"interval": "month", "interval_count": 1},
                },
            }
        )
        amount, warnings = subscription_mrr(tiered)
        assert amount == Decimal(10000)
        assert len(warnings) == 1
        assert "阶梯计价" in warnings[0]

    def test_one_off_item_ignored(self):
        s = sub([(10000, 1, "month", 1)])
        s["items"]["data"].append(
            {"quantity": 1, "price": {"id": "price_oneoff", "unit_amount": 50000, "recurring": None}}
        )
        amount, _ = subscription_mrr(s)
        assert amount == Decimal(10000)


class TestMoneyFormatting:
    """周付订阅折算成月是 ×52/12,会产生无限循环小数。
    展示前必须量化到两位,否则报表里会出现 108.3333333333333333333333333。"""

    def test_weekly_produces_repeating_decimal_before_quantize(self):
        raw = normalize_to_monthly(2500, 1, "week", 1)
        assert len(str(raw)) > 10  # 确实是长小数

    def test_from_minor_always_two_places(self):
        from src.money import from_minor

        assert from_minor(normalize_to_monthly(2500, 1, "week", 1)) == "108.33"
        assert from_minor(Decimal(60000)) == "600.00"
        assert from_minor(Decimal(0)) == "0.00"

    def test_rounds_half_up(self):
        from src.money import from_minor

        assert from_minor(Decimal("1005")) == "10.05"
        assert from_minor(Decimal("1000.5")) == "10.01"   # 10.005 → 10.01,不是银行家舍入

    def test_fmt_handles_none(self):
        from src.money import fmt

        assert fmt(None) == ""
        assert fmt(Decimal("50000")) == "50000.00"
