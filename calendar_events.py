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


# ── Top-300 market cap ─────────────────────────────────────────────────────────

def fetch_top300_stocks():
    """Return set of stock codes for top-300 market-cap companies from Goodinfo."""
    url = (
        "https://goodinfo.tw/tw/StockList.asp"
        "?MARKET_CAT=熱門排行"
        "&INDUSTRY_CAT=公司總市值最高%40%40公司總市值%40%40公司總市值最高"
        "&SHEET=公司基本資料"
        "&RPT_TIME=最新資料"
        "&RANK_RANGE=300"
    )
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36"
        ),
        "Referer": "https://goodinfo.tw/",
        "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
    }
    codes = set()
    try:
        r = requests.get(url, headers=headers, timeout=30)
        r.encoding = "big5"
        soup = BeautifulSoup(r.text, "html.parser")
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if "STOCK_ID=" in href:
                code = href.split("STOCK_ID=")[-1].split("&")[0].strip()
                if re.match(r"^\d{4,6}$", code):
                    codes.add(code)
        print(f"Goodinfo: found {len(codes)} stock codes")
    except Exception as e:
        print(f"Goodinfo fetch error: {e}")
    return codes


# ── Yahoo Finance TW Calendar ──────────────────────────────────────────────────

def fetch_calendar_events():
    """
    Load Yahoo Finance TW calendar with Selenium and parse events
    for the next 7 days. Returns list of dicts:
      {date, code, name, event_type}
    """
    today = datetime.now().date()
    cutoff = today + timedelta(days=6)

    driver = make_driver()
    events = []
    try:
        driver.get("https://tw.stock.yahoo.com/calendar")
        # Wait for some list content to appear
        try:
            WebDriverWait(driver, 25).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "ul li, [class*='List'] li"))
            )
        except Exception:
            pass
        time.sleep(5)  # allow lazy-loaded content to settle

        soup = BeautifulSoup(driver.page_source, "html.parser")
        events = _parse_calendar(soup, today, cutoff)
        print(f"Yahoo calendar: {len(events)} events parsed")

        # Debug: show a snippet of the text to verify structure
        page_lines = [l.strip() for l in soup.get_text("\n").split("\n") if l.strip()]
        sample = [l for l in page_lines if any(t in l for t in TARGET_EVENT_TYPES)]
        print(f"Sample event lines: {sample[:5]}")

    except Exception as e:
        print(f"Calendar fetch error: {e}")
    finally:
        driver.quit()

    return events


def _try_parse_date(text, year):
    """Try to parse a MM/DD date string, return date or None."""
    m = re.match(r"^(\d{1,2})/(\d{1,2})", text.strip())
    if m:
        try:
            return datetime(year, int(m.group(1)), int(m.group(2))).date()
        except ValueError:
            return None
    return None


def _parse_calendar(soup, today, cutoff):
    """
    Parse Yahoo Finance TW calendar HTML.
    The page renders date sections with event lists beneath each date.
    We scan line-by-line through the visible text to pair dates with events.
    """
    events = []
    year = today.year

    page_text = soup.get_text("\n", strip=True)
    lines = [l.strip() for l in page_text.split("\n") if l.strip()]

    current_date = None
    i = 0
    while i < len(lines):
        line = lines[i]

        # Detect date header: starts with MM/DD pattern
        parsed_date = _try_parse_date(line, year)
        if parsed_date:
            if today <= parsed_date <= cutoff:
                current_date = parsed_date
            else:
                current_date = None
            i += 1
            continue

        # Detect event type label as a standalone line
        event_type_found = next((t for t in TARGET_EVENT_TYPES if t == line), None)
        if event_type_found and current_date:
            # The stock code and name are likely on adjacent lines
            # Look ahead for a stock code in the next few lines
            for j in range(i + 1, min(i + 5, len(lines))):
                code_m = re.search(r"\b(\d{4,6})\b", lines[j])
                if code_m:
                    code = code_m.group(1)
                    name = re.sub(r"\b\d{4,6}\b", "", lines[j]).strip()
                    name = re.sub(r"\s+", " ", name).strip()
                    events.append({
                        "date": current_date,
                        "code": code,
                        "name": name,
                        "event_type": event_type_found,
                    })
            i += 1
            continue

        # Detect inline format: "2330 台積電 法說會" or "台積電 2330 股東會"
        if current_date and any(t in line for t in TARGET_EVENT_TYPES):
            code_m = re.search(r"\b(\d{4,6})\b", line)
            if code_m:
                code = code_m.group(1)
                etype = next(t for t in TARGET_EVENT_TYPES if t in line)
                name = re.sub(r"\b\d{4,6}\b", "", line).replace(etype, "").strip()
                name = re.sub(r"\s+", " ", name).strip(" -–|")
                events.append({
                    "date": current_date,
                    "code": code,
                    "name": name,
                    "event_type": etype,
                })

        i += 1

    # Deduplicate
    seen = set()
    unique = []
    for e in events:
        key = (e["date"], e["code"], e["event_type"])
        if key not in seen:
            seen.add(key)
            unique.append(e)
    return unique


# ── Format message ─────────────────────────────────────────────────────────────

def format_message(events_by_date):
    today = datetime.now().date()
    date_str = today.strftime("%Y/%m/%d")

    lines = [
        f"*台股行事曆 — 法說會 / 股東會*",
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


# ── Telegram ───────────────────────────────────────────────────────────────────

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
    print("Fetching top-300 market cap stocks from Goodinfo...")
    top300 = fetch_top300_stocks()
    if not top300:
        print("Warning: top-300 fetch failed — showing all events without filter")

    print("Fetching Yahoo Finance TW calendar...")
    events = fetch_calendar_events()

    # Filter: event type + top-300 (skip filter if top300 is empty)
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
