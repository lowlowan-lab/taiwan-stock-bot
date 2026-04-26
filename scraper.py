import requests
import os
from bs4 import BeautifulSoup

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

TARGETS = [
    {"label": "🟢 外資買進 TOP10", "url": "https://www.esunsec.com.tw/tw-rank/b2brwd/page/rank/chip/0005"},
    {"label": "🔴 外資賣出 TOP10", "url": "https://www.esunsec.com.tw/tw-rank/b2brwd/page/rank/chip/0006"},
    {"label": "🟡 投信買進 TOP10", "url": "https://www.esunsec.com.tw/tw-rank/b2brwd/page/rank/chip/0015"},
    {"label": "🔴 投信賣出 TOP10", "url": "https://www.esunsec.com.tw/tw-rank/b2brwd/page/rank/chip/0016"},
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
    "Referer": "https://www.esunsec.com.tw/",
}

def scrape_table(url):
    r = requests.get(url, headers=HEADERS, timeout=30)
    soup = BeautifulSoup(r.text, "html.parser")
    table = soup.find("table")
    if not table:
        return None
    rows = table.find_all("tr")[1:11]  # 前10筆，跳過 header
    results = []
    for row in rows:
        cols = [td.get_text(strip=True) for td in row.find_all("td")]
        if cols:
            results.append(cols)
    return results

def format_message(label, rows):
    if not rows:
        return f"{label}\n❌ 無資料（可能是動態渲染）"
    lines = [f"*{label}*"]
    for i, row in enumerate(rows, 1):
        code = row[0] if len(row) > 0 else ""
        name = row[1][:6] if len(row) > 1 else ""
        amount = row[-1] if len(row) > 2 else ""
        lines.append(f"`{i:2}. {code:<6} {name:<8} {amount:>10}`")
    return "\n".join(lines)

def send_telegram(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "Markdown"
    }
    r = requests.post(url, json=payload)
    print(f"Telegram response: {r.status_code} {r.text}")

def main():
    messages = ["📊 *三大法人買賣超排行*（今日）\n"]
    for target in TARGETS:
        print(f"Scraping: {target['label']}")
        try:
            rows = scrape_table(target["url"])
            msg = format_message(target["label"], rows)
            messages.append(msg)
        except Exception as e:
            messages.append(f"❌ {target['label']} 抓取失敗：{e}")
    full_message = "\n\n".join(messages)
    send_telegram(full_message)
    print("Done.")

main()