#!/usr/bin/env python3
"""
独立开发者机会小报 (Indie Opportunity Digest)
每天抓取 Product Hunt / Hacker News / Reddit，用 Claude 筛选出
"个人开发者可做 + 有变现信号" 的产品机会，排序后发邮件。

需要的环境变量（在 GitHub Secrets 里配置）:
  ANTHROPIC_API_KEY  - Claude API key
  SMTP_HOST          - 邮件服务器，例如 smtp.gmail.com
  SMTP_PORT          - 端口，587 (STARTTLS) 或 465 (SSL)
  SMTP_USER          - 邮箱账号
  SMTP_PASS          - 邮箱密码 / 应用专用密码
  EMAIL_TO           - 收件人（你自己）
  EMAIL_FROM         - 发件人（通常 = SMTP_USER）
"""

import os
import json
import html
import smtplib
import datetime as dt
from email.mime.text import MIMEText
from email.header import Header

import requests
import feedparser

# ----------------------------------------------------------------------------
# 配置区：信源 / 模型 / 阈值，随时改这里
# ----------------------------------------------------------------------------

MODEL = "claude-haiku-4-5-20251001"  # テスト中。本番は claude-sonnet-4-6
MAX_PICKS = 8                        # 每期最多几条
LOOKBACK_HOURS = 30                  # 只看最近多少小时的内容
MAX_RAW_ITEMS = 60                   # 喂给 Claude 的原始条目上限（控制 token）
SEEN_FILE = "seen.json"              # 去重状态文件
MIN_HN_POINTS = 3                    # Hacker News 最低分数

REDDIT_SUBS = ["SaaS", "microsaas", "SideProject", "EntrepreneurRideAlong"]
PRODUCTHUNT_FEED = "https://www.producthunt.com/feed"

# 额外订阅源：任意 RSS 都能塞进来，格式 (url, 显示名)。
# Indie Hackers 没官方 API，需要第三方转换，下面给了两条路子（默认注释掉）：
#   1) RSSHub 公共实例（最省事，但公共实例可能限流/不稳，确认路由见 docs.rsshub.app）
#   2) 自部署 ahonn/ihrss 或自建 RSSHub，拿到自己的稳定地址后填这里（推荐）
# 以后任何 newsletter / 博客 / 其他源，照葫芦画瓢加一行即可。
EXTRA_FEEDS = [
    # ("https://rsshub.app/indiehackers/popular", "Indie Hackers"),
    # ("https://你自部署的地址/indiehackers", "Indie Hackers"),
]

# 主题聚焦（语义层）：用自然语言描述你只想看的方向，会注入给 Claude 判断。
# 这是"聪明"的过滤——Claude 按含义筛，不靠字面词。留空字符串 "" 表示不限方向。
FOCUS = "AI 工具、效率/生产力工具、开发者工具这几类。其他方向除非机会特别大，否则不选。"

# 关键词预筛（便宜但"笨"的一层，在调用 Claude 之前先砍量、省 token）：
#   - INCLUDE：标题或摘要里含任一关键词才保留；留空 [] = 全部保留（推荐先留空，靠 FOCUS 筛）
#   - EXCLUDE：含任一关键词就直接丢弃
# 注意：关键词只看字面，会漏掉没出现该词但其实相关的条目。所以建议主要靠 FOCUS，
# 关键词只在"量太大、想省钱"时用来粗筛。
KEYWORDS_INCLUDE = []
KEYWORDS_EXCLUDE = []

UA = "indie-digest-bot/1.0 (personal use)"
HEADERS = {"User-Agent": UA}

# 给 Claude 的筛选标准——这里就是这个 agent 的"判断力"，按需要调
_focus_line = f"\n方向聚焦：{FOCUS}\n" if FOCUS.strip() else "\n"
FILTER_BRIEF = """你是一名产品机会侦察兵，专门为独立开发者寻找「有收入增长或用户增长信号」的产品机会。

【唯一选取标准】条目中必须包含以下任一具体信号，才可入选：
- 收入信号：提到 MRR / ARR / 付费用户数 / 定价 / 收入金额（如 $500 MRR、100 paying users）
- 用户增长信号：提到用户数增长、DAU/MAU、增长率、waitlist 人数等具体数字
- 市场需求信号：明确的付费意愿（用户在评论中说愿意付费）或竞品的收入数据%s
没有上述任一信号的条目，无论话题多热门，一律不选。

为每个入选项填写以下字段：
- why: 具体说明该条目包含哪个信号、数字是多少（一句中文，必须引用原文数字）
- solo_reason: 为什么一个人可以实现（技术复杂度、运营负担、有无现成 API 等）
- signal: 原文中的客观数字（直接引用，如「MRR $2k」「3,000 waitlist」「HN 412点」）

按信号の強さ（収入 > ユーザー数 > 市場需要）で降順に並べ、最大 %d 件。
信号が弱い場合でも、条件を満たすものは積極的に選ぶこと。

只返回 JSON，格式严格如下，不要任何解释或 markdown 代码块：
{"picks": [{"title": "...", "url": "...", "why": "一句中文", "solo_reason": "一句中文", "signal": "...", "source": "..."}]}
""" % (_focus_line, MAX_PICKS)


# ----------------------------------------------------------------------------
# 去重状态
# ----------------------------------------------------------------------------

def load_seen():
    if os.path.exists(SEEN_FILE):
        try:
            with open(SEEN_FILE) as f:
                return set(json.load(f).get("ids", []))
        except Exception:
            return set()
    return set()


def save_seen(ids):
    # 只保留最近 2000 条，避免文件无限增长
    keep = list(ids)[-2000:]
    with open(SEEN_FILE, "w") as f:
        json.dump({"ids": keep}, f, ensure_ascii=False, indent=0)


# ----------------------------------------------------------------------------
# 时间工具
# ----------------------------------------------------------------------------

def recent(ts_struct):
    """feedparser 的时间结构是否在 LOOKBACK_HOURS 之内。"""
    if not ts_struct:
        return True  # 没时间戳就放行，交给去重和 Claude
    published = dt.datetime(*ts_struct[:6], tzinfo=dt.timezone.utc)
    return (dt.datetime.now(dt.timezone.utc) - published) <= dt.timedelta(hours=LOOKBACK_HOURS)


# ----------------------------------------------------------------------------
# 各信源抓取（每个都包了 try，单个挂掉不影响整体）
# ----------------------------------------------------------------------------

def fetch_rss(url, source):
    out = []
    try:
        resp = requests.get(url, headers=HEADERS, timeout=20)
        feed = feedparser.parse(resp.content)
        for e in feed.entries:
            if not recent(e.get("published_parsed")):
                continue
            link = e.get("link", "")
            if not link:
                continue
            summary = html.unescape(e.get("summary", ""))[:400]
            # Reddit RSS には <score> タグが含まれる場合がある
            score = getattr(e, "score", None) or getattr(e, "ups", None)
            try:
                score = int(score)
            except (TypeError, ValueError):
                score = 0
            out.append({
                "id": link,
                "title": e.get("title", "").strip(),
                "url": link,
                "snippet": summary,
                "source": source,
                "points": score,
            })
    except Exception as ex:
        print(f"[warn] {source} 抓取失败: {ex}")
    return out


def fetch_producthunt():
    return fetch_rss(PRODUCTHUNT_FEED, "Product Hunt")


def fetch_reddit():
    out = []
    for sub in REDDIT_SUBS:
        url = f"https://www.reddit.com/r/{sub}/top/.rss?t=day"
        out += fetch_rss(url, f"Reddit r/{sub}")
    return out


def fetch_hackernews():
    out = []
    try:
        url = ("https://hn.algolia.com/api/v1/search_by_date"
               "?tags=show_hn&hitsPerPage=50")
        data = requests.get(url, headers=HEADERS, timeout=20).json()
        cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=LOOKBACK_HOURS)
        for hit in data.get("hits", []):
            created = dt.datetime.fromtimestamp(hit["created_at_i"], tz=dt.timezone.utc)
            if created < cutoff:
                continue
            if (hit.get("points") or 0) < MIN_HN_POINTS:
                continue
            link = hit.get("url") or f"https://news.ycombinator.com/item?id={hit['objectID']}"
            out.append({
                "id": link,
                "title": (hit.get("title") or "").strip(),
                "url": link,
                "snippet": (hit.get("story_text") or "")[:400],
                "source": "Hacker News (Show HN)",
                "points": hit.get("points") or 0,
                "comments": hit.get("num_comments") or 0,
            })
    except Exception as ex:
        print(f"[warn] Hacker News 抓取失败: {ex}")
    return out


def collect():
    items = []
    items += fetch_producthunt()
    items += fetch_hackernews()
    items += fetch_reddit()
    for url, label in EXTRA_FEEDS:          # Indie Hackers 等额外源
        items += fetch_rss(url, label)
    # 按来源去重相同 url
    uniq = {}
    for it in items:
        uniq.setdefault(it["id"], it)
    return list(uniq.values())


def keyword_prefilter(items):
    """便宜的字面预筛：先过 EXCLUDE，再（若设了 INCLUDE）只留命中的。"""
    def text(it):
        return (it["title"] + " " + it["snippet"]).lower()

    out = items
    if KEYWORDS_EXCLUDE:
        bad = [k.lower() for k in KEYWORDS_EXCLUDE]
        out = [it for it in out if not any(k in text(it) for k in bad)]
    if KEYWORDS_INCLUDE:
        good = [k.lower() for k in KEYWORDS_INCLUDE]
        out = [it for it in out if any(k in text(it) for k in good)]
    return out


# ----------------------------------------------------------------------------
# 用 Claude 筛选 + 排序
# ----------------------------------------------------------------------------

def rank_with_claude(items):
    payload = []
    for it in items[:MAX_RAW_ITEMS]:
        entry = {
            "title": it["title"],
            "url": it["url"],
            "snippet": it["snippet"],
            "source": it["source"],
        }
        if it.get("points"):
            entry["points"] = it["points"]
        if it.get("comments"):
            entry["comments"] = it["comments"]
        payload.append(entry)
    user_msg = (FILTER_BRIEF + "\n\n原始条目：\n"
                + json.dumps(payload, ensure_ascii=False, indent=1))

    resp = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": os.environ["ANTHROPIC_API_KEY"],
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": MODEL,
            "max_tokens": 2000,
            "messages": [{"role": "user", "content": user_msg}],
        },
        timeout=120,
    )
    resp.raise_for_status()
    text = "".join(
        b.get("text", "") for b in resp.json().get("content", [])
        if b.get("type") == "text"
    ).strip()
    # 去掉可能的 ```json 包裹
    if text.startswith("```"):
        text = text.strip("`")
        text = text[text.find("{"):text.rfind("}") + 1]
    try:
        return json.loads(text).get("picks", [])
    except Exception as ex:
        print(f"[warn] 解析 Claude 输出失败: {ex}\n原文: {text[:500]}")
        return []


# ----------------------------------------------------------------------------
# 渲染 + 发送邮件
# ----------------------------------------------------------------------------

def render_html(picks_by_source):
    today = dt.date.today().isoformat()
    total = sum(len(v) for v in picks_by_source.values())
    sections = []
    for source_label, picks in picks_by_source.items():
        if not picks:
            continue
        rows = []
        for i, p in enumerate(picks, 1):
            title = html.escape(p.get("title", "(无标题)"))
            url = html.escape(p.get("url", "#"))
            why = html.escape(p.get("why", ""))
            solo_reason = html.escape(p.get("solo_reason", ""))
            signal = html.escape(p.get("signal", ""))
            signal_html = (f'<div style="margin-top:4px;font-size:12px;color:#9a9a9a;">📊 {signal}</div>'
                           if signal and signal != "无公开数据" else "")
            rows.append(f"""
            <div style="margin:0 0 18px;padding:0 0 14px;border-bottom:1px solid #ececec;">
              <div style="font-size:13px;color:#9a9a9a;">#{i}</div>
              <a href="{url}" style="font-size:17px;font-weight:600;color:#111;text-decoration:none;line-height:1.4;">{title}</a>
              {signal_html}
              <div style="margin-top:6px;font-size:15px;color:#444;line-height:1.6;">{why}</div>
              <div style="margin-top:4px;font-size:13px;color:#666;">👤 {solo_reason}</div>
            </div>""")
        sections.append(f"""
        <div style="margin:0 0 30px;">
          <div style="font-size:13px;font-weight:700;color:#fff;background:#333;padding:4px 10px;border-radius:4px;display:inline-block;margin-bottom:14px;">{html.escape(source_label)}</div>
          {"".join(rows)}
        </div>""")
    body = "".join(sections)
    return f"""<!DOCTYPE html><html><body style="margin:0;background:#f6f6f4;">
      <div style="max-width:600px;margin:0 auto;padding:28px 22px;font-family:-apple-system,'Segoe UI',Roboto,'Helvetica Neue',sans-serif;">
        <h1 style="font-size:20px;margin:0 0 4px;color:#111;">独立开发者机会小报</h1>
        <div style="font-size:13px;color:#9a9a9a;margin-bottom:24px;">{today} · 共 {total} 条</div>
        {body}
        <div style="font-size:12px;color:#b5b5b5;margin-top:20px;">由 Claude 从 Product Hunt / Hacker News / Reddit 筛选。改信源和筛选标准请编辑 digest.py。</div>
      </div></body></html>"""


def send_email(html_body, count):
    host = os.environ["SMTP_HOST"]
    port = int(os.environ["SMTP_PORT"])
    user = os.environ["SMTP_USER"]
    pwd = os.environ["SMTP_PASS"]
    to_addr = os.environ["EMAIL_TO"]
    from_addr = os.environ.get("EMAIL_FROM", user)

    msg = MIMEText(html_body, "html", "utf-8")
    msg["Subject"] = Header(f"📮 独立开发者机会小报 · {dt.date.today().isoformat()} ({count}条)", "utf-8")
    msg["From"] = from_addr
    msg["To"] = to_addr

    if port == 465:
        server = smtplib.SMTP_SSL(host, port, timeout=30)
    else:
        server = smtplib.SMTP(host, port, timeout=30)
        server.starttls()
    server.login(user, pwd)
    server.sendmail(from_addr, [to_addr], msg.as_string())
    server.quit()


# ----------------------------------------------------------------------------
# 主流程
# ----------------------------------------------------------------------------

def main():
    seen = load_seen()
    items = collect()
    print(f"抓到 {len(items)} 条原始内容")

    fresh = [it for it in items if it["id"] not in seen]
    print(f"其中 {len(fresh)} 条是新的")

    # 不管有没有入选，都把抓到的标记为已读，避免明天重复送审
    seen.update(it["id"] for it in items)
    save_seen(seen)

    if not fresh:
        print("没有新内容，今天不发。")
        return

    fresh = keyword_prefilter(fresh)
    print(f"关键词预筛后剩 {len(fresh)} 条")
    if not fresh:
        print("预筛后没有内容，今天不发。")
        return

    source_groups = {
        "Product Hunt": [it for it in fresh if it["source"] == "Product Hunt"],
        "Hacker News":  [it for it in fresh if it["source"] == "Hacker News (Show HN)"],
        "Reddit":       [it for it in fresh if it["source"].startswith("Reddit")],
    }

    picks_by_source = {}
    for label, group_items in source_groups.items():
        if not group_items:
            print(f"{label}: 新着なし、スキップ")
            continue
        picks = rank_with_claude(group_items)
        print(f"{label}: Claude が {len(picks)} 件を選出")
        picks_by_source[label] = picks

    total = sum(len(v) for v in picks_by_source.values())
    if total == 0:
        print("没有合格机会，今天不发。")
        return

    send_email(render_html(picks_by_source), total)
    print("邮件已发送 ✅")


if __name__ == "__main__":
    main()
