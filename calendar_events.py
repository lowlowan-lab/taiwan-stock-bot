import os
import re
import json
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

CALENDAR_URL = "https://m.esunsec.com.tw/iframePage/calendar.aspx"
TWSE_WEIGHT_URL = "https://www.taifex.com.tw/cht/9/futuresQADetail"
TPEX_WEIGHT_URL = "https://www.taifex.com.tw/cht/2/tPEXPropertion"
TWSE_TOP_N = 200
TPEX_TOP_N = 100


# ── Selenium driver ────────────────────────────────────────────────────────────

def make_driver(capture_network=False):
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
    if capture_network:
        opts.set_capability("goog:loggingPrefs", {"performance": "ALL"})
    service = Service(ChromeDriverManager().install())
    return webdriver.Chrome(service=service, options=opts)


def dump_network_requests(driver):
    """Print XHR/fetch request URLs that look like data endpoints."""
    try:
        logs = driver.get_log("performance")
    except Exception as e:
        print(f"  No performance log: {e}")
        return
    seen = set()
    for entry in logs:
        try:
            msg = json.loads(entry["message"])["message"]
        except Exception:
            continue
        if msg.get("method") != "Network.requestWillBeSent":
            continue
        url = msg["params"]["request"]["url"]
        if url in seen:
            continue
        seen.add(url)
        # Filter to interesting data-ish endpoints
        low = url.lower()
        if any(k in low for k in [".ashx", ".asmx", ".json", "/api", "getcalendar",
                                  "calendar", "data", "ajax", "handler", "service",
                                  ".aspx/"]):
            method = msg["params"]["request"]["method"]
            print(f"  XHR {method}: {url[:200]}")


# ── Market cap ranking via TAIFEX index weight pages ───────────────────────────

def _scrape_taifex_weights(url, top_n, label):
    """
    Scrape a TAIFEX index-constituent-weight page.
    Returns set of top-N stock codes ranked by weight (proxy for market cap).
    """
    driver = make_driver()
    codes = set()
    try:
        driver.get(url)
        time.sleep(5)
        soup = BeautifulSoup(driver.page_source, "html.parser")

        tables = soup.find_all("table")
        print(f"  [{label}] {len(tables)} tables found")

        pairs = []  # (weight, code)
        for row in soup.select("table tr"):
            cells = [c.get_text(strip=True) for c in row.find_all(["td", "th"])]
            if len(cells) < 2:
                continue
            code = next((c for c in cells if re.fullmatch(r"\d{4}", c)), None)
            if not code:
                continue
            # weight: first float-looking cell
            weight = None
            for c in cells:
                if re.fullmatch(r"\d+\.\d+", c.replace(",", "")):
                    weight = float(c.replace(",", ""))
                    break
            pairs.append((weight if weight is not None else 0, code))

        # Debug: show first few parsed rows
        print(f"  [{label}] parsed {len(pairs)} code rows, sample: {pairs[:5]}")

        # If weights present, sort by weight desc; else keep page order
        if any(w for w, _ in pairs):
            pairs.sort(key=lambda x: x[0], reverse=True)
        codes = {code for _, code in pairs[:top_n]}
        print(f"  [{label}] top-{top_n}: {len(codes)} codes")

    except Exception as e:
        print(f"  [{label}] error: {e}")
    finally:
        driver.quit()
    return codes


def fetch_top_stocks():
    """TWSE top-200 + TPEX top-100 by index weight."""
    codes = set()
    codes |= _scrape_taifex_weights(TWSE_WEIGHT_URL, TWSE_TOP_N, "TWSE")
    codes |= _scrape_taifex_weights(TPEX_WEIGHT_URL, TPEX_TOP_N, "TPEX")
    print(f"Total filter set: {len(codes)} codes")
    return codes


# ── Calendar scraping (玉山證 行事曆) ──────────────────────────────────────────

def fetch_calendar_events():
    """
    Scrape esunsec mobile calendar for 法說會 / 股東會 in the next 7 days.
    Structure unknown — heavy debug output for first iteration.
    """
    today = datetime.now().date()
    cutoff = today + timedelta(days=6)

    driver = make_driver(capture_network=True)
    events = []
    try:
        driver.get(CALENDAR_URL)
        time.sleep(8)

        # Debug 1: what data endpoints did the page hit?
        print("  --- network requests ---")
        dump_network_requests(driver)

        # Debug 2: any iframes? print their src
        iframes = driver.find_elements(By.TAG_NAME, "iframe")
        print(f"  iframes found: {len(iframes)}")
        for f in iframes:
            print(f"    iframe src: {f.get_attribute('src')}")

        # Debug 3: try switching into each iframe and read its text
        for idx, f in enumerate(iframes):
            try:
                driver.switch_to.frame(f)
                ftext = driver.find_element(By.TAG_NAME, "body").text
                print(f"  iframe[{idx}] body text (first 400): {ftext[:400]!r}")
                driver.switch_to.default_content()
            except Exception as e:
                print(f"  iframe[{idx}] switch error: {e}")
                driver.switch_to.default_content()

        soup = BeautifulSoup(driver.page_source, "html.parser")
        lines = [l.strip() for l in soup.get_text("\n", strip=True).split("\n") if l.strip()]

        print(f"  Calendar total lines: {len(lines)}")
        for i, l in enumerate(lines):
            if any(t in l for t in TARGET_EVENT_TYPES):
                print(f"  Event ctx [{i}]: {lines[max(0,i-3):i+4]}")

        events = _parse_calendar_lines(lines, today.year, today, cutoff)
        print(f"Raw calendar events: {len(events)}")

    except Exception as e:
        print(f"Calendar fetch error: {e}")
    finally:
        driver.quit()

    seen = set()
    unique = []
    for e in events:
        key = (e["date"], e["code"], e["event_type"])
        if key not in seen:
            seen.add(key)
            unique.append(e)
    return unique


def _parse_calendar_lines(lines, year, today, cutoff):
    """Best-effort parser; refined after we see the real structure."""
    events = []
    current_date = None
    for i, line in enumerate(lines):
        d = _try_parse_date(line, year)
        if d is not None:
            current_date = d if today <= d <= cutoff else None
            continue
        if current_date is None:
            continue
        for etype in TARGET_EVENT_TYPES:
            if etype in line:
                cm = re.search(r"\b(\d{4,6})\b", line)
                code = cm.group(1) if cm else None
                if not code:
                    # look nearby
                    for b in range(1, 4):
                        if i - b >= 0:
                            m = re.search(r"\b(\d{4,6})\b", lines[i - b])
                            if m:
                                code = m.group(1)
                                break
                if code:
                    name = re.sub(r"\b\d{4,6}\b", "", line).replace(etype, "")
                    name = re.sub(r"\s+", " ", name).strip(" -–|、")
                    events.append({"date": current_date, "code": code,
                                   "name": name, "event_type": etype})
                break
    return events


def _try_parse_date(line, year):
    m = re.match(r"^(\d{4})[/-](\d{1,2})[/-](\d{1,2})", line)
    if m:
        try:
            return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3))).date()
        except ValueError:
            return None
    m = re.match(r"^(\d{1,2})/(\d{1,2})", line)
    if m:
        try:
            return datetime(year, int(m.group(1)), int(m.group(2))).date()
        except ValueError:
            return None
    m = re.match(r"^(\d{1,2})月(\d{1,2})日", line)
    if m:
        try:
            return datetime(year, int(m.group(1)), int(m.group(2))).date()
        except ValueError:
            return None
    return None


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
    print("Fetching market cap ranking (上市200 + 上櫃100)...")
    top_stocks = fetch_top_stocks()

    print("Fetching calendar events...")
    events = fetch_calendar_events()

    filtered = [
        ev for ev in events
        if not top_stocks or ev["code"] in top_stocks
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
