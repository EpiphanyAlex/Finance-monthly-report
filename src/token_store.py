"""Xero refresh token 的存取。

Xero 的 refresh token 每次使用都会轮换,旧的立即作废(有 30 分钟宽限期)。
所以每次刷新后必须把新 token 存回去,否则下次就登不上了。

存哪里:
  - 本地跑    → .xero_token.json(已 gitignore)
  - Actions 里 → 写回同名 GitHub Secret(需要一个有 secrets 写权限的 PAT)
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from .config import REPO_ROOT

LOCAL_PATH = REPO_ROOT / ".xero_token.json"
SECRET_NAME = "XERO_REFRESH_TOKEN"


def load() -> str | None:
    """环境变量优先(Actions 场景),否则读本地文件。"""
    token = os.environ.get(SECRET_NAME, "").strip()
    if token:
        return token
    if LOCAL_PATH.exists():
        return json.loads(LOCAL_PATH.read_text(encoding="utf-8")).get("refresh_token")
    return None


def save(refresh_token: str) -> None:
    if os.environ.get("GITHUB_ACTIONS") == "true":
        _save_to_github_secret(refresh_token)
    else:
        LOCAL_PATH.write_text(
            json.dumps({"refresh_token": refresh_token}, indent=2), encoding="utf-8"
        )
        LOCAL_PATH.chmod(0o600)
        print(f"▸ 新的 refresh token 已存入 {LOCAL_PATH.name}")


def _save_to_github_secret(refresh_token: str) -> None:
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    if not os.environ.get("GH_TOKEN"):
        print(
            f"\n⚠ 没有 GH_TOKEN,轮换后的 refresh token 无法写回 {SECRET_NAME}。\n"
            f"  这次能跑完,但 60 天内不再成功刷新的话就要重新授权。\n"
            f"  修法:建一个 fine-grained PAT(权限 Secrets: Read and write),"
            f"存成仓库 secret GH_PAT。\n",
            file=sys.stderr,
        )
        return

    # 通过 stdin 传值,不放进 argv —— 避免 token 出现在进程列表里
    result = subprocess.run(
        ["gh", "secret", "set", SECRET_NAME, "--repo", repo],
        input=refresh_token,
        text=True,
        capture_output=True,
    )
    if result.returncode != 0:
        print(
            f"\n⚠ 写回 GitHub Secret 失败:{result.stderr.strip()}\n"
            f"  检查 GH_PAT 是否有该仓库的 Secrets: Read and write 权限。\n",
            file=sys.stderr,
        )
    else:
        print(f"▸ 新的 refresh token 已写回 GitHub Secret {SECRET_NAME}")
