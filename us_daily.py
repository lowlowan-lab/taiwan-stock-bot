"""美股每日總結推播。

台灣週一~週五早上 07:00 跑（GitHub Actions cron，UTC 23:00 = 台灣隔天 07:00）。
週一 recap 美股週五場，其餘各日 recap 前一個美股交易日。
此時美股當日已收盤；盤後（延長交易至美東 20:00）在台灣早上 7 點可能尚未完全結束，
盤後漲跌幅取當下值。

內容：
  1. us_watchlist.txt 內每一檔的「當日漲跌幅」「盤後漲跌幅」
  2. 任一檔當日或盤後漲跌超過 ±3% → 抓近期新聞，交給 Claude 濃縮成一句中文原因

資料來源：
  - 報價：Yahoo Finance v7/finance/quote（需 cookie + crumb 授權）
  - 新聞：Yahoo Finance v1/finance/search
  - 漲跌原因摘要：Claude API（需 ANTHROPIC_API_KEY；沒設或失敗則退回顯示原始標題）
"""
import os
import re
import html
import json
import requests
from datetime import datetime, timezone, timedelta

TW_TZ = timezone(timedelta(hours=8))  # 台灣時區（Actions 跑在 UTC，需手動 +8）

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")  # 可選；缺則退回原始標題

# 漲跌超過這個門檻（%）就去抓新聞當原因
MOVE_THRESHOLD = 3.0
CLAUDE_MODEL = "claude-opus-4-8"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
}

WATCHLIST_FILE = os.path.join(os.path.dirname(__file__), "us_watchlist.txt")


# ────────────────────────────── 自選清單 ──────────────────────────────
def parse_watchlist():
    """讀 us_watchlist.txt，回傳依分類分組的清單。

    格式：`# 分類名` 當作群組標題，`TICKER,公司名稱` 當作一檔。
    回傳 [(分類, [(ticker, 名稱), ...]), ...]，保留檔案順序。
    """
    groups = []
    current = "美股自選"
    bucket = []
    with open(WATCHLIST_FILE, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            if line.startswith("#"):
                label = line.lstrip("#").strip()
                # 跳過檔頭的說明註解（沒有股票的純說明行）
                if label and "格式" not in label and "增減" not in label:
                    if bucket:
                        groups.append((current, bucket))
                        bucket = []
                    current = label
                continue
            ticker, _, name = line.partition(",")
            ticker = ticker.strip().upper()
            name = name.strip() or ticker
            if ticker:
                bucket.append((ticker, name))
    if bucket:
        groups.append((current, bucket))
    return groups


# ────────────────────────────── Yahoo 授權 ──────────────────────────────
def yahoo_session():
    """建立帶 cookie 的 session 並取得 crumb（v7 quote 需要）。"""
    s = requests.Session()
    s.headers.update(HEADERS)
    s.get("https://fc.yahoo.com", timeout=30)
    crumb = s.get(
        "https://query1.finance.yahoo.com/v1/test/getcrumb", timeout=30
    ).text.strip()
    return s, crumb


def fetch_quotes(session, crumb, tickers):
    """批次抓報價，回傳 {ticker: {reg, post, price, state, time}}。"""
    out = {}
    for i in range(0, len(tickers), 50):  # 一次最多 50 檔，避免 URL 過長
        chunk = tickers[i:i + 50]
        r = session.get(
            "https://query1.finance.yahoo.com/v7/finance/quote",
            params={"symbols": ",".join(chunk), "crumb": crumb},
            timeout=30,
        )
        for q in r.json().get("quoteResponse", {}).get("result", []):
            out[q["symbol"]] = {
                "reg": q.get("regularMarketChangePercent"),
                "post": q.get("postMarketChangePercent"),
                "price": q.get("regularMarketPrice"),
                "state": q.get("marketState"),
                "time": q.get("regularMarketTime"),
            }
    return out


def fetch_quote_fallback(session, ticker):
    """v7 拿不到時，用免授權的 chart 端點補當日漲跌幅（盤後不可得）。"""
    try:
        r = session.get(
            f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}",
            params={"interval": "1d", "range": "1d"}, timeout=30,
        )
        m = r.json()["chart"]["result"][0]["meta"]
        price = m.get("regularMarketPrice")
        prev = m.get("previousClose") or m.get("chartPreviousClose")
        reg = (price - prev) / prev * 100 if price and prev else None
        return {"reg": reg, "post": None, "price": price,
                "state": m.get("marketState"), "time": m.get("regularMarketTime")}
    except Exception:
        return {"reg": None, "post": None, "price": None, "state": None, "time": None}


# ────────────────────────────── 新聞原因 ──────────────────────────────
def fetch_reason(session, ticker, max_items=4):
    """抓該檔近期新聞標題，回傳 [(標題, 媒體), ...]，只取 3 天內。"""
    try:
        r = session.get(
            "https://query1.finance.yahoo.com/v1/finance/search",
            params={"q": ticker, "newsCount": 8, "quotesCount": 0,
                    "enableFuzzyQuery": "false"},
            timeout=30,
        )
        news = r.json().get("news", [])
    except Exception:
        return []

    cutoff = datetime.now(timezone.utc).timestamp() - 3 * 86400
    items = []
    for n in news:
        ts = n.get("providerPublishTime", 0)
        if ts and ts < cutoff:
            continue
        title = html.unescape(n.get("title", "")).strip()
        if title:
            items.append((title, n.get("publisher", "")))
        if len(items) >= max_items:
            break
    return items


# ────────────────────────────── 格式化 ──────────────────────────────
def fmt_pct(v):
    """漲跌幅 → 上漲 +、下跌 -，固定寬度 7。"""
    if v is None:
        return f"{'—':>7}"
    sign = "+" if v > 0 else "-" if v < 0 else " "
    return f"{sign}{abs(v):.2f}%".rjust(7)


def is_mover(q):
    """當日或盤後任一 |漲跌| ≥ 門檻 → True。"""
    for v in (q.get("reg"), q.get("post")):
        if v is not None and abs(v) >= MOVE_THRESHOLD:
            return True
    return False


def build_summary(groups, quotes, date_str):
    lines = ["🇺🇸 *美股每日總結*", f"🗓 {date_str}（收盤＋盤後）", ""]
    lines.append("`代碼    當日      盤後`")
    lines.append("`" + "─" * 26 + "`")
    for cat, stocks in groups:
        lines.append(f"*{cat}*")
        for ticker, _name in stocks:
            q = quotes.get(ticker, {})
            flag = "🔥" if is_mover(q) else "  "
            lines.append(f"`{ticker:<6}{fmt_pct(q.get('reg'))} {fmt_pct(q.get('post'))}`{flag}")
        lines.append("")
    lines.append("🔥 = 當日或盤後漲跌 ≥ 3%")
    return "\n".join(lines).strip()


def summarize_reasons(items):
    """用 Claude 把每檔的新聞標題濃縮成一句中文漲跌原因。

    items: [{"ticker", "name", "reg", "post", "headlines": [標題,...]}, ...]
    回傳 {ticker: 一句原因}，原因為空字串代表新聞與當日漲跌無明確關聯（略過不列）。
    沒設 API key 或呼叫失敗則回傳 {}（由呼叫端退回原始標題）。
    """
    if not ANTHROPIC_API_KEY:
        print("No ANTHROPIC_API_KEY — 退回顯示原始標題")
        return {}
    try:
        import anthropic
    except ImportError:
        print("anthropic 套件未安裝 — 退回顯示原始標題")
        return {}

    payload = [
        {
            "ticker": it["ticker"],
            "name": it["name"],
            "change": f"當日 {it['reg']:+.2f}%" if it["reg"] is not None else "",
            "headlines": it["headlines"],
        }
        for it in items
    ]
    prompt = (
        "你是美股財經分析助理。以下是今天漲跌超過 3% 的個股，附上各自近期新聞標題。\n"
        "請逐檔判斷：這些新聞裡有沒有「明確且具體」解釋它今天漲跌的原因"
        "（例如財報、財測、分析師升/降評、購併、產業利多/利空、訴訟、產品發表、"
        "獲得大訂單等）。\n"
        "- 有：消化新聞後，用一句繁體中文（35 字內）總結那個原因，不要列標題、不要分點。\n"
        "- 沒有（新聞只是泛泛的估值/歷史回顧分析、與當日漲跌無明確關聯、或資訊不足"
        "以判斷原因）：reason 直接留空字串 \"\"，代表這檔略過不列。\n"
        "只根據提供的標題判斷，不要杜撰具體數字或不存在的事件。寧可留空，也不要硬湊。\n\n"
        f"資料：\n{json.dumps(payload, ensure_ascii=False, indent=2)}"
    )
    schema = {
        "type": "object",
        "properties": {
            "reasons": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "ticker": {"type": "string"},
                        "reason": {"type": "string"},
                    },
                    "required": ["ticker", "reason"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["reasons"],
        "additionalProperties": False,
    }
    try:
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        resp = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=2000,
            output_config={
                "effort": "low",  # 單純摘要，不需深度思考
                "format": {"type": "json_schema", "schema": schema},
            },
            messages=[{"role": "user", "content": prompt}],
        )
        text = next(b.text for b in resp.content if b.type == "text")
        data = json.loads(text)
        return {r["ticker"]: r["reason"] for r in data.get("reasons", [])}
    except Exception as e:
        print(f"Claude 摘要失敗，退回原始標題：{e}")
        return {}


def build_reasons(groups, quotes, session):
    """組出異動原因區塊；沒有任何異動回傳 None。"""
    name_of = {t: n for _, stocks in groups for t, n in stocks}
    movers = [(t, q) for t, q in quotes.items() if is_mover(q)]
    if not movers:
        return None
    # 依當日漲跌幅絕對值由大到小排
    movers.sort(key=lambda x: abs(x[1].get("reg") or 0), reverse=True)

    # 先抓每檔新聞，再一次交給 Claude 濃縮成原因
    news = {t: fetch_reason(session, t) for t, _ in movers}
    summary_input = [
        {
            "ticker": t,
            "name": name_of.get(t, t),
            "reg": q.get("reg"),
            "post": q.get("post"),
            "headlines": [title for title, _pub in news[t]],
        }
        for t, q in movers
    ]
    reasons = summarize_reasons(summary_input)
    llm_mode = bool(reasons)  # 有回傳代表 Claude 有跑（含「略過」的空字串）

    lines = ["📌 *異動原因（漲跌 ≥ 3%）*", ""]
    listed = 0
    for ticker, q in movers:
        name = name_of.get(ticker, ticker)
        parts = []
        if q.get("reg") is not None:
            parts.append(f"當日 {q['reg']:+.2f}%")
        if q.get("post") is not None and abs(q["post"]) >= MOVE_THRESHOLD:
            parts.append(f"盤後 {q['post']:+.2f}%")
        header = f"*{ticker}* {name}　{'｜'.join(parts)}"

        if llm_mode:
            reason = reasons.get(ticker, "").strip()
            if not reason:
                continue  # 新聞與當日漲跌無明確關聯 → 不列出
            lines.append(header)
            lines.append(f"  → {reason}")
        else:
            # 無 API key／呼叫失敗 → 退回原始新聞標題
            lines.append(header)
            if news[ticker]:
                for title, pub in news[ticker]:
                    pub_str = f" — {pub}" if pub else ""
                    lines.append(f"  ・{title}{pub_str}")
            else:
                lines.append("  ・（查無近期相關新聞）")
        lines.append("")
        listed += 1

    if listed == 0:
        return None  # 全部都與當日漲跌無明確關聯
    return "\n".join(lines).strip()


# ────────────────────────────── 推播 ──────────────────────────────
def _chunks(text, limit=3800):
    """依行切段，避免超過 Telegram 4096 字上限。"""
    out, buf = [], ""
    for line in text.split("\n"):
        if len(buf) + len(line) + 1 > limit and buf:
            out.append(buf)
            buf = ""
        buf += line + "\n"
    if buf.strip():
        out.append(buf)
    return out


def send_telegram(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    for part in _chunks(text):
        r = requests.post(url, json={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": part,
            "parse_mode": "Markdown",
            "disable_web_page_preview": True,
        })
        print(f"Telegram: {r.status_code} {r.text[:120]}")


def main():
    now_str = datetime.now(TW_TZ).strftime("%Y/%m/%d %H:%M")
    print(f"US daily report at {now_str}")

    groups = parse_watchlist()
    tickers = [t for _, stocks in groups for t, _ in stocks]
    print(f"Watchlist: {len(tickers)} tickers")

    try:
        session, crumb = yahoo_session()
        print(f"Got crumb: {crumb[:6]}...")
        quotes = fetch_quotes(session, crumb, tickers)
    except Exception as e:
        print(f"Quote bulk failed: {e}")
        session = requests.Session()
        session.headers.update(HEADERS)
        quotes = {}

    # 補抓缺漏（含 crumb 失敗整批落空的情況）
    missing = [t for t in tickers if t not in quotes or quotes[t].get("reg") is None]
    for t in missing:
        quotes[t] = fetch_quote_fallback(session, t)
    print(f"Got quotes for {sum(1 for t in tickers if quotes.get(t, {}).get('reg') is not None)}/{len(tickers)}")

    summary = build_summary(groups, quotes, now_str)
    send_telegram(summary)

    reasons = build_reasons(groups, quotes, session)
    if reasons:
        send_telegram(reasons)
    print("Done.")


if __name__ == "__main__":
    main()
