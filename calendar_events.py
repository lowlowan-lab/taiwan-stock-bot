import os
import re
import requests
from datetime import datetime, timedelta
from bs4 import BeautifulSoup

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

WEEKDAYS = ["一", "二", "三", "四", "五", "六", "日"]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36"
    ),
}

# 玉山證理財行事曆 JSON API（與 scraper.py 同一伺服器）
CALENDAR_JSON_URL = "https://sjis.esunsec.com.tw/b2brwdCommon/jsondata/0e/e7/d3/twstockdata.xdjjson"
CALENDAR_TAGID = "afterhours-bulletin0004-3"

# 來源事件名稱 → 顯示名稱（只保留這兩種）
EVENT_TYPE_MAP = {
    "法人說明會": "法說會",
    "股東會": "股東會",
}

# TAIFEX 指數成分股權重（市值排名 proxy）
TWSE_WEIGHT_URL = "https://www.taifex.com.tw/cht/9/futuresQADetail"
TPEX_WEIGHT_URL = "https://www.taifex.com.tw/cht/2/tPEXPropertion"
TWSE_TOP_N = 200
TPEX_TOP_N = 100


# ── 市值排名 (上市200 + 上櫃100) ───────────────────────────────────────────────

def _scrape_taifex_weights(url, top_n, label):
    """
    Parse a TAIFEX index-weight page. Each table row holds two side-by-side
    entries: [rank, code, name, weight%, rank, code, name, weight%].
    Extract all (code, weight) pairs, sort by weight desc, return top-N codes.
    """
    codes = set()
    try:
        r = requests.get(url, headers=HEADERS, timeout=30)
        r.raise_for_status()
        soup = BeautifulSoup(r.content, "html.parser")

        pairs = []  # (weight, code)
        for row in soup.select("table tr"):
            cells = [c.get_text(strip=True) for c in row.find_all(["td", "th"])]
            row_codes = [c for c in cells if re.fullmatch(r"\d{4}", c)]
            row_weights = []
            for c in cells:
                cc = c.replace(",", "")
                if cc.endswith("%") and re.fullmatch(r"\d+(\.\d+)?", cc[:-1]):
                    row_weights.append(float(cc[:-1]))
            for code, weight in zip(row_codes, row_weights):
                pairs.append((weight, code))

        pairs.sort(key=lambda x: x[0], reverse=True)
        codes = {code for _, code in pairs[:top_n]}
        top5 = [c for _, c in pairs[:5]]
        print(f"{label}: {len(pairs)} stocks ranked, top-{top_n} selected, top5={top5}")
    except Exception as e:
        print(f"{label} weight fetch error: {e}")
    return codes


def fetch_top_stocks():
    """上市市值前200 + 上櫃市值前100。"""
    codes = set()
    codes |= _scrape_taifex_weights(TWSE_WEIGHT_URL, TWSE_TOP_N, "TWSE")
    codes |= _scrape_taifex_weights(TPEX_WEIGHT_URL, TPEX_TOP_N, "TPEX")
    print(f"Filter set total: {len(codes)} codes")
    return codes


# ── 行事曆事件 (法說會 / 股東會) ────────────────────────────────────────────────

def fetch_calendar_events(today, cutoff):
    """直接打玉山證 JSON API 取得未來七天事件。"""
    params = {
        "x": CALENDAR_TAGID,
        "a": today.strftime("%Y/%m/%d"),
        "b": cutoff.strftime("%Y/%m/%d"),
    }
    events = []
    try:
        r = requests.get(CALENDAR_JSON_URL, params=params, headers=HEADERS, timeout=30)
        r.encoding = "utf-8"
        data = r.json()
        rows = data["ResultSet"]["Result"]
        print(f"Calendar API: {len(rows)} total rows")

        for row in rows:
            src_type = row.get("V4", "").strip()
            if src_type not in EVENT_TYPE_MAP:
                continue
            event_type = EVENT_TYPE_MAP[src_type]

            date_str = row.get("V1", "").strip()
            try:
                ev_date = datetime.strptime(date_str, "%Y/%m/%d").date()
            except ValueError:
                continue
            if not (today <= ev_date <= cutoff):
                continue

            code_m = re.search(r"\d{4,6}", row.get("V2", ""))
            if not code_m:
                continue
            code = code_m.group(0)
            name = row.get("V3", "").strip()

            events.append({
                "date": ev_date,
                "code": code,
                "name": name,
                "event_type": event_type,
            })

        print(f"After type/date filter: {len(events)} events")
    except Exception as e:
        print(f"Calendar fetch error: {e}")
    return events


# ── 訊息格式 & 發送 ─────────────────────────────────────────────────────────────

def _esc(text):
    """Escape HTML special chars for Telegram HTML parse mode."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def format_message(events_by_date):
    today = datetime.now().date()
    date_str = today.strftime("%Y/%m/%d")

    lines = [
        "<b>台股行事曆 — 法說會 / 股東會</b>",
        f"（未來 7 天，市值前 300）  {date_str}",
        "",
    ]

    if not events_by_date:
        lines.append("未來 7 天內無符合條件的法說會或股東會。")
        return "\n".join(lines)

    for date in sorted(events_by_date):
        wd = WEEKDAYS[date.weekday()]
        lines.append(f"<b>{date.strftime('%m/%d')}（週{wd}）</b>")

        by_type: dict = {}
        for ev in events_by_date[date]:
            by_type.setdefault(ev["event_type"], []).append(ev)

        for etype in sorted(by_type):
            lines.append(f"  ▎{etype}")
            for ev in sorted(by_type[etype], key=lambda x: x["code"]):
                name = f" {_esc(ev['name'])}" if ev["name"] else ""
                lines.append(f"  <code>{ev['code']}</code>{name}")

        lines.append("")

    return "\n".join(lines).rstrip()


def send_telegram(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    r = requests.post(url, json={
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
    })
    print(f"Telegram: {r.status_code} {r.text[:200]}")


# ── 主程式 ──────────────────────────────────────────────────────────────────────

def main():
    today = datetime.now().date()
    cutoff = today + timedelta(days=7)

    print("Fetching market cap ranking (上市200 + 上櫃100)...")
    top_stocks = fetch_top_stocks()

    print("Fetching calendar events...")
    events = fetch_calendar_events(today, cutoff)

    filtered = [ev for ev in events if not top_stocks or ev["code"] in top_stocks]
    print(f"Final filtered events: {len(filtered)}")

    events_by_date: dict = {}
    for ev in filtered:
        events_by_date.setdefault(ev["date"], []).append(ev)

    msg = format_message(events_by_date)
    print(msg)
    send_telegram(msg)
    print("Done.")


main()
