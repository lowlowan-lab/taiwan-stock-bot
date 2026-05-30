import os
import re
import requests
from datetime import datetime, timedelta
from bs4 import BeautifulSoup

WATCHLIST_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "us_watchlist.txt")

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

# Nasdaq 官方財報行事曆 API（逐日查詢，免金鑰）
US_EARNINGS_URL = "https://api.nasdaq.com/api/calendar/earnings"
US_TIMING_MAP = {
    "time-pre-market": "盤前",
    "time-after-hours": "盤後",
}

# 雲達期貨交易行事曆 API（台指期結算日）
YUANTA_CAL_URL = "https://www.yuantafutures.com.tw/api/TradeCal01"
TX_ROW_PREFIX = "大台指期貨(TX)"

# 事件類型顯示順序
EVENT_TYPE_ORDER = ["流動性事件", "法說會", "股東會", "美股財報"]


# ── 市值排名 (上市200 + 上櫃100) ───────────────────────────────────────────────

def _scrape_taifex_weights(url, top_n, label):
    """
    Parse a TAIFEX index-weight page. Each table row holds two side-by-side
    entries: [rank, code, name, weight%, rank, code, name, weight%].
    Returns top-N codes ordered by weight desc (biggest market cap first).
    """
    ordered = []
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
        ordered = [code for _, code in pairs[:top_n]]
        print(f"{label}: {len(pairs)} stocks ranked, top-{top_n} selected, top5={ordered[:5]}")
    except Exception as e:
        print(f"{label} weight fetch error: {e}")
    return ordered


def fetch_top_stocks():
    """
    上市市值前200 + 上櫃市值前100。
    Returns {code: rank} where smaller rank = bigger market cap.
    TWSE stocks rank 1..200, TPEX stocks rank 201.. (TWSE caps generally larger).
    """
    rank_map = {}
    twse = _scrape_taifex_weights(TWSE_WEIGHT_URL, TWSE_TOP_N, "TWSE")
    tpex = _scrape_taifex_weights(TPEX_WEIGHT_URL, TPEX_TOP_N, "TPEX")
    for i, code in enumerate(twse):
        rank_map.setdefault(code, i)
    for i, code in enumerate(tpex):
        rank_map.setdefault(code, TWSE_TOP_N + i)
    print(f"Filter set total: {len(rank_map)} codes")
    return rank_map


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


# ── 美股財報 (investing.com，僅自選清單) ────────────────────────────────────────

def load_watchlist():
    """讀取 us_watchlist.txt，回傳 {ticker: (name, order)}，order 用於排序。"""
    watchlist = {}
    try:
        with open(WATCHLIST_FILE, encoding="utf-8") as f:
            order = 0
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split(",", 1)
                ticker = parts[0].strip().upper()
                name = parts[1].strip() if len(parts) > 1 else ticker
                if ticker:
                    watchlist[ticker] = (name, order)
                    order += 1
        print(f"Watchlist: {len(watchlist)} tickers loaded")
    except Exception as e:
        print(f"Watchlist load error: {e}")
    return watchlist


def fetch_us_earnings(today, cutoff, watchlist):
    """逐日打 Nasdaq API 取未來七天美股財報，只保留自選清單內的股票。"""
    events = []
    if not watchlist:
        return events
    headers = {
        **HEADERS,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "Origin": "https://www.nasdaq.com",
        "Referer": "https://www.nasdaq.com/",
    }

    total = 0
    day = today
    while day <= cutoff:
        try:
            r = requests.get(
                US_EARNINGS_URL,
                params={"date": day.strftime("%Y-%m-%d")},
                headers=headers,
                timeout=30,
            )
            r.raise_for_status()
            rows = (r.json().get("data") or {}).get("rows") or []
            total += len(rows)
            for row in rows:
                ticker = (row.get("symbol") or "").strip().upper()
                if ticker not in watchlist:
                    continue
                name, order = watchlist[ticker]
                timing = US_TIMING_MAP.get((row.get("time") or "").strip(), "")
                events.append({
                    "date": day,
                    "code": ticker,
                    "name": name,
                    "event_type": "美股財報",
                    "rank": order,
                    "timing": timing,
                })
        except Exception as e:
            print(f"US earnings fetch error ({day}): {e}")
        day += timedelta(days=1)

    print(f"US earnings: {total} total rows, {len(events)} in watchlist")
    return events


# ── 流動性事件 (台指期結算) ─────────────────────────────────────────────────────

def fetch_tx_settlements(today, cutoff):
    """打雲達期貨行事曆 API，取台指期(TX)每月結算日（已含假日順延）。"""
    events = []
    headers = {**HEADERS, "Referer": "https://www.yuantafutures.com.tw/marketinfo_05"}
    for year in sorted({today.year, cutoff.year}):
        try:
            r = requests.get(
                YUANTA_CAL_URL,
                params={"format": "json", "select01": "", "select02": "", "y": str(year), "o": ""},
                headers=headers,
                timeout=30,
            )
            r.raise_for_status()
            rows = r.json().get("result02") or []
            tx = next((row for row in rows
                       if (row.get("name") or "").startswith(TX_ROW_PREFIX)), None)
            if not tx:
                print(f"TX row not found for {year}")
                continue
            for ltd in (tx.get("ltd") or []):
                m = re.match(r"(\d{2})/(\d{2})", ltd or "")
                if not m:
                    continue
                try:
                    d = datetime(year, int(m.group(1)), int(m.group(2))).date()
                except ValueError:
                    continue
                if today <= d <= cutoff:
                    events.append({
                        "date": d,
                        "code": "TX",
                        "name": "台指期結算",
                        "event_type": "流動性事件",
                        "rank": 0,
                        "timing": "",
                    })
        except Exception as e:
            print(f"TX settlement fetch error ({year}): {e}")
    print(f"TX settlements in window: {len(events)}")
    return events


# ── 訊息格式 & 發送 ─────────────────────────────────────────────────────────────

def _esc(text):
    """Escape HTML special chars for Telegram HTML parse mode."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def format_message(events_by_date):
    lines = ["📅 <b>股市行事曆</b>", ""]

    if not events_by_date:
        lines.append("近期無相關行事曆事件。")
        return "\n".join(lines)

    for date in sorted(events_by_date):
        wd = WEEKDAYS[date.weekday()]
        lines.append(f"<b>{date.strftime('%m/%d')}（週{wd}）</b>")

        by_type: dict = {}
        for ev in events_by_date[date]:
            by_type.setdefault(ev["event_type"], []).append(ev)

        ordered_types = [t for t in EVENT_TYPE_ORDER if t in by_type]
        ordered_types += [t for t in sorted(by_type) if t not in EVENT_TYPE_ORDER]

        for etype in ordered_types:
            lines.append(f"  ▎{etype}")
            # 依排序值（台股市值 rank 越小越大；美股依清單順序）
            for ev in sorted(by_type[etype], key=lambda x: x.get("rank", 1e9)):
                name = _esc(ev["name"]) if ev["name"] else ev["code"]
                timing = ev.get("timing")
                suffix = f"  <i>{timing}</i>" if timing else ""
                lines.append(f"  {name} (<code>{ev['code']}</code>){suffix}")

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
    rank_map = fetch_top_stocks()

    print("Fetching calendar events...")
    events = fetch_calendar_events(today, cutoff)

    filtered = []
    for ev in events:
        if rank_map and ev["code"] not in rank_map:
            continue
        ev["rank"] = rank_map.get(ev["code"], 1e9)
        filtered.append(ev)
    print(f"Final filtered TW events: {len(filtered)}")

    print("Fetching US earnings (watchlist)...")
    watchlist = load_watchlist()
    us_events = fetch_us_earnings(today, cutoff, watchlist)
    filtered += us_events

    print("Fetching TX futures settlement (流動性事件)...")
    tx_events = fetch_tx_settlements(today, cutoff)
    filtered += tx_events

    events_by_date: dict = {}
    for ev in filtered:
        events_by_date.setdefault(ev["date"], []).append(ev)

    msg = format_message(events_by_date)
    print(msg)
    send_telegram(msg)
    print("Done.")


main()
