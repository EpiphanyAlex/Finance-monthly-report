"""Xero 报表解析测试。

Xero 报表结构最容易出的三种错:
  1. 把 SummaryRow 当成数据行 → 现金余额翻倍
  2. 按科目名字匹配而不是 AccountID → 改个科目名就崩
  3. 数字带千分位逗号 / 美元符号 → Decimal 解析失败
下面每种都有对应用例。
"""

from decimal import Decimal

import pytest

from src import xero
from tests import fixtures as fx


class FakeXeroClient:
    """不联网,按 path 返回 fixture。"""

    def __init__(self):
        self.calls = []

    def get(self, path, params=None):
        self.calls.append((path, params or {}))
        if path == "Accounts":
            return fx.ACCOUNTS
        raise AssertionError(f"未预期的调用:{path}")

    def accounts(self):
        return self.get("Accounts")["Accounts"]

    def report(self, name, **params):
        self.calls.append((f"Reports/{name}", params))
        return {
            "TrialBalance": fx.TRIAL_BALANCE,
            "BankSummary": fx.BANK_SUMMARY,
            "ProfitAndLoss": fx.PROFIT_AND_LOSS,
        }[name]["Reports"][0]


@pytest.fixture
def period():
    from src.config import resolve_period
    return resolve_period("2026-07", "Australia/Melbourne")


class TestRowParsing:
    def test_iter_rows_skips_header(self):
        report = fx.TRIAL_BALANCE["Reports"][0]
        labels = [cells[0]["Value"] for _, cells in xero.iter_rows(report)]
        assert "Account" not in labels  # Header 行不该出现
        assert "Sales (200)" in labels

    def test_find_row_by_account_id(self):
        report = fx.TRIAL_BALANCE["Reports"][0]
        cells = xero.find_row_by_account_id(report, fx.GST_ACCOUNT_ID)
        assert cells is not None
        assert cells[0]["Value"] == "GST (820)"

    def test_find_row_missing_returns_none(self):
        report = fx.TRIAL_BALANCE["Reports"][0]
        assert xero.find_row_by_account_id(report, "no-such-id") is None

    def test_summary_rows_handles_thousands_separator(self):
        report = fx.PROFIT_AND_LOSS["Reports"][0]
        rows = xero.summary_rows(report)
        assert rows["Total Income"] == Decimal("25100.00")
        assert rows["Net Profit"] == Decimal("5120.00")

    def test_pick_is_case_insensitive_and_ordered(self):
        rows = xero.summary_rows(fx.PROFIT_AND_LOSS["Reports"][0])
        assert xero.pick(rows, "total income") == Decimal("25100.00")
        assert xero.pick(rows, "nope", "net profit") == Decimal("5120.00")
        assert xero.pick(rows, "does not exist") is None


class TestCollect:
    def test_gst_is_credit_minus_debit(self, period):
        data = xero.collect(FakeXeroClient(), period, "820", [])
        assert data["gst"]["balance_owing"] == "4312.55"
        assert data["gst"]["account_name"] == "GST"

    def test_cash_excludes_summary_row(self, period):
        """合计行没有 account 属性,不能被算进去 —— 否则现金翻倍。"""
        data = xero.collect(FakeXeroClient(), period, "820", [])
        assert data["cash"]["closing_total"] == "142140.10"  # 92140.10 + 50000.00
        assert len(data["cash"]["accounts"]) == 2

    def test_bank_account_filter(self, period):
        data = xero.collect(FakeXeroClient(), period, "820", ["090"])
        assert data["cash"]["closing_total"] == "92140.10"
        assert len(data["cash"]["accounts"]) == 1

    def test_pnl_extracted(self, period):
        data = xero.collect(FakeXeroClient(), period, "820", [])
        assert data["pnl"]["income"] == "25100.00"
        assert data["pnl"]["expenses"] == "19980.00"
        assert data["pnl"]["net_profit"] == "5120.00"

    def test_report_dates_use_period_boundaries(self, period):
        client = FakeXeroClient()
        xero.collect(client, period, "820", [])
        calls = dict((p, params) for p, params in client.calls)
        assert calls["Reports/TrialBalance"]["date"] == "2026-07-31"
        assert calls["Reports/ProfitAndLoss"]["fromDate"] == "2026-07-01"
        assert calls["Reports/ProfitAndLoss"]["toDate"] == "2026-07-31"

    def test_bad_gst_code_fails_loudly_with_candidates(self, period, capsys):
        with pytest.raises(SystemExit):
            xero.collect(FakeXeroClient(), period, "999", [])
        err = capsys.readouterr().err
        assert "999" in err
        assert "820=GST" in err  # 报错时列出候选科目
