"""配置加载:config.yaml(可提交)+ 环境变量密钥(绝不提交)。"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent

# Xero custom connection 的 scope。
# 2026-04-29 之前创建的连接用下面这组(SCOPESV1);之后创建的用颗粒化的 SCOPESV2,
# 名称见 https://developer.xero.com/documentation/guides/oauth2/scopes/
# 需要覆盖时设环境变量 XERO_SCOPES(空格分隔)。
DEFAULT_XERO_SCOPES = (
    "accounting.reports.read accounting.transactions.read accounting.settings.read"
)


def die(msg: str) -> None:
    print(f"\n✗ {msg}\n", file=sys.stderr)
    sys.exit(1)


def require_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        die(f"缺少环境变量 {name}(本地写进 .env,线上写进 GitHub Secrets)")
    return value


@dataclass
class Period:
    """报告覆盖的月份,边界按业务所在时区算。"""

    label: str  # "2026-07"
    start_local: datetime
    end_local: datetime  # 开区间,不含
    tz: ZoneInfo

    @property
    def start_date(self) -> date:
        return self.start_local.date()

    @property
    def end_date_inclusive(self) -> date:
        return (self.end_local - timedelta(days=1)).date()

    @property
    def start_epoch(self) -> int:
        return int(self.start_local.timestamp())

    @property
    def end_epoch(self) -> int:
        return int(self.end_local.timestamp())


def resolve_period(month_arg: str | None, tz_name: str) -> Period:
    """默认取"上一个完整月份";传 --month 2026-07 可回溯任意月份。"""
    tz = ZoneInfo(tz_name)
    if month_arg:
        try:
            year, month = (int(x) for x in month_arg.split("-"))
        except ValueError:
            die(f"--month 格式应为 YYYY-MM,收到:{month_arg}")
    else:
        today = datetime.now(tz).date()
        first_of_this_month = today.replace(day=1)
        last_month = first_of_this_month - timedelta(days=1)
        year, month = last_month.year, last_month.month

    start = datetime(year, month, 1, tzinfo=tz)
    end = datetime(year + (month == 12), (month % 12) + 1, 1, tzinfo=tz)
    return Period(label=f"{year:04d}-{month:02d}", start_local=start, end_local=end, tz=tz)


@dataclass
class Config:
    business_name: str
    timezone: str
    home_currency: str
    language: str

    # Xero
    xero_gst_account_code: str
    xero_bank_account_codes: list[str]
    xero_scopes: str

    # Stripe
    mrr_statuses: list[str]
    include_trialing: bool
    fx_to_home: dict[str, Decimal]
    churn_scan_max_pages: int

    # LLM
    llm_model: str
    llm_temperature: float

    # 交付
    email_to: list[str] = field(default_factory=list)
    email_from: str = ""
    sheet_tab: str = "monthly"

    @classmethod
    def load(cls, path: Path) -> "Config":
        if not path.exists():
            die(f"找不到 {path.name}。先复制一份:cp config.example.yaml config.yaml")
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}

        biz = raw.get("business", {})
        xero = raw.get("xero", {})
        stripe_cfg = raw.get("stripe", {})
        llm = raw.get("llm", {})
        deliver = raw.get("deliver", {})

        fx = {
            str(k).lower(): Decimal(str(v))
            for k, v in (stripe_cfg.get("fx_to_home") or {}).items()
        }

        return cls(
            business_name=biz.get("name", "My Business"),
            timezone=biz.get("timezone", "Australia/Melbourne"),
            home_currency=str(biz.get("home_currency", "aud")).lower(),
            language=biz.get("report_language", "zh"),
            xero_gst_account_code=str(xero.get("gst_account_code", "820")),
            xero_bank_account_codes=[str(c) for c in (xero.get("bank_account_codes") or [])],
            xero_scopes=os.environ.get("XERO_SCOPES", "").strip() or DEFAULT_XERO_SCOPES,
            mrr_statuses=[s.lower() for s in (stripe_cfg.get("mrr_statuses") or ["active", "past_due"])],
            include_trialing=bool(stripe_cfg.get("include_trialing", False)),
            fx_to_home=fx,
            churn_scan_max_pages=int(stripe_cfg.get("churn_scan_max_pages", 20)),
            llm_model=llm.get("model", "gpt-4.1"),
            llm_temperature=float(llm.get("temperature", 0.3)),
            email_to=[e for e in (deliver.get("email_to") or []) if e],
            email_from=deliver.get("email_from", ""),
            sheet_tab=deliver.get("sheet_tab", "monthly"),
        )
