import os
import re
import time
import requests
from datetime import datetime, timedelta
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager
from bs4 import BeautifulSoup

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

TARGET_EVENT_TYPES = {"法說會", "股東會"}
WEEKDAYS = ["一", "二", "三", "四", "五", "六", "日"]


# ── Selenium driver ────────────────────────────────────────────────────────────

def make_driver():
    opts = Options()
    opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--window-size=1440,900")
    opts.add_argument(
        "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36"
    )
    service = Service(ChromeDriverManager().install())
    return webdriver.Chrome(service=service, options=opts)


# ── Top-300 market cap via TWSE open API ───────────────────────────────────────

def fetch_top300_stocks():
    """
    Fetch top-300 stocks by market cap using TWSE open data API.
    Falls back to TPEX (OTC) to supplement the list.
    Returns a set of stock codes (strings).
    """
    codes = set()

    # TWSE listed companies market value
    try:
        url = "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"
        r = requests.get(url, timeout=30, headers={"Accept": "application/json"})
        data = r.json()
        # Each item has Code, TradeVolume, etc. No direct market cap here.
        # Use TWSE market cap endpoint instead
    except Exception:
        pass

    # TWSE market cap ranking — use the listed company summary API
    try:
        url = "https://openapi.twse.com.tw/v1/opendata/t187ap03_L"
        r = requests.get(url, timeout=30, headers={"Accept": "application/json"})
        data = r.json()
        # Fields include: 公司代號, 公司名稱, 市值(百萬元)
        pairs = []
        for item in data:
            code = item.get("公司代號", "").strip()
            cap_str = item.get("市值(百萬元)", item.get("market_cap", "0"))
            if not code or not re.match(r"^\d{4,6}$", code):
                continue
            try:
                cap = float(str(cap_str).replace(",", ""))
            except ValueError:
                cap = 0
            pairs.append((cap, code))
        pairs.sort(reverse=True)
        codes.update(code for _, code in pairs[:300])
        print(f"TWSE API: {len(pairs)} companies, top-300 selected ({len(codes)} codes)")
    except Exception as e:
        print(f"TWSE market cap API error: {e}")

    # If TWSE API failed or returned too few, fall back to a simple TWSE listed list
    if len(codes) < 100:
        try:
            url = "https://openapi.twse.com.tw/v1/opendata/t187ap03_L"
            r = requests.get(url, timeout=30)
            # Try alternate field names
            data = r.json()
            print(f"TWSE fallback sample keys: {list(data[0].keys()) if data else 'empty'}")
        except Exception as e:
            print(f"TWSE fallback error: {e}")

    # Last resort: TWSE listed company basic list (no market cap sorting,
    # but at least we know they're listed — use all listed codes as the "filter")
    if len(codes) < 50:
        try:
            url = "https://openapi.twse.com.tw/v1/exchangeReport/BWIBBU_ALL"
            r = requests.get(url, timeout=30, headers={"Accept": "application/json"})
            data = r.json()
            for item in data:
                code = item.get("Code", item.get("股票代號", "")).strip()
                if re.match(r"^\d{4,6}$", code):
                    codes.add(code)
            print(f"TWSE last-resort: got {len(codes)} codes (no market cap filter)")
        except Exception as e:
            print(f"TWSE last-resort error: {e}")

    return codes


# ── Yahoo Finance TW Calendar ──────────────────────────────────────────────────

def fetch_calendar_events():
    """
    Load Yahoo Finance TW calendar with Selenium.
    The page renders event types as standalone lines; nearby lines carry the date
    and stock code. We scan the full text with a sliding-window approach.
    """
    today = datetime.now().date()
    cutoff = today + timedelta(days=6)

    driver = make_driver()
    events = []
    try:
        driver.get("https://tw.stock.yahoo.com/calendar")
        try:
            WebDriverWait(driver, 30).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "li"))
            )
        except Exception:
            pass
        time.sleep(6)

        soup = BeautifulSoup(driver.page_source, "html.parser")
        page_text = soup.get_text("\n", strip=True)
        lines = [l.strip() for l in page_text.split("\n") if l.strip()]

        # Debug: print lines around event type keywords
        for i, l in enumerate(lines):
            if any(t in l for t in TARGET_EVENT_TYPES):
                context = lines[max(0, i-3):i+4]
                print(f"  Event context @ line {i}: {context}")

        events = _parse_lines(lines, today.year, today, cutoff)
        print(f"Total raw events: {len(events)}")

    except Exception as e:
        print(f"Calendar fetch error: {e}")
    finally:
        driver.quit()

    # Deduplicate
    seen = set()
    unique = []
    for e in events:
        key = (e["date"], e["code"], e["event_type"])
        if key not in seen:
            seen.add(key)
            unique.append(e)
    return unique


def _parse_lines(lines, year, today, cutoff):
    """
    Sliding-window parser.

    The Yahoo Finance TW calendar page text looks like one of these patterns:

    Pattern A (date → code+name → event_type):
        06/01（週一）
        2327 國巨
        法說會

    Pattern B (date → event_type → code → name):
        06/01
        法說會
        2327
        國巨

    We track the current date as we scan. When we find an event-type line,
    we look at the surrounding ±4 lines for a stock code.
    """
    events = []
    current_date = None

    for i, line in enumerate(lines):
        # Update current date when we see a MM/DD pattern
        dm = re.match(r"^(\d{1,2})/(\d{1,2})", line)
        if dm:
            try:
                d = datetime(year, int(dm.group(1)), int(dm.group(2))).date()
                if today <= d <= cutoff:
                    current_date = d
                else:
                    current_date = None
            except ValueError:
                pass
            continue

        if current_date is None:
            continue

        # Check if this line is (or contains) a target event type
        matched_type = None
        for etype in TARGET_EVENT_TYPES:
            if line == etype or line.startswith(etype):
                matched_type = etype
                break
        if not matched_type:
            continue

        # Search ±4 lines for a stock code (4–6 digit number)
        window_start = max(0, i - 4)
        window_end = min(len(lines), i + 5)
        window = lines[window_start:window_end]

        code = None
        name = ""
        for wline in window:
            if wline == matched_type:
                continue
            cm = re.search(r"\b(\d{4,6})\b", wline)
            if cm:
                code = cm.group(1)
                # Name: whatever is on the same line minus the code
                name = re.sub(r"\b\d{4,6}\b", "", wline).strip(" -–|")
                name = re.sub(r"\s+", " ", name).strip()
                break

        if code:
            events.append({
                "date": current_date,
                "code": code,
                "name": name,
                "event_type": matched_type,
            })

    return events


# ── Format & Send ──────────────────────────────────────────────────────────────

def format_message(events_by_date):
    today = datetime.now().date()
    date_str = today.strftime("%Y/%m/%d")

    lines = [
        "*台股行事曆 — 法說會 / 股東會*",
        f"（未來 7 天，市值前 300）  {date_str}",
        "",
    ]

    if not events_by_date:
        lines.append("未來 7 天內無符合條件的法說會或股東會。")
        return "\n".join(lines)

    for date in sorted(events_by_date):
        wd = WEEKDAYS[date.weekday()]
        date_label = date.strftime(f"%m/%d（週{wd}）")
        lines.append(f"*{date_label}*")

        by_type: dict[str, list] = {}
        for ev in events_by_date[date]:
            by_type.setdefault(ev["event_type"], []).append(ev)

        for etype in sorted(by_type):
            lines.append(f"  ▎{etype}")
            for ev in sorted(by_type[etype], key=lambda x: x["code"]):
                name = f" {ev['name']}" if ev["name"] else ""
                lines.append(f"  `{ev['code']}`{name}")

        lines.append("")

    return "\n".join(lines).rstrip()


def send_telegram(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    r = requests.post(url, json={
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "Markdown",
    })
    print(f"Telegram: {r.status_code}")


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    print("Fetching top-300 market cap stocks...")
    top300 = fetch_top300_stocks()
    if not top300:
        print("Warning: top-300 fetch failed — no market cap filter applied")

    print("Fetching Yahoo Finance TW calendar...")
    events = fetch_calendar_events()

    filtered = [
        ev for ev in events
        if ev["event_type"] in TARGET_EVENT_TYPES
        and (not top300 or ev["code"] in top300)
    ]
    print(f"Filtered events: {len(filtered)}")

    events_by_date: dict = {}
    for ev in filtered:
        events_by_date.setdefault(ev["date"], []).append(ev)

    msg = format_message(events_by_date)
    print(msg)
    send_telegram(msg)
    print("Done.")


main()
