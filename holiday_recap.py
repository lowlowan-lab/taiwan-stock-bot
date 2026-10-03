"""台股連假・國際股市前瞻推播。

每天晚上 19:00（台灣）由 cron 觸發自我判斷：
  若「今天台股休市、明天台股開盤、且這段休市是連假（含國定假日、連續≥2天）」，
  就推播連假期間各國指數自台股最後收盤起的累計漲跌幅；否則保持安靜。

指數（Yahoo Finance，免費）：道瓊 ^DJI、NASDAQ ^IXIC、費半 ^SOX、
  KOSPI ^KS11、TOPIX（用 1306.T ETF 代理，Yahoo 無指數本身）、日經225 ^N225。
"""
import os
import requests
from datetime import datetime, timezone, timedelta

from market_holidays import is_twse_closed, twse_holidays

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

TW_TZ = timezone(timedelta(hours=8))
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
}

# 顯示順序：美股 → 韓股 → 日股
INDICES = [
    ("^DJI", "道瓊"),
    ("^IXIC", "NASDAQ"),
    ("^SOX", "費半SOX"),
    ("^KS11", "韓國KOSPI"),
    ("1306.T", "日本TOPIX"),   # TOPIX ETF 代理
    ("^N225", "日經225"),
]


# ── 連假偵測 ────────────────────────────────────────────────────────────────────

def holiday_streak(today):
    """若今天是連假的最後一天（明天開盤），回傳 (休市起始日, 最後交易日)；否則 None。

    條件：今天台股休市、明天台股開盤、休市連續 ≥2 天且含至少一個國定假日。
    """
    if not is_twse_closed(today):
        return None
    if is_twse_closed(today + timedelta(days=1)):
        return None  # 明天還沒開盤 → 還不是前一晚

    # 往回數連續休市日
    start = today
    while is_twse_closed(start - timedelta(days=1)):
        start -= timedelta(days=1)
    streak_days = (today - start).days + 1
    has_holiday = any(
        (start + timedelta(days=i)) in twse_holidays((start + timedelta(days=i)).year)
        for i in range(streak_days)
    )
    if streak_days < 2 or not has_holiday:
        return None  # 純週末或單日假 → 不算連假
    return start, start - timedelta(days=1)


# ── 指數報價（Yahoo chart）──────────────────────────────────────────────────────

def fetch_cum_change(symbol, baseline_date):
    """回傳 (累計漲跌幅%, 基準日, 最新日)；抓不到回 None。

    基準＝baseline_date（含）當天或之前最後一根日K收盤；最新＝序列最後一根收盤。
    """
    try:
        r = requests.get(
            f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}",
            params={"interval": "1d", "range": "1mo"}, headers=HEADERS, timeout=30,
        )
        res = r.json()["chart"]["result"][0]
        off = res["meta"].get("gmtoffset", 0)
        ts = res["timestamp"]
        closes = res["indicators"]["quote"][0]["close"]
        bars = []
        for t, c in zip(ts, closes):
            if c is None:
                continue
            d = datetime.utcfromtimestamp(t + off).date()
            bars.append((d, c))
        if not bars:
            return None
        base = [c for d, c in bars if d <= baseline_date]
        if not base:
            return None
        base_close = base[-1]
        base_date = [d for d, c in bars if d <= baseline_date][-1]
        last_date, last_close = bars[-1]
        pct = (last_close / base_close - 1) * 100
        return pct, base_date, last_date
    except Exception as e:
        print(f"{symbol} fetch error: {e}")
        return None


# ── 訊息 & 發送 ─────────────────────────────────────────────────────────────────

def _vwidth(s):
    """視覺寬度：CJK 全形字算 2。"""
    return sum(2 if ord(c) > 0x2E80 else 1 for c in s)


def _vpad(s, width):
    return s + " " * max(0, width - _vwidth(s))


def build_message(last_trading_day, rows):
    head = (f"📊 <b>台股開盤前・連假國際行情</b>\n"
            f"累計漲跌（自 {last_trading_day:%m/%d} 台股收盤起）\n")
    w = max(_vwidth(label) for label, _ in rows) + 1
    lines = [head]
    for label, pct in rows:
        val = "    —" if pct is None else f"{pct:+6.2f}%"
        lines.append(f"<code>{_vpad(label, w)}{val}</code>")
    return "\n".join(lines)


def send_telegram(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    r = requests.post(url, json={
        "chat_id": TELEGRAM_CHAT_ID, "text": text,
        "parse_mode": "HTML", "disable_web_page_preview": True,
    })
    print(f"Telegram: {r.status_code} {r.text[:200]}")


def main():
    today = datetime.now(TW_TZ).date()
    streak = holiday_streak(today)
    if not streak:
        print(f"{today} 非連假前一晚，保持安靜。")
        return
    _, last_trading_day = streak
    print(f"連假偵測：台股最後交易日 {last_trading_day}，明日開盤。")

    rows = []
    for symbol, label in INDICES:
        got = fetch_cum_change(symbol, last_trading_day)
        rows.append((label, got[0] if got else None))
        if got:
            print(f"  {label}: {got[0]:+.2f}% ({got[1]}→{got[2]})")

    send_telegram(build_message(last_trading_day, rows))
    print("Done.")


if __name__ == "__main__":
    main()
