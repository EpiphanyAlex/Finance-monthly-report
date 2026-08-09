"""组装 report.json,并渲染成 Markdown。

report.json 是唯一真相。Markdown 正文里模型写的部分放前面,
原始数字表格放后面 —— 你每个月扫一眼表格就能发现模型有没有胡说。
"""

from __future__ import annotations

from datetime import datetime, timezone


def build(cfg, period, stripe_data: dict, xero_data: dict) -> dict:
    return {
        "meta": {
            "business": cfg.business_name,
            "period": period.label,
            "period_start": period.start_date.isoformat(),
            "period_end": period.end_date_inclusive.isoformat(),
            "timezone": cfg.timezone,
            "home_currency": cfg.home_currency.upper(),
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        },
        "stripe": stripe_data,
        "xero": xero_data,
        "warnings": stripe_data.get("warnings", []),
    }


def _row(label: str, value) -> str:
    return f"| {label} | {value if value not in (None, '') else '—'} |"


def to_markdown(report: dict, narrative: str | None) -> str:
    meta, stripe_d, xero_d = report["meta"], report["stripe"], report["xero"]
    cur = meta["home_currency"]

    lines = [
        f"# {meta['business']} 财务月报 · {meta['period']}",
        "",
        f"*{meta['period_start']} 至 {meta['period_end']}({meta['timezone']})*",
        "",
    ]

    if narrative:
        lines += [narrative, "", "---", ""]

    lines += [
        "## 原始数字",
        "",
        "> 以上正文由模型撰写,模型不参与计算。下表为脚本直接从 Stripe / Xero 取得的终值,以此为准。",
        "",
        "| 指标 | 数值 |",
        "| --- | --- |",
        _row(f"MRR ({cur})", stripe_d["mrr"]["total"]),
        _row("活跃订阅数", stripe_d["counts"]["active_subscriptions"]),
        _row("本月新增订阅", stripe_d["growth"]["new_subscriptions"]),
        _row(f"新增 MRR ({cur})", stripe_d["growth"]["new_mrr"]),
        _row("本月流失订阅", stripe_d["growth"]["churned_subscriptions"]),
        _row(f"流失 MRR ({cur})", stripe_d["growth"]["churned_mrr"]),
        _row(f"净新增 MRR ({cur})", stripe_d["growth"]["net_new_mrr"]),
        _row("客户流失率 (%)", stripe_d["growth"]["logo_churn_pct"]),
        _row("未收回开票金额", stripe_d["collections"]["outstanding_amount"]),
        _row("银行结余合计", xero_d["cash"]["closing_total"]),
        _row("本月现金流入", xero_d["cash"]["cash_received"]),
        _row("本月现金流出", xero_d["cash"]["cash_spent"]),
        _row("现金净变动", xero_d["cash"]["net_movement"]),
        _row(f"GST 准备金(应付税局,科目 {xero_d['gst']['account_code']})", xero_d["gst"]["balance_owing"]),
        _row("本月收入 (P&L)", xero_d["pnl"]["income"]),
        _row("本月费用 (P&L)", xero_d["pnl"]["expenses"]),
        _row("本月净利 (P&L)", xero_d["pnl"]["net_profit"]),
        "",
    ]

    if stripe_d["mrr"]["by_currency"]:
        lines += ["**MRR 按币种拆分**", ""]
        lines += [f"- {c}: {v}" for c, v in stripe_d["mrr"]["by_currency"].items()]
        lines += [""]

    if report["warnings"]:
        lines += ["## ⚠ 数据质量提醒", ""]
        lines += [f"- {w}" for w in report["warnings"]]
        lines += [""]

    lines += [f"*生成于 {meta['generated_at']} UTC*"]
    return "\n".join(lines)


SHEET_HEADER = [
    "period", "generated_at", "mrr", "active_subs", "new_subs", "new_mrr",
    "churned_subs", "churned_mrr", "net_new_mrr", "logo_churn_pct",
    "outstanding", "cash_closing", "cash_in", "cash_out", "gst_owing",
    "pnl_income", "pnl_expenses", "pnl_net_profit",
]


def to_sheet_row(report: dict) -> list:
    """一行一个月,存进 Google Sheet。12 个月后这张表就是你的指标时间序列。"""
    s, x = report["stripe"], report["xero"]
    return [
        report["meta"]["period"],
        report["meta"]["generated_at"],
        s["mrr"]["total"],
        s["counts"]["active_subscriptions"],
        s["growth"]["new_subscriptions"],
        s["growth"]["new_mrr"],
        s["growth"]["churned_subscriptions"],
        s["growth"]["churned_mrr"],
        s["growth"]["net_new_mrr"],
        s["growth"]["logo_churn_pct"],
        s["collections"]["outstanding_amount"],
        x["cash"]["closing_total"],
        x["cash"]["cash_received"],
        x["cash"]["cash_spent"],
        x["gst"]["balance_owing"] or "",
        x["pnl"]["income"],
        x["pnl"]["expenses"],
        x["pnl"]["net_profit"],
    ]
