import os
import re
import time
import requests
from datetime import datetime, timedelta
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager
from bs4 import BeautifulSoup

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

WEEKDAYS = ["一", "二", "三", "四", "五", "六", "日"]

CALENDAR_URLS = {
    "法說會": "https://tw.stock.yahoo.com/calendar/earnings-call",
    "股東會": "https://tw.stock.yahoo.com/calendar/holders-meeting",
}


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


# ── Top-300 market cap (上市 + 上櫃) ──────────────────────────────────────────

def _fetch_caps(url, label):
    """Fetch market cap pairs from a TWSE open API endpoint."""
    r = requests.get(url, timeout=30, headers={"Accept": "application/json"})
    r.raise_for_status()
    data = r.json()
    pairs = []
    for item in data:
        code = item.get("公司代號", "").strip()
        cap_str = item.get("市值(百萬元)", "0")
        if not re.match(r"^\d{4,6}$", code):
            continue
        try:
            pairs.append((float(str(cap_str).replace(",", "")), code))
        except ValueError:
            pass
    print(f"{label}: {len(pairs)} companies")
    return pairs


def fetch_top300_stocks():
    """Combine TWSE (上市) and TPEX (上櫃) market caps, return top-300 codes."""
    all_pairs = []

    try:
        all_pairs.extend(_fetch_caps(
            "https://openapi.twse.com.tw/v1/opendata/t187ap03_L", "TWSE"
        ))
    except Exception as e:
        print(f"TWSE market cap error: {e}")

    try:
        all_pairs.extend(_fetch_caps(
            "https://openapi.twse.com.tw/v1/opendata/t187ap03_O", "TPEX"
        ))
    except Exception as e:
        print(f"TPEX market cap error: {e}")

    if not all_pairs:
        print("Warning: both market cap sources failed — no filter applied")
        return set()

    all_pairs.sort(reverse=True)
    top300 = {code for _, code in all_pairs[:300]}
    top5 = [code for _, code in all_pairs[:5]]
    print(f"Top-300 combined ({len(all_pairs)} total), top5: {top5}")
    return top300


# ── Calendar scraping ──────────────────────────────────────────────────────────

def scrape_one_calendar(url, event_type, today, cutoff):
    """
    Scrape a dedicated Yahoo Finance TW calendar page (earnings-call or
    holders-meeting). Each page lists only one event type.

    Confirmed page structure (from debug):
        [149] 2026/06/01 週一          ← date: YYYY/MM/DD 週X
        [150] 除權息                    (ignored on dedicated pages)
        [151] 中航
        [152] 2612
        [153] 、
        [154] 大田
        [155] 8924
        [156] 、
        ...

    On earnings-call / holders-meeting pages, all listed companies
    belong to the page's event type. We just need date + code pairs.
    """
    driver = make_driver()
    events = []
    try:
        driver.get(url)
        try:
            WebDriverWait(driver, 30).until(
                EC.presence_of_element_located(("css selector", "li"))
            )
        except Exception:
            pass
        time.sleep(6)

        soup = BeautifulSoup(driver.page_source, "html.parser")
        lines = [l.strip() for l in soup.get_text("\n", strip=True).split("\n") if l.strip()]

        current_date = None
        for i, line in enumerate(lines):
            # Date line: YYYY/MM/DD 週X
            dm = re.match(r"^(\d{4})/(\d{1,2})/(\d{1,2})", line)
            if dm:
                try:
                    d = datetime(int(dm.group(1)), int(dm.group(2)), int(dm.group(3))).date()
                    current_date = d if today <= d <= cutoff else None
                except ValueError:
                    current_date = None
                continue

            if current_date is None:
                continue

            # Stock code: standalone 4-6 digit line
            if re.match(r"^\d{4,6}$", line):
                code = line
                # Company name is the line immediately before (skip separators)
                name = ""
                for back in range(1, 4):
                    prev = lines[i - back] if i - back >= 0 else ""
                    if prev in {"、", "，", ",", ""} or re.match(r"^\d", prev):
                        continue
                    # Skip event-type labels and date lines
                    if re.match(r"^\d{4}/", prev) or prev in {"法說會", "股東會", "除權息"}:
                        continue
                    name = prev
                    break

                events.append({
                    "date": current_date,
                    "code": code,
                    "name": name,
                    "event_type": event_type,
                })

    except Exception as e:
        print(f"Scrape error ({event_type}): {e}")
    finally:
        driver.quit()

    print(f"{event_type}: {len(events)} raw events")
    return events


def fetch_calendar_events():
    today = datetime.now().date()
    cutoff = today + timedelta(days=6)
    all_events = []
    for event_type, url in CALENDAR_URLS.items():
        events = scrape_one_calendar(url, event_type, today, cutoff)
        all_events.extend(events)

    # Deduplicate
    seen = set()
    unique = []
    for e in all_events:
        key = (e["date"], e["code"], e["event_type"])
        if key not in seen:
            seen.add(key)
            unique.append(e)
    return unique


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
        lines.append(f"*{date.strftime('%m/%d')}（週{wd}）*")

        by_type: dict = {}
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

    print("Fetching Yahoo Finance TW calendar...")
    events = fetch_calendar_events()

    filtered = [
        ev for ev in events
        if not top300 or ev["code"] in top300
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
