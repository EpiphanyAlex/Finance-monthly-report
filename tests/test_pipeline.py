"""全链路离线测试:Stripe + Xero → report.json → Markdown。

不联网、不调模型。跑通这个说明除了真实 API 凭证之外,所有代码路径都是通的。
"""

from decimal import Decimal

import pytest

from src import report as report_mod, stripe_metrics, xero
from src.config import Config, resolve_period
from tests import fixtures as fx
from tests.test_xero_parsing import FakeXeroClient

JULY_START = 1782914400  # 2026-07-01 00:00 墨尔本
MID_JULY = 1784000000
JUNE = 1780000000


class FakeList:
    def __init__(self, items):
        self._items = items

    def auto_paging_iter(self):
        return iter(self._items)


class FakeStripe:
    """按 stripe-python 的调用形状造的替身。"""

    api_key = None

    def __init__(self, subs_by_status, created_subs, canceled_subs, invoices):
        self._subs_by_status = subs_by_status
        self._created = created_subs
        self._canceled = canceled_subs
        self._invoices = invoices
        self.Subscription = self._Subscription(self)
        self.Invoice = self._Invoice(self)

    class _Subscription:
        def __init__(self, parent):
            self._p = parent

        def list(self, status=None, created=None, **_kwargs):
            if created is not None:
                return FakeList(self._p._created)
            if status == "canceled":
                return FakeList(self._p._canceled)
            return FakeList(self._p._subs_by_status.get(status, []))

    class _Invoice:
        def __init__(self, parent):
            self._p = parent

        def list(self, **_kwargs):
            return FakeList(self._p._invoices)


@pytest.fixture
def cfg(tmp_path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        """
business:
  name: "Test Co"
  timezone: "Australia/Melbourne"
  home_currency: "aud"
  report_language: "zh"
xero:
  gst_account_code: "820"
  bank_account_codes: []
stripe:
  mrr_statuses: ["active", "past_due"]
  include_trialing: false
  fx_to_home:
    usd: 1.50
llm:
  model: "gpt-4.1"
deliver:
  email_to: []
""",
        encoding="utf-8",
    )
    return Config.load(config_file)


@pytest.fixture
def period():
    return resolve_period("2026-07", "Australia/Melbourne")


@pytest.fixture
def fake_stripe():
    active = [
        fx.make_sub("sub_monthly", 10000),                              # 100.00/月
        fx.make_sub("sub_annual", 120000, interval="year"),             # 100.00/月
        fx.make_sub("sub_quarterly", 30000, interval_count=3),          # 100.00/月
        fx.make_sub("sub_seats", 5000, quantity=4),                     # 200.00/月
        fx.make_sub("sub_usd", 20000, currency="usd"),                  # 200 USD → 300 AUD
    ]
    past_due = [fx.make_sub("sub_late", 10000, status="past_due")]      # 100.00/月
    trialing = [fx.make_sub("sub_trial", 99900, status="trialing")]     # 不该计入
    created = [fx.make_sub("sub_new", 15000, created=MID_JULY)]
    canceled = [
        fx.make_sub("sub_gone", 8000, status="canceled", canceled_at=MID_JULY),
        fx.make_sub("sub_old", 9900, status="canceled", canceled_at=JUNE),  # 上个月的,不算
    ]
    invoices = [
        {"status": "open", "amount_due": 44000},
        {"status": "paid", "amount_due": 0},
        {"status": "uncollectible", "amount_due": 12000},
    ]
    return FakeStripe(
        {"active": active, "past_due": past_due, "trialing": trialing},
        created, canceled, invoices,
    )


class TestStripeCollect:
    def test_mrr_normalises_all_intervals(self, monkeypatch, cfg, period, fake_stripe):
        monkeypatch.setattr(stripe_metrics, "stripe", fake_stripe)
        data = stripe_metrics.collect("sk_test", period, cfg)
        # AUD: 100 + 100 + 100 + 200 + 100(past_due) = 600
        assert data["mrr"]["by_currency"]["AUD"] == "600.00"
        # USD 200 × 1.50 = 300 → 合计 900
        assert data["mrr"]["total"] == "900.00"

    def test_trialing_excluded_by_default(self, monkeypatch, cfg, period, fake_stripe):
        monkeypatch.setattr(stripe_metrics, "stripe", fake_stripe)
        data = stripe_metrics.collect("sk_test", period, cfg)
        assert "999" not in data["mrr"]["total"]  # 试用订阅是 999.00,不该混进来

    def test_churn_only_counts_this_period(self, monkeypatch, cfg, period, fake_stripe):
        monkeypatch.setattr(stripe_metrics, "stripe", fake_stripe)
        data = stripe_metrics.collect("sk_test", period, cfg)
        assert data["growth"]["churned_subscriptions"] == 1   # 六月那个不算
        assert data["growth"]["churned_mrr"] == "80.00"

    def test_net_new_mrr(self, monkeypatch, cfg, period, fake_stripe):
        monkeypatch.setattr(stripe_metrics, "stripe", fake_stripe)
        data = stripe_metrics.collect("sk_test", period, cfg)
        assert data["growth"]["new_mrr"] == "150.00"
        assert data["growth"]["net_new_mrr"] == "70.00"       # 150 - 80

    def test_outstanding_invoices(self, monkeypatch, cfg, period, fake_stripe):
        monkeypatch.setattr(stripe_metrics, "stripe", fake_stripe)
        data = stripe_metrics.collect("sk_test", period, cfg)
        assert data["collections"]["outstanding_invoices"] == 2
        assert data["collections"]["outstanding_amount"] == "560.00"

    def test_missing_fx_rate_warns(self, monkeypatch, cfg, period, fake_stripe):
        cfg.fx_to_home = {}  # 拿掉 USD 汇率
        monkeypatch.setattr(stripe_metrics, "stripe", fake_stripe)
        data = stripe_metrics.collect("sk_test", period, cfg)
        assert data["mrr"]["total"] == "600.00"               # USD 没被静默算进去
        assert any("USD" in w for w in data["warnings"])


class TestFullPipeline:
    def test_end_to_end_to_markdown(self, monkeypatch, cfg, period, fake_stripe):
        monkeypatch.setattr(stripe_metrics, "stripe", fake_stripe)
        stripe_data = stripe_metrics.collect("sk_test", period, cfg)
        xero_data = xero.collect(FakeXeroClient(), period, "820", [])
        report = report_mod.build(cfg, period, stripe_data, xero_data)

        assert report["meta"]["period"] == "2026-07"
        assert report["xero"]["gst"]["balance_owing"] == "4312.55"

        markdown = report_mod.to_markdown(report, "测试正文。")
        assert "Test Co 财务月报 · 2026-07" in markdown
        assert "| MRR (AUD) | 900.00 |" in markdown
        assert "| GST 准备金(应付税局,科目 820) | 4312.55 |" in markdown
        assert "测试正文。" in markdown

        row = report_mod.to_sheet_row(report)
        assert len(row) == len(report_mod.SHEET_HEADER)
        assert row[0] == "2026-07"
