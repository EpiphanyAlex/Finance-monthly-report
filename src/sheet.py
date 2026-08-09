"""把每月一行指标追加进 Google Sheet(可选)。

这张 Sheet 不是数据源,是脚本的输出归档。存够 12 个月之后,
你问 AI "MRR 这半年走势" 时它只需要读这一张小表 —— 只读数,不算数。
"""

from __future__ import annotations

import json
import os

import requests
from google.auth.transport.requests import Request
from google.oauth2 import service_account

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
API = "https://sheets.googleapis.com/v4/spreadsheets"


def is_configured() -> bool:
    return bool(
        os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON") and os.environ.get("GOOGLE_SHEET_ID")
    )


def _token() -> str:
    info = json.loads(os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"])
    creds = service_account.Credentials.from_service_account_info(info, scopes=SCOPES)
    creds.refresh(Request())
    return creds.token


def append_row(tab: str, header: list[str], row: list) -> None:
    sheet_id = os.environ["GOOGLE_SHEET_ID"]
    headers = {"Authorization": f"Bearer {_token()}", "Content-Type": "application/json"}

    # 表是空的就先写一行表头
    existing = requests.get(f"{API}/{sheet_id}/values/{tab}!A1:A1", headers=headers, timeout=30)
    if existing.status_code == 200 and not existing.json().get("values"):
        requests.post(
            f"{API}/{sheet_id}/values/{tab}!A1:append",
            headers=headers,
            params={"valueInputOption": "RAW", "insertDataOption": "INSERT_ROWS"},
            json={"values": [header]},
            timeout=30,
        ).raise_for_status()

    resp = requests.post(
        f"{API}/{sheet_id}/values/{tab}!A1:append",
        headers=headers,
        params={"valueInputOption": "RAW", "insertDataOption": "INSERT_ROWS"},
        json={"values": [[str(c) for c in row]]},
        timeout=30,
    )
    resp.raise_for_status()
