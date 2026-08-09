#!/usr/bin/env python3
"""财务月报入口。

  python run.py                      # 跑上个月,完整流程
  python run.py --month 2026-07      # 回溯任意月份(核对历史数据用)
  python run.py --no-llm             # 只算数字,不调模型,不花钱
  python run.py --dry-run            # 只算数字并打印,不写 Sheet 不发邮件
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from dotenv import load_dotenv

from src import deliver, narrate, report as report_mod, sheet, stripe_metrics, token_store, xero
from src.config import REPO_ROOT, Config, require_env, resolve_period


def main() -> int:
    parser = argparse.ArgumentParser(description="Stripe + Xero → 财务月报")
    parser.add_argument("--month", help="YYYY-MM,默认上一个完整月份")
    parser.add_argument("--no-llm", action="store_true", help="跳过模型,只出数字")
    parser.add_argument("--dry-run", action="store_true", help="不写 Sheet、不发邮件")
    parser.add_argument(
        "--refresh-only",
        action="store_true",
        help="只刷新并存回 Xero token,不出报表(给每周 keepalive 用)",
    )
    args = parser.parse_args()

    load_dotenv(REPO_ROOT / ".env")
    cfg = Config.load(REPO_ROOT / "config.yaml")

    if args.refresh_only:
        refresh_token = token_store.load()
        if not refresh_token:
            print("没有 refresh token,无需刷新(可能在用 Custom Connection 模式)。")
            return 0
        payload = xero.refresh_access_token(
            require_env("XERO_CLIENT_ID"), require_env("XERO_CLIENT_SECRET"), refresh_token
        )
        new_token = payload.get("refresh_token")
        if new_token and new_token != refresh_token:
            token_store.save(new_token)
        print("▸ Xero token 已刷新,60 天窗口重新计时")
        return 0

    period = resolve_period(args.month, cfg.timezone)

    print(f"▸ {cfg.business_name} · {period.label} "
          f"({period.start_date} → {period.end_date_inclusive}, {cfg.timezone})")

    print("▸ 拉取 Stripe …")
    stripe_data = stripe_metrics.collect(require_env("STRIPE_API_KEY"), period, cfg)

    print("▸ 拉取 Xero …")
    refresh_token = token_store.load()
    if not refresh_token:
        print("  ℹ 没找到 refresh token,将尝试 Custom Connection 模式。"
              "想用免费的标准 OAuth 请先跑:python authorize.py")
    xero_client = xero.XeroClient(
        require_env("XERO_CLIENT_ID"),
        require_env("XERO_CLIENT_SECRET"),
        cfg.xero_scopes,
        refresh_token=refresh_token,
    )
    xero_data = xero.collect(
        xero_client, period, cfg.xero_gst_account_code, cfg.xero_bank_account_codes
    )

    report = report_mod.build(cfg, period, stripe_data, xero_data)

    # 先把原始数字打出来。模型还没上场,这些就是终值 —— 有问题在这里就该发现。
    print("\n" + json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    for warning in report["warnings"]:
        print(f"  ⚠ {warning}", file=sys.stderr)

    narrative = None
    if not args.no_llm:
        print("▸ 生成月报正文 …")
        require_env("OPENAI_API_KEY")
        narrative = narrate.write_narrative(
            report, cfg.llm_model, cfg.llm_temperature, cfg.language
        )

    markdown = report_mod.to_markdown(report, narrative)
    json_path, md_path = deliver.write_files(REPO_ROOT / "out", report, markdown)
    print(f"▸ 已写入 {json_path.name} / {md_path.name}")
    deliver.write_job_summary(markdown)

    if args.dry_run:
        print("▸ --dry-run:跳过 Sheet 归档与邮件")
        return 0

    if sheet.is_configured():
        sheet.append_row(cfg.sheet_tab, report_mod.SHEET_HEADER, report_mod.to_sheet_row(report))
        print(f"▸ 已追加一行到 Google Sheet「{cfg.sheet_tab}」")
    else:
        print("▸ 未配置 Google Sheet,跳过归档(可选)")

    if cfg.email_to and deliver.email_configured():
        deliver.send_email(
            subject=f"[{cfg.business_name}] 财务月报 {period.label}",
            markdown=markdown,
            to=cfg.email_to,
            sender=cfg.email_from,
        )
        print(f"▸ 已发送至 {', '.join(cfg.email_to)}")
    else:
        print("▸ 未配置邮件,跳过发送(可选)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
