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

## 🔒 动手之前:你的仓库必须是 Private

**公开仓库的 GitHub Actions 运行日志、job summary、artifact,任何人不用登录就能看。**

这个脚本会把 report.json 打印到日志、把月报写进 job summary、把 `out/` 传成 artifact。
仓库只要是公开的,你的 **MRR、银行余额、GST 欠税额就挂在公网上**了 —— 而且不会有任何报错,
你不会发现。

所以:

- 从模板建仓库时,可见性选 **Private**
- 如果你是 fork 的:fork 公开仓库默认还是公开,**立刻**去 Settings → 拉到底 →
  Change repository visibility → Private
- 私有仓库的 Actions 免费额度是 2000 分钟/月,这个任务一次约 2 分钟,完全够用

密钥本身是安全的(GitHub Secrets 在公开仓库也不会泄露),**泄露的是跑出来的数字。**

---

## 15 分钟配好

### 0. 拿到代码

点右上角 **Use this template** → 可见性选 **Private** → clone 下来。

```bash
pip install -r requirements.txt
cp .env.example .env
# config.yaml 仓库里已经有了,直接改它;config.example.yaml 是留作参考的原始副本
```

### 1. Xero:建一个免费的 OAuth App(3 分钟)

> **前提:你的 Xero 账号得先有一个组织**,否则 `my.xero.com` 会被强制重定向到
> 「Add your business」,进不去 My Xero 也建不了 Demo Company。
> 新账号的做法:先 **Start trial**(免费 30 天,不用信用卡,**别点 Buy now**),
> 然后 My Xero → **Try the Demo Company** → Country 选 **Australia**。
> Demo Company 有预置数据,试用组织是空的 —— 要用 Demo Company 测。

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

> **先确认仓库是 Private。** Settings 顶部会写 `Private` 徽章。是 `Public` 的话现在就改 ——
> 一旦跑过一次,日志里的数字就已经公开过了,改可见性也追不回来。

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

改完 `config.yaml` 提交进仓库(里面没有密钥),然后去 Actions 页手动触发一次
**财务月报** 确认能跑通。之后每月 1 日自动运行。

---

## 成熟度:哪些验过,哪些没有

同一套代码,不同部分的把握程度差很多。踩坑时先看这张表:

| 模块 | 状态 |
| --- | --- |
| Xero OAuth + 报表解析 | ✅ 对真实 API 验过(Demo Company AU) |
| 金额计算与格式化 | ✅ 38 个离线测试 |
| Stripe MRR / churn | ⚠️ **只跑过离线假数据,未接真实 API** |
| OpenAI 生成正文 | ⚠️ 未接真实 API,`llm.model` 默认值不一定在你账号可用 |
| Google Sheet / SMTP | ⚠️ 未验证 |
| GitHub Actions 定时与 token 写回 | ⚠️ 未验证 |

标 ⚠️ 的部分请**务必先用 `--no-llm --dry-run` 逐项对数**。

前车之鉴:Xero 那半边曾经 36 个离线测试全绿,一接真实数据立刻炸出两个 bug,其中一个
**是静默的** —— 现金显示 0.00,不报错,月报照常生成。离线测试绿 ≠ 数字对。

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
实测 `Reports/BASReport`、`Reports/GSTReport`、`Reports/ActivityStatement` 全部返回 404 ——
scope 文档把它们列为 `accounting.reports.taxreports.read` 的资源有误导性,那两个报表只有在
Xero 界面里手动 publish 之后才能通过 `Reports/{ReportID}` 取到,没法自动化。
所以这里直接读 GST 负债科目的余额 —— 那个数就是你欠税局的钱,够用且干净。

**踩过的两个解析坑**(已修 + 有回归测试,列在这里是因为你改代码时可能重新踩):

- Xero 指同一个科目 ID 用了两个属性名:TrialBalance 是 `account`,BankSummary 是
  `accountID`。只认一个 → 现金静默变成 0。
- P&L 里 `Total Income` 是 `SummaryRow`,但 `GROSS PROFIT` / `NET PROFIT` 是普通 `Row`,
  而且全大写。只收 SummaryRow → 净利取不到。

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
