import requests
import os
from bs4 import BeautifulSoup
from datetime import datetime

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
}

def fetch_turnover_rank():
    url = "https://tw.stock.yahoo.com/rank/turnover"
    r = requests.get(url, headers=HEADERS, timeout=30)
    soup = BeautifulSoup(r.text, "html.parser")

    items = soup.select("ul.M\\(0\\) li")
    if not items:
        # 備用選擇器
        items = soup.select("li.List\\(n\\)")

    results = []
    for item in items[:10]:
        try:
            # 股名
            name_el = item.select_one("div.Fw\\(b\\)")
            name = name_el.text.strip() if name_el else "—"

            # 股號
            code_el = item.select_one("span.C\\(\\$c-link-color\\)")
            code = code_el.text.strip().replace(".TW", "").replace(".TWO", "") if code_el else "—"

            # 所有數字欄位
            spans = item.select("span.Fz\\(16px\\), li span")
            numbers = [s.text.strip() for s in item.select("span") if s.text.strip()]

            # 漲跌幅
            change_pct_el = item.select_one("span[class*='Bgc']")

            # 直接抓所有文字節點
            texts = [t.strip() for t in item.stripped_strings]

            results.append({
                "name": name,
                "code": code,
                "texts": texts,
            })
        except Exception as e:
            continue

    return results

def fetch_turnover_data():
    """用更直接的方式解析 Yahoo 成交金額排行"""
    url = "https://tw.stock.yahoo.com/rank/turnover"
    r = requests.get(url, headers=HEADERS, timeout=30)
    soup = BeautifulSoup(r.text, "html.parser")

    results = []
    # Yahoo Finance 的排行榜用 li 結構
    rows = soup.find_all("li", class_=lambda c: c and "List(n)" in c)

    if not rows:
        # 嘗試找所有 li 裡有股票名稱的
        rows = soup.find_all("li")

    for row in rows[:10]:
        text_nodes = list(row.stripped_strings)
        if len(text_nodes) < 5:
            continue

        # 過濾掉不是股票資料的 li
        # 股票資料通常第一個是排名數字或股名
        try:
            # 找漲跌幅（含 % 符號）
            pct = next((t for t in text_nodes if "%" in t), "—")
            # 找成交金額（含小數點，通常最後幾個）
            amounts = [t for t in text_nodes if "." in t and t.replace(".", "").replace(",", "").isdigit()]
            amount = amounts[-1] if amounts else "—"
            # 名稱通常是中文
            name = next((t for t in text_nodes if any("\u4e00" <= c <= "\u9fff" for c in t)), "—")
            # 代碼
            code = next((t for t in text_nodes if t.replace(".", "").replace("TW", "").replace("TWO", "").isdigit()), "—")
            code = code.replace(".TW", "").replace(".TWO", "")

            results.append({
                "name": name,
                "code": code,
                "change_pct": pct,
                "amount": amount,
            })
        except:
            continue

    return results

def parse_turnover_page():
    """解析成交金額排行，回傳前10筆"""
    url = "https://tw.stock.yahoo.com/rank/turnover"
    r = requests.get(url, headers=HEADERS, timeout=30)
    soup = BeautifulSoup(r.text, "html.parser")

    results = []
    rank = 1

    # Yahoo 用 ul > li 結構，每個 li 是一支股票
    # 找包含股票資料的 li（至少含有中文名稱和百分比）
    all_li = soup.find_all("li")

    for li in all_li:
        if rank > 10:
            break
        text_list = list(li.stripped_strings)
        full_text = " ".join(text_list)

        # 有中文 + 有 % + 有數字 = 股票資料列
        has_chinese = any("\u4e00" <= c <= "\u9fff" for c in full_text)
        has_pct = "%" in full_text

        if not (has_chinese and has_pct):
            continue
        if len(text_list) < 6:
            continue

        try:
            # 找股名（純中文）
            name = next((t for t in text_list if any("\u4e00" <= c <= "\u9fff" for c in t) and len(t) >= 2), "—")
            # 找代碼（純數字4碼）
            code = next((t for t in text_list if t.isdigit() and len(t) == 4), "—")
            # 找漲跌幅（含%）
            pct = next((t for t in text_list if "%" in t), "—")
            # 找成交金額億（通常是最後一個含小數的數字）
            decimals = [t for t in text_list if "." in t and len(t) <= 10]
            amount = decimals[-1] if decimals else "—"

            # 判斷漲跌方向
            is_up = "+" in pct or (pct != "—" and not pct.startswith("-") and pct != "0.00%")
            arrow = "🔺" if is_up else "🔻" if pct.startswith("-") else "▪️"

            results.append({
                "rank": rank,
                "name": name,
                "code": code,
                "change_pct": pct,
                "amount": amount,
                "arrow": arrow,
            })
            rank += 1
        except:
            continue

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

        # 漲跌幅：小數一位，固定寬度
        pct_raw = row["change_pct"].replace("%", "").replace("+", "").strip()
        try:
            pct_val = float(pct_raw)
            sign = "+" if pct_val > 0 else ""
            pct_str = f"{sign}{pct_val:.1f}%"
        except:
            pct_str = row["change_pct"]
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
    now_str = datetime.now().strftime("%Y/%m/%d %H:%M")
    print(f"Fetching turnover rank at {now_str}...")

    rows = parse_turnover_page()
    print(f"Got {len(rows)} rows")

    msg = format_turnover_message(rows, now_str)
    send_telegram(msg)
    print("Done.")

main()
