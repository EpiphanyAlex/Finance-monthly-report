# 财务月报自动化 · Stripe + Xero → AI 月报

每月 1 日自动跑:从 Stripe 算 MRR / churn,从 Xero 取现金流和 GST 准备金,
交给模型写成一页人话,发到你邮箱,并在 Google Sheet 里存下一行历史指标。

**全程免费**(除了模型调用的几分钱)。不需要 Xero 付费的 Custom Connection。

```
Stripe API ─┐
            ├→ 脚本算指标 → report.json ─┬→ 模型写正文 → 邮件
Xero API ───┘                            └→ 追加一行进 Google Sheet
```

**为什么是脚本而不是 MCP / 连接器:** 财务数字不能让模型算。同一个问题问两次给出两个
MRR,这报表就废了。所以脚本负责算数,模型只负责把算好的数字写成人话 —— prompt 里明确
禁止它做任何计算,报表末尾永远附原始数字表格供你核对。

---

## 15 分钟配好

### 0. 拿到代码

点右上角 **Use this template** → 建自己的私有仓库 → clone 下来。

```bash
pip install -r requirements.txt
cp config.example.yaml config.yaml
cp .env.example .env
```

### 1. Xero:建一个免费的 OAuth App(3 分钟)

1. 去 [developer.xero.com/app/manage](https://developer.xero.com/app/manage) → **New app**
2. App type 选 **Web app**(不是 Custom Connection —— 那个要收费,现阶段不需要)
3. Redirect URI 填死这个:`http://localhost:8976/callback`
4. 建好后到 **Configuration** 页,复制 Client ID,点 **Generate a secret** 复制 Secret
5. 填进 `.env` 的 `XERO_CLIENT_ID` / `XERO_CLIENT_SECRET`

然后本地跑一次授权(**只需要跑这一次**):

```bash
python authorize.py
```

浏览器会弹出来让你选组织、点同意。回到终端会打印一个 `XERO_REFRESH_TOKEN`,先留着。

### 2. Stripe:建一个只读密钥(1 分钟)

Stripe Dashboard → Developers → API keys → **Create restricted key**。
只勾这三个的 **Read**,其他一律不给:

- Subscriptions
- Invoices
- Customers

复制 `rk_live_...` 填进 `.env` 的 `STRIPE_API_KEY`。

> 只读密钥是硬要求。这个脚本永远不需要写权限,给了就是白给风险。

### 3. 改 config.yaml(2 分钟)

至少改这三项:

```yaml
business:
  name: "你的公司名"
  timezone: "Australia/Melbourne"   # 月份边界按这个时区切
  home_currency: "aud"

xero:
  gst_account_code: "820"           # AU 标准科目表通常是 820
```

`gst_account_code` 填错了不会静默出错 —— 脚本会报错并把你账上的候选科目列出来,照着改一次就对。

### 4. 先在本地验一遍数字(最重要的一步)

```bash
python run.py --month 2026-07 --no-llm --dry-run
```

`--no-llm` 不调模型不花钱,`--dry-run` 不写 Sheet 不发邮件。它会把所有数字打印出来。

**拿这些数字去对 Stripe Dashboard 和 Xero 的报表。** 至少对三个月。
对不上就别往下走 —— 一个每月准时送达的错数字,比没有报表更危险。

对上了再跑完整流程:

```bash
python run.py --month 2026-07
```

`out/report-2026-07.md` 就是成品。

### 5. 上 GitHub Actions(5 分钟)

仓库 → Settings → Secrets and variables → Actions,加这几个:

| Secret | 必填 | 说明 |
| --- | --- | --- |
| `XERO_CLIENT_ID` | ✅ | 第 1 步 |
| `XERO_CLIENT_SECRET` | ✅ | 第 1 步 |
| `XERO_REFRESH_TOKEN` | ✅ | 第 1 步 `authorize.py` 打印出来的 |
| `STRIPE_API_KEY` | ✅ | 第 2 步 |
| `OPENAI_API_KEY` | ✅ | platform.openai.com |
| `GH_PAT` | ✅ | 见下面「关于 GH_PAT」 |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | ⬜ | Sheet 归档,可选 |
| `GOOGLE_SHEET_ID` | ⬜ | Sheet 归档,可选 |
| `SMTP_HOST` / `SMTP_USER` / `SMTP_PASSWORD` | ⬜ | 邮件,可选 |

把 `config.yaml` 提交进仓库(里面没有密钥),然后去 Actions 页手动触发一次
**财务月报** 确认能跑通。之后每月 1 日自动运行。

---

## 关于 GH_PAT(唯一有点绕的地方)

Xero 的 refresh token **每次使用都会轮换**,旧的立刻作废。所以每次跑完,脚本必须把
新 token 存回去,否则下次就登不上了。GitHub Actions 是无状态的,存回去 = 写回 Secret,
而写 Secret 需要一个 PAT —— 内置的 `GITHUB_TOKEN` 没这个权限。

建法:Settings → Developer settings → **Fine-grained tokens** → 只授权这一个仓库,
权限只勾 **Secrets: Read and write**。存成仓库 secret `GH_PAT`。

没有它也能跑,但 60 天内如果没有成功刷新过,就要重新跑 `authorize.py`。

### 每周保活 job

`xero-token-keepalive.yml` 每周一跑一次,做两件事:

1. 刷新 Xero token —— 60 天的过期窗口永远走不到头
2. 提交一个时间戳文件 —— GitHub 会在仓库 60 天无活动后**自动禁用定时 workflow**,
   而定时任务自己跑不算"活动"。这一步把计时器按回去。

两个 60 天窗口,一个 job 解决。别删这个 workflow。

---

## 各个数字是怎么来的

| 指标 | 来源 | 注意 |
| --- | --- | --- |
| MRR | Stripe 存量订阅,按周期折算成月 | 年付 /12、季付 /3、周付 ×52/12;折扣按近似处理 |
| churn | Stripe `canceled_at` 落在本月的订阅 | 无法服务端过滤,只能扫描,量大时调高 `churn_scan_max_pages` |
| 现金 | Xero Bank Summary | 期末余额合计 |
| GST 准备金 | Xero Trial Balance 上 GST 科目的 `credit - debit` | 见下 |
| P&L | Xero Profit and Loss | 行标签因地区而异,取不到就是 `—`,不影响其他数字 |

**关于 GST:** Xero 的 Accounting API **没有 Activity Statement / BAS 端点**。
GST Report 只有在 Xero 界面里手动 publish 之后才能通过 `Reports/{ReportID}` 取到,
没法自动化。所以这里直接读 GST 负债科目的余额 —— 那个数就是你欠税局的钱,够用且干净。

**MRR 的坑:** 阶梯计价(tiered pricing)没有单价,算不了,脚本会跳过并在
`warnings` 里点名是哪个订阅。不会静默让你的 MRR 偏低。多币种没配汇率同理。

---

## 常用命令

```bash
python run.py                          # 上个月,完整流程
python run.py --month 2026-07          # 回溯任意月份
python run.py --no-llm                 # 只算数字,不花钱
python run.py --dry-run                # 不写 Sheet 不发邮件
python run.py --refresh-only           # 只刷新 Xero token
pytest -q                              # 36 个离线测试,不需要任何凭证
```

`pytest` 跑的是全链路离线测试:用真实结构的 Xero / Stripe 响应喂进去,
一路验到 Markdown 输出。改了 `stripe_metrics.py` 或 `xero.py` 之后先跑这个。

Actions 页手动触发也支持传月份(**Run workflow** → 填 `2026-07`)。

---

## 文件结构

```
run.py                  入口
authorize.py            一次性 Xero 授权
config.example.yaml     配置模板(可提交)
src/
  config.py             配置 + 月份边界计算
  xero.py               Xero 客户端 + 报表解析 + 指标提取
  stripe_metrics.py     MRR / churn 计算 ← 最值得盯的文件
  report.py             组装 report.json + 渲染 Markdown
  narrate.py            调模型写正文(prompt 里禁止计算)
  sheet.py              Google Sheet 归档(可选)
  deliver.py            落盘 / Actions 摘要 / 邮件
tests/test_mrr.py       MRR 计算单测,不联网
```

---

## 什么时候该换成 Custom Connection

业务跑起来之后,如果你觉得每周保活 job + PAT 这套太绕,可以花钱买 Xero 的
**Custom Connection**(机器对机器,没有 refresh token 需要维护)。

脚本已经支持:把 `XERO_REFRESH_TOKEN` 留空,它会自动切到 `client_credentials` 模式,
其他代码一行都不用改。现在没必要。
