"""Stripe → MRR / churn。

Stripe 不给你 MRR,得自己算。这个文件是整个项目里最容易算错的地方,
所以计算逻辑全部放在纯函数 normalize_to_monthly / subscription_mrr 里,
可以脱网单测(见 tests/test_mrr.py)。上线前务必拿三个月历史数据
对一遍 Stripe Dashboard,对得上再往下走。
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Iterable

import stripe

from . import money

# 一个计费周期折算成几个月
MONTHS_PER_INTERVAL = {
    "month": Decimal(1),
    "year": Decimal(12),
    "week": Decimal(12) / Decimal(52),
    "day": Decimal(12) / Decimal(365),
}


def normalize_to_monthly(
    unit_amount: int, quantity: int, interval: str, interval_count: int
) -> Decimal:
    """把任意计费周期的金额折算成月度金额(最小货币单位,比如分)。

    季付 300 → 每月 100;年付 1200 → 每月 100;周付 25 → 每月约 108.33。
    """
    months = MONTHS_PER_INTERVAL.get(interval)
    if months is None:
        raise ValueError(f"未知的计费周期:{interval}")
    return Decimal(unit_amount) * Decimal(quantity) / (Decimal(interval_count) * months)


def apply_discount(monthly: Decimal, discount: dict | None) -> Decimal:
    """折扣是近似处理:percent_off 按比例扣,amount_off 直接减。

    amount_off 严格来说是"每张发票减固定金额",跨周期折算并不精确;
    对绝大多数订阅业务够用,但如果你大量使用固定金额券,这里要改。
    """
    if not discount:
        return monthly
    coupon = discount.get("coupon") or {}
    if coupon.get("percent_off"):
        monthly *= (Decimal(100) - Decimal(str(coupon["percent_off"]))) / Decimal(100)
    if coupon.get("amount_off"):
        monthly -= Decimal(coupon["amount_off"])
    return max(monthly, Decimal(0))


def subscription_mrr(sub: dict) -> tuple[Decimal, list[str]]:
    """返回 (月度金额, 警告列表)。金额单位是最小货币单位。"""
    total = Decimal(0)
    warnings: list[str] = []
    for item in (sub.get("items", {}) or {}).get("data", []):
        price = item.get("price") or {}
        recurring = price.get("recurring") or {}
        if not recurring:
            continue
        if price.get("unit_amount") is None:
            # 阶梯计价(tiered)没有单价,算不了。不静默跳过 —— 报出来。
            warnings.append(
                f"订阅 {sub.get('id')} 的价格 {price.get('id')} 是阶梯计价,已跳过,MRR 会偏低"
            )
            continue
        total += normalize_to_monthly(
            unit_amount=price["unit_amount"],
            quantity=item.get("quantity") or 1,
            interval=recurring.get("interval", "month"),
            interval_count=recurring.get("interval_count") or 1,
        )
    return apply_discount(total, sub.get("discount")), warnings


def _sum_by_currency(subs: Iterable[dict]) -> tuple[dict[str, Decimal], list[str]]:
    totals: dict[str, Decimal] = {}
    warnings: list[str] = []
    for sub in subs:
        amount, warns = subscription_mrr(sub)
        warnings.extend(warns)
        currency = (sub.get("currency") or "").lower()
        totals[currency] = totals.get(currency, Decimal(0)) + amount
    return totals, warnings


def _to_home(
    by_currency: dict[str, Decimal], home: str, fx: dict[str, Decimal]
) -> tuple[Decimal, list[str]]:
    total = Decimal(0)
    warnings: list[str] = []
    for currency, amount in by_currency.items():
        if currency == home:
            total += amount
        elif currency in fx:
            total += amount * fx[currency]
        else:
            warnings.append(
                f"币种 {currency.upper()} 没有汇率,未计入本位币合计。"
                f"在 config.yaml 的 stripe.fx_to_home 里补上 {currency}: <汇率>"
            )
    return total, warnings


def collect(api_key: str, period, cfg) -> dict:
    stripe.api_key = api_key
    warnings: list[str] = []

    # --- 当前 MRR ---
    statuses = list(cfg.mrr_statuses)
    if cfg.include_trialing and "trialing" not in statuses:
        statuses.append("trialing")

    live_subs: list[dict] = []
    for status in statuses:
        live_subs.extend(
            stripe.Subscription.list(
                status=status, limit=100, expand=["data.discount"]
            ).auto_paging_iter()
        )

    mrr_by_currency, warns = _sum_by_currency(live_subs)
    warnings.extend(warns)
    mrr_home, warns = _to_home(mrr_by_currency, cfg.home_currency, cfg.fx_to_home)
    warnings.extend(warns)

    # --- 本期新增(created 可以在服务端过滤,干净)---
    new_subs = list(
        stripe.Subscription.list(
            status="all",
            created={"gte": period.start_epoch, "lt": period.end_epoch},
            limit=100,
            expand=["data.discount"],
        ).auto_paging_iter()
    )
    new_by_currency, warns = _sum_by_currency(new_subs)
    warnings.extend(warns)
    new_mrr_home, _ = _to_home(new_by_currency, cfg.home_currency, cfg.fx_to_home)

    # --- 本期流失 ---
    # Stripe 不支持按 canceled_at 服务端过滤,只能拉已取消的订阅在本地筛。
    # 订阅量大的账号这里会慢,churn_scan_max_pages 是刹车。
    churned: list[dict] = []
    scanned = pages = 0
    for sub in stripe.Subscription.list(
        status="canceled", limit=100, expand=["data.discount"]
    ).auto_paging_iter():
        scanned += 1
        if scanned % 100 == 0:
            pages += 1
            if pages >= cfg.churn_scan_max_pages:
                warnings.append(
                    f"已取消订阅扫描到 {scanned} 条就停了(churn_scan_max_pages={cfg.churn_scan_max_pages})。"
                    "流失数字可能不完整 —— 调高这个上限,或改用 Sheet 里的月度差值。"
                )
                break
        canceled_at = sub.get("canceled_at")
        if canceled_at and period.start_epoch <= canceled_at < period.end_epoch:
            churned.append(sub)

    churned_by_currency, warns = _sum_by_currency(churned)
    warnings.extend(warns)
    churned_mrr_home, _ = _to_home(churned_by_currency, cfg.home_currency, cfg.fx_to_home)

    active_count = len([s for s in live_subs if s.get("status") in ("active", "past_due")])
    base_count = active_count + len(churned)
    logo_churn = (Decimal(len(churned)) / Decimal(base_count) * 100) if base_count else Decimal(0)

    # --- 本期没收回来的钱 ---
    outstanding = Decimal(0)
    outstanding_count = 0
    for inv in stripe.Invoice.list(
        created={"gte": period.start_epoch, "lt": period.end_epoch}, limit=100
    ).auto_paging_iter():
        if inv.get("status") in ("open", "uncollectible") and (inv.get("amount_due") or 0) > 0:
            outstanding += Decimal(inv["amount_due"])
            outstanding_count += 1

    return {
        "mrr": {
            "home_currency": cfg.home_currency.upper(),
            "total": money.from_minor(mrr_home),
            "by_currency": {
                c.upper(): money.from_minor(v) for c, v in mrr_by_currency.items()
            },
            "as_of": "运行时刻的存量订阅",
        },
        "growth": {
            "new_subscriptions": len(new_subs),
            "new_mrr": money.from_minor(new_mrr_home),
            "churned_subscriptions": len(churned),
            "churned_mrr": money.from_minor(churned_mrr_home),
            "net_new_mrr": money.from_minor(new_mrr_home - churned_mrr_home),
            "logo_churn_pct": f"{logo_churn:.2f}",
        },
        "collections": {
            "outstanding_invoices": outstanding_count,
            "outstanding_amount": money.from_minor(outstanding),
        },
        "counts": {"active_subscriptions": active_count},
        "warnings": warnings,
    }
