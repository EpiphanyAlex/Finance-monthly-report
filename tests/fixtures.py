"""按 Xero / Stripe 真实响应结构造的测试数据。

Xero 报表是 Rows → Section → Rows → Cells 的嵌套结构,还混着 Header / SummaryRow,
科目 ID 藏在 Cells[0].Attributes 里 —— 这些坑都在下面还原了。
"""

GST_ACCOUNT_ID = "11111111-1111-1111-1111-111111111111"
BANK_ACCOUNT_ID = "22222222-2222-2222-2222-222222222222"
SAVINGS_ACCOUNT_ID = "33333333-3333-3333-3333-333333333333"
SALES_ACCOUNT_ID = "44444444-4444-4444-4444-444444444444"


def _acct_cell(name, account_id, attr_id="account"):
    """Xero 用两种属性名指同一个东西:TrialBalance 用 account,BankSummary 用 accountID。"""
    return {"Value": name, "Attributes": [{"Value": account_id, "Id": attr_id}]}


ACCOUNTS = {
    "Accounts": [
        {"AccountID": SALES_ACCOUNT_ID, "Code": "200", "Name": "Sales",
         "Type": "REVENUE", "Class": "REVENUE"},
        {"AccountID": BANK_ACCOUNT_ID, "Code": "090", "Name": "Business Bank Account",
         "Type": "BANK", "Class": "ASSET"},
        {"AccountID": SAVINGS_ACCOUNT_ID, "Code": "091", "Name": "Savings Account",
         "Type": "BANK", "Class": "ASSET"},
        {"AccountID": GST_ACCOUNT_ID, "Code": "820", "Name": "GST",
         "Type": "CURRLIAB", "Class": "LIABILITY"},
    ]
}

TRIAL_BALANCE = {
    "Reports": [{
        "ReportID": "TrialBalance",
        "ReportName": "Trial Balance",
        "Rows": [
            {"RowType": "Header", "Cells": [
                {"Value": "Account"}, {"Value": "Debit"}, {"Value": "Credit"},
                {"Value": "YTD Debit"}, {"Value": "YTD Credit"},
            ]},
            {"RowType": "Section", "Title": "Revenue", "Rows": [
                {"RowType": "Row", "Cells": [
                    _acct_cell("Sales (200)", SALES_ACCOUNT_ID),
                    {"Value": ""}, {"Value": "25100.00"},
                    {"Value": ""}, {"Value": "180400.00"},
                ]},
            ]},
            {"RowType": "Section", "Title": "Liabilities", "Rows": [
                # GST 是贷方余额 —— credit 4500.55 减 debit 188.00 = 欠税局 4312.55
                {"RowType": "Row", "Cells": [
                    _acct_cell("GST (820)", GST_ACCOUNT_ID),
                    {"Value": "188.00"}, {"Value": "4500.55"},
                    {"Value": "188.00"}, {"Value": "4500.55"},
                ]},
            ]},
            {"RowType": "Section", "Title": "", "Rows": [
                {"RowType": "SummaryRow", "Cells": [
                    {"Value": "Total"}, {"Value": "25288.00"}, {"Value": "25288.00"},
                    {"Value": ""}, {"Value": ""},
                ]},
            ]},
        ],
    }]
}

BANK_SUMMARY = {
    "Reports": [{
        "ReportID": "BankSummary",
        "Rows": [
            {"RowType": "Header", "Cells": [
                {"Value": "Bank Accounts"}, {"Value": "Opening Balance"},
                {"Value": "Cash Received"}, {"Value": "Cash Spent"},
                {"Value": "Closing Balance"},
            ]},
            # 真实 BankSummary 的科目单元格用 Id="accountID"(不是 "account")
            {"RowType": "Section", "Title": "", "Rows": [
                {"RowType": "Row", "Cells": [
                    _acct_cell("Business Bank Account", BANK_ACCOUNT_ID, "accountID"),
                    {"Value": "86580.50"}, {"Value": "24880.00"},
                    {"Value": "19320.40"}, {"Value": "92140.10"},
                ]},
                {"RowType": "Row", "Cells": [
                    _acct_cell("Savings Account", SAVINGS_ACCOUNT_ID, "accountID"),
                    {"Value": "50000.00"}, {"Value": "0.00"},
                    {"Value": "0.00"}, {"Value": "50000.00"},
                ]},
                # 合计行没有 account 属性,必须被跳过,否则现金会翻倍
                {"RowType": "SummaryRow", "Cells": [
                    {"Value": "Total"}, {"Value": "136580.50"}, {"Value": "24880.00"},
                    {"Value": "19320.40"}, {"Value": "142140.10"},
                ]},
            ]},
        ],
    }]
}

PROFIT_AND_LOSS = {
    "Reports": [{
        "ReportID": "ProfitAndLoss",
        "Rows": [
            {"RowType": "Header", "Cells": [{"Value": ""}, {"Value": "31 Jul 26"}]},
            {"RowType": "Section", "Title": " Income", "Rows": [
                {"RowType": "Row", "Cells": [
                    _acct_cell("Sales", SALES_ACCOUNT_ID), {"Value": "25,100.00"},
                ]},
                {"RowType": "SummaryRow", "Cells": [
                    {"Value": "Total Income"}, {"Value": "25,100.00"},
                ]},
            ]},
            # 真实 P&L 里 GROSS PROFIT / NET PROFIT 是普通 Row,不是 SummaryRow,
            # 而且是全大写。只收 SummaryRow 会拿不到净利。
            {"RowType": "Section", "Title": "", "Rows": [
                {"RowType": "Row", "Cells": [
                    {"Value": "GROSS PROFIT"}, {"Value": "25,100.00"},
                ]},
            ]},
            {"RowType": "Section", "Title": " Less Operating Expenses", "Rows": [
                {"RowType": "Row", "Cells": [
                    {"Value": "Advertising"}, {"Value": "3,200.00"},
                ]},
                {"RowType": "SummaryRow", "Cells": [
                    {"Value": "Total Operating Expenses"}, {"Value": "19,980.00"},
                ]},
            ]},
            {"RowType": "Section", "Title": "", "Rows": [
                {"RowType": "Row", "Cells": [
                    {"Value": "NET PROFIT"}, {"Value": "5,120.00"},
                ]},
            ]},
        ],
    }]
}


# ---------- Stripe ----------

def make_sub(sub_id, amount, interval="month", interval_count=1, quantity=1,
             currency="aud", status="active", canceled_at=None, created=0,
             discount=None):
    return {
        "id": sub_id,
        "status": status,
        "currency": currency,
        "created": created,
        "canceled_at": canceled_at,
        "discount": discount,
        "items": {"data": [{
            "quantity": quantity,
            "price": {
                "id": f"price_{sub_id}",
                "unit_amount": amount,
                "recurring": {"interval": interval, "interval_count": interval_count},
            },
        }]},
    }
