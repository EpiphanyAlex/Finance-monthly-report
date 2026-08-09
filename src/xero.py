"""Xero 客户端,支持两种认证方式,自动判断用哪种。

【默认·免费】标准 OAuth 2.0 授权码流程
    先本地跑一次 `python authorize.py` 在浏览器里授权,拿到 refresh token。
    之后每次运行用它换 access token。refresh token 每次使用都会轮换,
    脚本会自动把新的存回去(本地文件 / GitHub Secret)。
    未使用满 60 天才会失效 —— 配合每周的 keepalive workflow,不会断。

【可选·付费】Custom Connection(client_credentials)
    设了 XERO_CLIENT_ID/SECRET 但没有 refresh token 时走这条。
    没有 refresh token 需要维护,更省心,但 Xero 按连接收费。
    等业务跑起来了再换过来,现在没必要。
"""

from __future__ import annotations

import base64
from decimal import Decimal, InvalidOperation
from typing import Any, Iterator

import requests

from . import money, token_store
from .config import die

TOKEN_URL = "https://identity.xero.com/connect/token"
CONNECTIONS_URL = "https://api.xero.com/connections"
API_BASE = "https://api.xero.com/api.xro/2.0"
TIMEOUT = 60


def _to_decimal(raw: Any) -> Decimal | None:
    if raw in (None, "", "-"):
        return None
    try:
        return Decimal(str(raw).replace(",", "").replace("$", ""))
    except (InvalidOperation, ValueError):
        return None


def basic_auth_header(client_id: str, client_secret: str) -> str:
    encoded = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    return f"Basic {encoded}"


def refresh_access_token(client_id: str, client_secret: str, refresh_token: str) -> dict:
    """用 refresh token 换新的 access token。Xero 会同时返回一个新的 refresh token。"""
    resp = requests.post(
        TOKEN_URL,
        headers={
            "Authorization": basic_auth_header(client_id, client_secret),
            "Content-Type": "application/x-www-form-urlencoded",
        },
        data={"grant_type": "refresh_token", "refresh_token": refresh_token},
        timeout=TIMEOUT,
    )
    if resp.status_code != 200:
        die(
            "Xero refresh token 换取失败。常见原因:\n"
            "  1. token 已超过 60 天未使用 → 重新跑一次 `python authorize.py`\n"
            "  2. 上次轮换后的新 token 没存回去 → 检查 GH_PAT 是否有 Secrets 写权限\n"
            "  3. XERO_CLIENT_ID / XERO_CLIENT_SECRET 不匹配\n"
            f"\nXero 返回:{resp.status_code} {resp.text[:500]}"
        )
    return resp.json()


class XeroClient:
    def __init__(
        self,
        client_id: str,
        client_secret: str,
        scopes: str,
        refresh_token: str | None = None,
    ):
        self._client_id = client_id
        self._client_secret = client_secret
        self._scopes = scopes
        self._refresh_token = refresh_token
        self._token: str | None = None
        self._tenant_id: str | None = None

    # ---------- 认证 ----------

    def _authenticate(self) -> None:
        if self._refresh_token:
            self._auth_via_refresh_token()
        else:
            self._auth_via_client_credentials()
        self._resolve_tenant()

    def _auth_via_refresh_token(self) -> None:
        payload = refresh_access_token(
            self._client_id, self._client_secret, self._refresh_token or ""
        )
        self._token = payload["access_token"]
        # 关键一步:轮换后的新 token 必须存回去,旧的已经作废了。
        new_refresh = payload.get("refresh_token")
        if new_refresh and new_refresh != self._refresh_token:
            token_store.save(new_refresh)
            self._refresh_token = new_refresh

    def _auth_via_client_credentials(self) -> None:
        resp = requests.post(
            TOKEN_URL,
            headers={
                "Authorization": basic_auth_header(self._client_id, self._client_secret),
                "Content-Type": "application/x-www-form-urlencoded",
            },
            data={"grant_type": "client_credentials", "scope": self._scopes},
            timeout=TIMEOUT,
        )
        if resp.status_code != 200:
            die(
                "Xero 认证失败。你现在走的是 Custom Connection(client_credentials)模式,\n"
                "因为没有找到 refresh token。\n\n"
                "如果你想用免费的标准 OAuth:先在本地跑一次 `python authorize.py`。\n"
                "如果你确实买了 Custom Connection:检查 CLIENT_ID/SECRET,以及 scope ——\n"
                "2026-04-29 之后创建的连接要用 SCOPESV2,设 XERO_SCOPES 覆盖。\n"
                f"\nXero 返回:{resp.status_code} {resp.text[:500]}"
            )
        self._token = resp.json()["access_token"]

    def _resolve_tenant(self) -> None:
        import os

        override = os.environ.get("XERO_TENANT_ID", "").strip()
        if override:
            self._tenant_id = override
            return

        conns = requests.get(
            CONNECTIONS_URL,
            headers={"Authorization": f"Bearer {self._token}", "Accept": "application/json"},
            timeout=TIMEOUT,
        )
        conns.raise_for_status()
        payload = conns.json()
        if not payload:
            die("Xero token 拿到了,但没有关联任何组织。去 Xero 后台确认这个 app 已经连上了你的组织。")
        if len(payload) > 1:
            names = ", ".join(f"{c.get('tenantName')} ({c.get('tenantId')})" for c in payload)
            print(f"  ℹ 该授权关联了多个组织,默认用第一个。要指定请设 XERO_TENANT_ID。\n    {names}")
        self._tenant_id = payload[0]["tenantId"]
        print(f"  ℹ Xero 组织:{payload[0].get('tenantName')}")

    def _headers(self) -> dict[str, str]:
        if not self._token:
            self._authenticate()
        return {
            "Authorization": f"Bearer {self._token}",
            "Xero-tenant-id": self._tenant_id or "",
            "Accept": "application/json",
        }

    # ---------- 原始调用 ----------

    def get(self, path: str, params: dict[str, Any] | None = None) -> dict:
        resp = requests.get(
            f"{API_BASE}/{path}", headers=self._headers(), params=params or {}, timeout=TIMEOUT
        )
        if resp.status_code == 403:
            die(
                f"Xero 拒绝访问 {path}(403)。多半是 scope 不够。\n"
                f"当前 scope:{self._scopes}\n"
                f"返回:{resp.text[:300]}"
            )
        resp.raise_for_status()
        return resp.json()

    def report(self, name: str, **params: Any) -> dict:
        payload = self.get(f"Reports/{name}", params)
        reports = payload.get("Reports") or []
        if not reports:
            die(f"Xero 报表 {name} 返回为空。")
        return reports[0]

    def accounts(self) -> list[dict]:
        return self.get("Accounts").get("Accounts", [])


# ---------- 报表解析 ----------
#
# Xero 报表是嵌套的 Rows → Section → Rows → Cells,直接遍历很痛苦。
# 下面两个函数把它拍平成 (section_title, cells) 的序列。

def iter_rows(report: dict) -> Iterator[tuple[str, list[dict]]]:
    for block in report.get("Rows", []):
        if block.get("RowType") == "Section":
            title = block.get("Title", "")
            for row in block.get("Rows", []):
                if row.get("RowType") in ("Row", "SummaryRow"):
                    yield title, row.get("Cells", [])
        elif block.get("RowType") in ("Row", "SummaryRow"):
            yield "", block.get("Cells", [])


def cell_account_id(cell: dict) -> str | None:
    for attr in cell.get("Attributes") or []:
        if attr.get("Id") == "account":
            return attr.get("Value")
    return None


def find_row_by_account_id(report: dict, account_id: str) -> list[dict] | None:
    for _, cells in iter_rows(report):
        if cells and cell_account_id(cells[0]) == account_id:
            return cells
    return None


def summary_rows(report: dict) -> dict[str, Decimal]:
    """抓出所有 SummaryRow,形成 {标签: 数值}。用于 P&L 这类标签因地区而异的报表。"""
    out: dict[str, Decimal] = {}
    for block in report.get("Rows", []):
        rows = block.get("Rows", []) if block.get("RowType") == "Section" else [block]
        for row in rows:
            if row.get("RowType") != "SummaryRow":
                continue
            cells = row.get("Cells", [])
            if len(cells) < 2:
                continue
            label = (cells[0].get("Value") or "").strip()
            value = _to_decimal(cells[1].get("Value"))
            if label and value is not None:
                out[label] = value
    return out


def pick(labels: dict[str, Decimal], *patterns: str) -> Decimal | None:
    """按关键词从 summary_rows 里挑一个值,大小写不敏感,取第一个命中。"""
    for pattern in patterns:
        for label, value in labels.items():
            if pattern.lower() in label.lower():
                return value
    return None


# ---------- 业务指标 ----------

def collect(client: XeroClient, period, gst_account_code: str, bank_codes: list[str]) -> dict:
    start = period.start_date.isoformat()
    end = period.end_date_inclusive.isoformat()

    accounts = client.accounts()
    by_code = {str(a.get("Code")): a for a in accounts if a.get("Code")}

    # --- GST 准备金 ---
    # Xero 的 Accounting API 没有 Activity Statement / BAS 端点(GST Report 只有在界面里
    # 手动 publish 之后才能通过 Reports/{ReportID} 取到,没法自动化)。
    # 所以直接读 GST 负债科目的余额 —— 这个数就是你欠税局的钱。
    gst_account = by_code.get(gst_account_code)
    if not gst_account:
        available = ", ".join(
            f"{c}={a.get('Name')}"
            for c, a in sorted(by_code.items())
            if "gst" in str(a.get("Name", "")).lower() or a.get("Class") == "LIABILITY"
        )
        die(
            f"config.yaml 里的 xero.gst_account_code = {gst_account_code} 在你的科目表里不存在。\n"
            f"你账上可能的候选科目:\n  {available or '(没找到负债类科目,检查连接的是不是正确的组织)'}"
        )

    trial = client.report("TrialBalance", date=end)
    gst_cells = find_row_by_account_id(trial, gst_account["AccountID"])
    gst_balance = None
    if gst_cells and len(gst_cells) >= 3:
        debit = _to_decimal(gst_cells[1].get("Value")) or Decimal(0)
        credit = _to_decimal(gst_cells[2].get("Value")) or Decimal(0)
        gst_balance = credit - debit  # 正数 = 欠税局

    # --- 现金 ---
    bank = client.report("BankSummary", fromDate=start, toDate=end)
    wanted_ids = {
        by_code[c]["AccountID"] for c in bank_codes if c in by_code
    } if bank_codes else None

    bank_accounts, closing_total, received_total, spent_total = [], Decimal(0), Decimal(0), Decimal(0)
    for _, cells in iter_rows(bank):
        if len(cells) < 5:
            continue
        acct_id = cell_account_id(cells[0])
        if not acct_id or (wanted_ids is not None and acct_id not in wanted_ids):
            continue
        opening = _to_decimal(cells[1].get("Value")) or Decimal(0)
        received = _to_decimal(cells[2].get("Value")) or Decimal(0)
        spent = _to_decimal(cells[3].get("Value")) or Decimal(0)
        closing = _to_decimal(cells[4].get("Value")) or Decimal(0)
        bank_accounts.append(
            {
                "name": cells[0].get("Value"),
                "opening": money.fmt(opening),
                "cash_received": money.fmt(received),
                "cash_spent": money.fmt(spent),
                "closing": money.fmt(closing),
            }
        )
        closing_total += closing
        received_total += received
        spent_total += spent

    # --- 损益 ---
    # P&L 的行标签因地区和科目表配置而异,所以整块原样收下来,再尽力猜三个关键值。
    # 猜不到就是 None —— 这一项不如 GST 关键,不值得让整个任务失败。
    pnl = client.report("ProfitAndLoss", fromDate=start, toDate=end)
    pnl_labels = summary_rows(pnl)

    return {
        "gst": {
            "account_code": gst_account_code,
            "account_name": gst_account.get("Name"),
            "balance_owing": money.fmt(gst_balance) if gst_balance is not None else None,
            "as_at": end,
            "note": "正数 = 应付税局。来源:Trial Balance 上该科目的 credit - debit。",
        },
        "cash": {
            "closing_total": money.fmt(closing_total),
            "cash_received": money.fmt(received_total),
            "cash_spent": money.fmt(spent_total),
            "net_movement": money.fmt(received_total - spent_total),
            "accounts": bank_accounts,
        },
        "pnl": {
            "income": money.fmt(pick(pnl_labels, "total income", "total revenue", "total trading income")),
            "expenses": money.fmt(pick(pnl_labels, "total operating expenses", "total expenses")),
            "net_profit": money.fmt(pick(pnl_labels, "net profit", "profit for the period", "net income")),
            "all_summary_rows": {k: money.fmt(v) for k, v in pnl_labels.items()},
        },
    }
