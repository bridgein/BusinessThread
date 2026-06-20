# 独立开发者机会小报 📮

每天早上自动给你发一封邮件：从 **Product Hunt / Hacker News / Reddit** 抓取最新内容，
用 **Claude** 筛选出「一个人能做、且有变现信号」的产品机会，排序后送到你邮箱。
跑在 **GitHub Actions** 上，零服务器、免费。

---

## 一次性配置（约 10 分钟）

### 1. 建仓库
把这几个文件放进一个新的 GitHub 仓库（**设为 Private**）：

```
digest.py
requirements.txt
seen.json
.github/workflows/digest.yml
```

### 2. 配置 Secrets
仓库 → **Settings → Secrets and variables → Actions → New repository secret**，
逐个添加：

| Secret 名 | 值 | 说明 |
|---|---|---|
| `ANTHROPIC_API_KEY` | `sk-ant-...` | 在 console.anthropic.com 创建 |
| `SMTP_HOST` | `smtp.gmail.com` | 你的邮箱服务器 |
| `SMTP_PORT` | `587` | 587(STARTTLS) 或 465(SSL) |
| `SMTP_USER` | `you@gmail.com` | 邮箱账号 |
| `SMTP_PASS` | 应用专用密码 | **不是登录密码**，见下 |
| `EMAIL_TO` | `you@gmail.com` | 收件人（你自己） |
| `EMAIL_FROM` | `you@gmail.com` | 发件人，通常同上 |

**Gmail 的应用专用密码**：需先开启两步验证，然后到 Google 账号 →
安全性 → 应用专用密码，生成一个 16 位密码填进 `SMTP_PASS`。
（其他邮箱如 Outlook、QQ、Fastmail 同理，用各自的 SMTP 信息。）

### 3. 开权限
仓库 → **Settings → Actions → General → Workflow permissions** →
选 **Read and write permissions**（让它能把去重记录提交回仓库）。

### 4. 测试
仓库 → **Actions → daily-digest → Run workflow**，手动跑一次。
绿勾后查收邮件。点开任务日志能看到抓了多少、选了几条。

---

## 调整

所有开关都在 `digest.py` 顶部配置区：

- **发送时间**：改 `.github/workflows/digest.yml` 里的 `cron`。
  现在是 `0 23 * * *`（UTC），即日本时间次日 08:00。北京时间 08:00 用 `0 0 * * *`。
- **主题聚焦 `FOCUS`**（推荐用这个控方向）：一句自然语言，比如默认的
  "AI 工具、效率/生产力工具、开发者工具"。Claude 按**含义**筛，不靠字面词，
  漏判少。想看全部方向就设成空字符串 `""`。
- **关键词预筛 `KEYWORDS_INCLUDE / EXCLUDE`**：在调 Claude 之前的便宜粗筛，
  只看字面词。`INCLUDE` 留空 `[]` 表示不预筛（推荐先这样，靠 `FOCUS`）；
  量太大想省 token 时再用它砍量。`EXCLUDE` 里的词命中就直接丢。
- **额外信源 `EXTRA_FEEDS`**：任意 RSS 都能加，格式 `(url, "显示名")`。
  Indie Hackers 的两条接法已写在注释里（见下）。
- **Reddit 板块 `REDDIT_SUBS`** / **每期条数 `MAX_PICKS`** / **时间窗 `LOOKBACK_HOURS`** /
  **模型 `MODEL`**（省钱可换 `claude-haiku-4-5-20251001`）。
- **筛选标准 `FILTER_BRIEF`**：agent 判断力的核心，选出来不合口味就改这段。

### 加 Indie Hackers（及其他源）
IH 没官方 RSS，需要第三方转换，在 `EXTRA_FEEDS` 里取消注释二选一：
- **省事**：用 RSSHub 公共实例，如 `https://rsshub.app/indiehackers/popular`
  （公共实例可能限流/不稳，当前路由以 docs.rsshub.app 为准）。
- **稳定（推荐）**：自部署 RSSHub 或 `ahonn/ihrss`，拿到自己的地址再填进去。
抓取已包异常处理，IH 源临时挂掉不影响其余内容照常发送。

---

## 工作原理

```
Product Hunt RSS ┐
Hacker News API  ├─→ 去重(seen.json) ─→ Claude 按标准筛选排序 ─→ 渲染 HTML ─→ SMTP 发邮件
Reddit RSS       ┘
```

`seen.json` 记录已处理过的链接，每次跑完由 Actions 自动提交回仓库，所以不会重复推送同一条。

## 之后可以加的源
- **Indie Hackers**：没官方 API，可自部署社区项目 `ahonn/ihrss` 拿到 RSS 后加进来。
- **付费收入数据**（Sensor Tower / AppMagic）：有真实 App 流水，但 API 很贵，按需再接。
