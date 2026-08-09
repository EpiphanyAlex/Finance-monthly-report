"""把算好的数字交给模型写成人话。

铁律:模型不做任何计算。所有数字在 report.json 里已经是终值,
模型只负责解释和排优先级。prompt 里这条写死,别删。
"""

from __future__ import annotations

import json

from openai import OpenAI

SYSTEM_PROMPT = """你是这家公司的 CFO,正在给创始人写月度财务简报。

绝对规则:
1. 不要做任何计算。不要算同比、环比、百分比、平均值、预测值。
   所有数字都已经在输入 JSON 里算好了,你只能引用,不能推导。
   如果某个你想说的指标不在 JSON 里,就不要提它。
2. JSON 里值为 null 或空字符串的字段,说明这次没取到,直接说"本月未取到",
   不要猜、不要用其他数字倒推。
3. warnings 数组里的内容必须原样在简报末尾列出来 —— 那是数据质量问题,
   创始人需要知道哪些数字不可全信。

写作要求:
- 开头一句话结论:这个月生意是变好还是变坏,凭哪个数。
- 然后分三块:增长(MRR / 新增 / 流失)、现金(余额 / 净流动)、税务(GST 准备金)。
- 每块最多 3 句。不要复述所有数字,挑真正会影响决策的。
- 结尾给 1–3 条具体行动项,每条要能在两周内做完。
- 不要用"综上所述""总体而言"这类填充话。不要吹。数字难看就直说。"""


def write_narrative(report: dict, model: str, temperature: float, language: str) -> str:
    client = OpenAI()  # 读环境变量 OPENAI_API_KEY

    lang_hint = {
        "zh": "用简体中文写。",
        "en": "Write in English.",
    }.get(language, f"用 {language} 写。")

    response = client.chat.completions.create(
        model=model,
        temperature=temperature,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT + "\n\n" + lang_hint},
            {
                "role": "user",
                "content": (
                    f"这是 {report['meta']['business']} 在 {report['meta']['period']} 的财务数据。\n"
                    f"本位币:{report['meta']['home_currency']}\n\n"
                    f"```json\n{json.dumps(report, ensure_ascii=False, indent=2)}\n```"
                ),
            },
        ],
    )
    return (response.choices[0].message.content or "").strip()
