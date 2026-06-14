import os
import difflib
import requests
from datetime import datetime, timezone, timedelta

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
APIFY_TOKEN = os.environ["APIFY_TOKEN"]

TW_TZ = timezone(timedelta(hours=8))          # 台灣時區（runner 在 UTC）
KEYWORD = "外電綜合整理"                         # 只要含這關鍵字的當日貼文
SIMILAR_THRESHOLD = 0.80                       # 內容相似度 >= 此值視為重複
DEBUG = os.environ.get("FB_NEWS_DEBUG", "").lower() in ("1", "true", "yes")  # 測試模式

# 兩個 FB 粉專
PAGES = [
    "https://www.facebook.com/profile.php?id=61577628819414",
    "https://www.facebook.com/profile.php?id=61550725319794",
]

APIFY_URL = ("https://api.apify.com/v2/acts/"
             "apify~facebook-posts-scraper/run-sync-get-dataset-items")


# ── 抓 FB 貼文（透過 Apify）─────────────────────────────────────────────────────

def fetch_posts():
    """呼叫 Apify Facebook Posts Scraper，回傳 dataset items（list of dict）。"""
    payload = {
        "startUrls": [{"url": u} for u in PAGES],
        "resultsLimit": 10,           # 每個粉專抓最近 10 篇就夠濾當日
        "onlyPostsNewerThan": "2 days",  # 多抓兩天，再用台灣日期精準過濾
        "captionText": False,
    }
    r = requests.post(APIFY_URL, params={"token": APIFY_TOKEN}, json=payload, timeout=300)
    r.raise_for_status()
    return r.json()


def post_date_tw(item):
    """把貼文時間換算成台灣日期。支援 unix timestamp 或 ISO 字串。"""
    ts = item.get("timestamp")
    if isinstance(ts, (int, float)):
        return datetime.fromtimestamp(ts, TW_TZ).date()
    raw = item.get("time") or item.get("date")
    if raw:
        try:
            dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(TW_TZ).date()
        except ValueError:
            return None
    return None


# ── 去重（兩粉專若貼一樣的就合併）───────────────────────────────────────────────

def _norm(s):
    return "".join(s.split())


def _is_similar(a, b):
    na, nb = _norm(a), _norm(b)
    if not na or not nb:
        return False
    if na == nb or na in nb or nb in na:
        return True
    return difflib.SequenceMatcher(None, na, nb).ratio() >= SIMILAR_THRESHOLD


def dedupe(posts):
    """保留第一筆，後面與已保留者相似的就丟掉。"""
    kept = []
    for p in posts:
        if not any(_is_similar(p["text"], k["text"]) for k in kept):
            kept.append(p)
    return kept


# ── 訊息 & 發送 ─────────────────────────────────────────────────────────────────

def _esc(text):
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def format_message(posts):
    lines = ["📰 <b>外電綜合整理</b>", ""]
    for i, p in enumerate(posts):
        if i:
            lines.append("\n———\n")
        lines.append(_esc(p["text"]))
    return "\n".join(lines)


def send_telegram(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    r = requests.post(url, json={
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    })
    print(f"Telegram: {r.status_code} {r.text[:200]}")


# ── 主程式 ──────────────────────────────────────────────────────────────────────

def collect_matches(items, today):
    """挑出『當日』且含關鍵字的貼文。"""
    matches = []
    for it in items:
        text = (it.get("text") or "").strip()
        if KEYWORD not in text:
            continue
        if post_date_tw(it) != today:
            continue
        matches.append({"text": text, "url": it.get("url", "")})
    return matches


def debug_message(items, matches, today):
    """測試模式：不論今天有無符合貼文，都回報整條管線的狀態。"""
    lines = [
        "🔧 <b>fb_news 測試</b>",
        f"Apify 回傳貼文：{len(items)} 篇",
        f"今日（{today}）含「{KEYWORD}」：{len(matches)} 篇",
    ]
    if items:
        sample = items[0]
        preview = (sample.get("text") or "")[:60].replace("\n", " ")
        lines.append("")
        lines.append(f"最近一篇 日期：{post_date_tw(sample)}")
        lines.append(f"預覽：{_esc(preview)}…")
    if matches:
        lines.append("")
        lines.append("———")
        lines.append(format_message(dedupe(matches)))
    return "\n".join(lines)


def main():
    today = datetime.now(TW_TZ).date()
    try:
        items = fetch_posts()
    except Exception as e:
        print(f"Apify fetch error: {e}")
        if DEBUG:
            send_telegram(f"🔧 <b>fb_news 測試</b>\nApify 抓取失敗：{_esc(str(e))}")
        return
    print(f"Apify returned {len(items)} posts")

    matches = collect_matches(items, today)
    print(f"Matched {len(matches)} '{KEYWORD}' posts for {today}")

    if DEBUG:
        send_telegram(debug_message(items, matches, today))
        print("Debug message sent.")
        return

    if not matches:
        print("No matching posts today — staying silent.")
        return

    merged = dedupe(matches)
    print(f"After dedupe: {len(merged)} posts")
    send_telegram(format_message(merged))
    print("Done.")


if __name__ == "__main__":
    main()
