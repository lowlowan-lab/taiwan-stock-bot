import re
import requests
import os
from bs4 import BeautifulSoup
from datetime import datetime, timezone, timedelta

from market_holidays import is_twse_closed

TW_TZ = timezone(timedelta(hours=8))  # 台灣時區（GitHub Actions 跑在 UTC，需手動 +8）

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
}

def parse_turnover_page():
    """解析 Yahoo 成交金額排行前10筆。

    重點：Yahoo 的漲跌幅文字「不含正負號」，方向是用 CSS class
    c-trend-up / c-trend-down 標示，所以方向一律以 class 為準。
    """
    url = "https://tw.stock.yahoo.com/rank/turnover"
    r = requests.get(url, headers=HEADERS, timeout=30)
    soup = BeautifulSoup(r.text, "html.parser")

    results = []
    rank = 1
    for li in soup.find_all("li", class_=lambda c: c and "List(n)" in c):
        if rank > 10:
            break

        # 漲跌幅 span：文字以 % 結尾；方向看它的 class
        pct_span = next((sp for sp in li.find_all("span")
                         if sp.get_text(strip=True).endswith("%")), None)
        if not pct_span:
            continue
        cls = " ".join(pct_span.get("class") or [])
        direction = 1 if "c-trend-up" in cls else -1 if "c-trend-down" in cls else 0

        texts = list(li.stripped_strings)
        name = next((t for t in texts
                     if any("\u4e00" <= ch <= "\u9fff" for ch in t) and len(t) >= 2), "—")
        code = "—"
        for t in texts:
            m = re.match(r"^(\d{4,6})\.(TWO?|TW)$", t)
            if m:
                code = m.group(1)
                break
        # 成交值（億）：最後一個含小數點的數字
        amount = next((t for t in reversed(texts)
                       if re.match(r"^[\d,]+\.\d+$", t)), "—")

        results.append({
            "rank": rank,
            "name": name,
            "code": code,
            "pct": pct_span.get_text(strip=True),  # 無符號，如 "0.84%"
            "direction": direction,                # 1 漲 / -1 跌 / 0 平
            "amount": amount,
        })
        rank += 1

    return results

def format_turnover_message(rows, now_str):
    if not rows:
        return "❌ 成交金額排行抓取失敗"

    lines = [f"💰 *台股成交金額 TOP 10*", f"🕐 {now_str}", ""]
    lines.append("`#  股名        漲跌幅   成交額`")
    lines.append("`" + "─" * 32 + "`")

    for row in rows:
        rank = f"{row['rank']:2}"
        name = f"{row['name'][:6]:<7}"

        # 漲跌幅：正負號一律以 direction（CSS class）為準，固定寬度
        pct_raw = row["pct"].replace("%", "").replace("+", "").replace("-", "").strip()
        d = row["direction"]
        try:
            pct_val = float(pct_raw)
            sign = "+" if d > 0 else "-" if d < 0 else ""
            pct_str = f"{sign}{pct_val:.1f}%"
        except:
            pct_str = row["pct"]
        pct_display = f"{pct_str:>7}"

        # 成交金額：取整數
        try:
            amount_val = int(float(row["amount"].replace(",", "")))
            amount_str = f"{amount_val}億"
        except:
            amount_str = row["amount"] + "億"
        amount_display = f"{amount_str:>5}"

        lines.append(f"`{rank}. {name}{pct_display} {amount_display}`")

    return "\n".join(lines)

def send_telegram(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "Markdown"
    }
    r = requests.post(url, json=payload)
    print(f"Telegram: {r.status_code} {r.text[:100]}")

def main():
    today = datetime.now(TW_TZ).date()
    if is_twse_closed(today):
        print(f"{today} 台股休市，不推播成交金額排行。")
        return

    now_str = datetime.now(TW_TZ).strftime("%Y/%m/%d %H:%M")
    print(f"Fetching turnover rank at {now_str}...")

    rows = parse_turnover_page()
    print(f"Got {len(rows)} rows")

    msg = format_turnover_message(rows, now_str)
    send_telegram(msg)
    print("Done.")

main()
