import requests
import os
from datetime import datetime

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
    "Referer": "https://www.esunsec.com.tw/",
}

TARGETS = [
    {
        "label": "🟢 外資買進 TOP10",
        "url": "https://sjis.esunsec.com.tw/b2brwdCommon/jsondata/0c/0f/17/twstockdata.xdjjson",
        "params": {"a": "b", "b": "C", "d": "50", "x": "rank-chip0005-1", "c": "1"}
    },
    {
        "label": "🔴 外資賣出 TOP10",
        "url": "https://sjis.esunsec.com.tw/b2brwdCommon/jsondata/4a/cf/09/twstockdata.xdjjson",
        "params": {"a": "b", "b": "T", "d": "50", "x": "rank-chip0005-1", "c": "1"}
    },
    {
        "label": "🟡 投信買進 TOP10",
        "url": "https://sjis.esunsec.com.tw/b2brwdCommon/jsondata/09/58/89/twstockdata.xdjjson",
        "params": {"a": "b", "b": "B", "d": "50", "x": "rank-chip0013-1", "c": "1"}
    },
    {
        "label": "🟠 投信賣出 TOP10",
        "url": "https://sjis.esunsec.com.tw/b2brwdCommon/jsondata/e3/f2/5a/twstockdata.xdjjson",
        "params": {"a": "b", "b": "T", "d": "50", "x": "rank-chip0013-1", "c": "1"}
    },
]

def fetch_data(url, params):
    r = requests.get(url, params=params, headers=HEADERS, timeout=30)
    data = r.json()
    return data["ResultSet"]["Result"][:10]

def format_message(label, rows):
    lines = [f"*{label}*"]
    lines.append("`#   代碼    名稱       買賣超(千)`")
    lines.append("`" + "─" * 34 + "`")
    for i, row in enumerate(rows, 1):
        code = row["V2"].replace("AS", "").replace("AP", "")
        name = row["V3"][:6]
        amount = f"{int(row['V9']):,}"
        change = float(row["V5"])
        arrow = "▲" if change > 0 else "▼" if change < 0 else "－"
        lines.append(f"`{i:2}. {code:<6} {name:<7} {arrow} {amount:>10}`")
    return "\n".join(lines)

def send_telegram(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "Markdown"
    }
    r = requests.post(url, json=payload)
    print(f"Telegram: {r.status_code}")

def main():
    date_str = datetime.now().strftime("%Y/%m/%d")
    messages = [f"📊 *三大法人買賣超排行*\n🗓 {date_str}"]

    for target in TARGETS:
        print(f"Fetching: {target['label']}")
        try:
            rows = fetch_data(target["url"], target["params"])
            msg = format_message(target["label"], rows)
            messages.append(msg)
        except Exception as e:
            messages.append(f"❌ {target['label']} 失敗：{e}")

    send_telegram("\n\n".join(messages))
    print("Done.")

main()