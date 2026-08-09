#!/usr/bin/env python3
"""一次性授权:在浏览器里点一下,拿到 Xero refresh token。

只需要在本地跑一次:

    python authorize.py

它会打开浏览器让你选组织并同意授权,然后把 refresh token 存进 .xero_token.json,
并打印出来供你粘贴到 GitHub Secrets。

前提:在 Xero 开发者后台把这个 app 的 Redirect URI 设成
    http://localhost:8976/callback
"""

from __future__ import annotations

import secrets
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer

import requests
from dotenv import load_dotenv

from src import token_store
from src.config import REPO_ROOT, DEFAULT_XERO_SCOPES, die, require_env
from src.xero import TOKEN_URL, basic_auth_header

PORT = 8976
REDIRECT_URI = f"http://localhost:{PORT}/callback"
AUTHORIZE_URL = "https://login.xero.com/identity/connect/authorize"

# offline_access 是拿到 refresh token 的必要条件,标准 OAuth 流程一定要带。
SCOPES = f"offline_access {DEFAULT_XERO_SCOPES}"

_received: dict[str, str] = {}


class CallbackHandler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        query = urllib.parse.urlparse(self.path).query
        params = urllib.parse.parse_qs(query)
        _received.update({k: v[0] for k, v in params.items()})

        ok = "code" in _received
        body = (
            "<h2>✓ 授权成功</h2><p>可以关掉这个页面,回终端看结果。</p>"
            if ok
            else f"<h2>✗ 授权失败</h2><pre>{_received.get('error', '未知错误')}</pre>"
        )
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(f"<meta charset='utf-8'>{body}".encode())

    def log_message(self, *_args):  # 别把回调 URL(含 code)打进终端日志
        pass


def main() -> int:
    load_dotenv(REPO_ROOT / ".env")
    client_id = require_env("XERO_CLIENT_ID")
    client_secret = require_env("XERO_CLIENT_SECRET")
    state = secrets.token_urlsafe(16)

    url = f"{AUTHORIZE_URL}?" + urllib.parse.urlencode(
        {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": REDIRECT_URI,
            "scope": SCOPES,
            "state": state,
        }
    )

    print(f"▸ 正在打开浏览器授权。如果没自动打开,手动访问:\n\n{url}\n")
    print(f"▸ 提示:Xero 后台的 Redirect URI 必须正好是 {REDIRECT_URI}\n")
    webbrowser.open(url)

    server = HTTPServer(("127.0.0.1", PORT), CallbackHandler)
    server.handle_request()  # 收到一次回调就够
    server.server_close()

    if _received.get("state") != state:
        die("state 不匹配,可能是 CSRF 或者你同时跑了两次。重跑一次 authorize.py。")
    if "code" not in _received:
        die(f"没拿到授权码:{_received.get('error_description') or _received}")

    print("▸ 用授权码换 token …")
    resp = requests.post(
        TOKEN_URL,
        headers={
            "Authorization": basic_auth_header(client_id, client_secret),
            "Content-Type": "application/x-www-form-urlencoded",
        },
        data={
            "grant_type": "authorization_code",
            "code": _received["code"],
            "redirect_uri": REDIRECT_URI,
        },
        timeout=60,
    )
    if resp.status_code != 200:
        die(f"换取 token 失败:{resp.status_code} {resp.text[:500]}")

    refresh_token = resp.json().get("refresh_token")
    if not refresh_token:
        die("Xero 没返回 refresh token。确认授权 scope 里带了 offline_access。")

    token_store.save(refresh_token)
    print(
        "\n" + "─" * 68 + "\n"
        "把下面这个值加到 GitHub Secrets,名字叫 XERO_REFRESH_TOKEN:\n\n"
        f"{refresh_token}\n\n"
        "注意:它每次使用都会轮换,脚本会自动写回。这里这个值只用于首次录入。\n"
        + "─" * 68
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
