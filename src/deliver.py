"""交付:落盘 → GitHub Actions 摘要 → 邮件(可选)。

落盘和 Actions 摘要是零配置的,没配邮箱也能看到月报。
"""

from __future__ import annotations

import json
import os
import smtplib
from email.message import EmailMessage
from pathlib import Path


def write_files(out_dir: Path, report: dict, markdown: str) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    period = report["meta"]["period"]
    json_path = out_dir / f"report-{period}.json"
    md_path = out_dir / f"report-{period}.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(markdown, encoding="utf-8")
    return json_path, md_path


def write_job_summary(markdown: str) -> bool:
    """GitHub Actions 会把这段渲染在运行结果页上,不用点开 artifact 就能看。"""
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return False
    with open(path, "a", encoding="utf-8") as f:
        f.write(markdown + "\n")
    return True


def email_configured() -> bool:
    return all(os.environ.get(k) for k in ("SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD"))


def send_email(subject: str, markdown: str, to: list[str], sender: str) -> None:
    host = os.environ["SMTP_HOST"]
    port = int(os.environ.get("SMTP_PORT", "587"))
    user = os.environ["SMTP_USER"]
    password = os.environ["SMTP_PASSWORD"]

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = sender or user
    msg["To"] = ", ".join(to)
    msg.set_content(markdown)

    with smtplib.SMTP(host, port, timeout=60) as smtp:
        smtp.starttls()
        smtp.login(user, password)
        smtp.send_message(msg)
