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


# ── Top-300 market cap (上市 + 上櫃) ──────────────────────────────────────────

def _fetch_twse_caps():
    """Return list of (market_cap, code) for TWSE listed companies."""
    url = "https://openapi.twse.com.tw/v1/opendata/t187ap03_L"
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
    print(f"TWSE: {len(pairs)} listed companies")
    return pairs


def _fetch_tpex_caps():
    """Return list of (market_cap, code) for TPEX OTC companies."""
    url = "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_peratio_analysis"
    r = requests.get(url, timeout=30, headers={"Accept": "application/json"})
    r.raise_for_status()
    data = r.json()
    if data:
        print(f"  TPEX sample keys: {list(data[0].keys())}")
        print(f"  TPEX sample row:  {data[0]}")
    pairs = []
    for item in data:
        # Field names vary by API version — try multiple
        code = (item.get("SecuritiesCompanyCode")
                or item.get("股票代號")
                or item.get("Code", "")).strip()
        # Market cap field (百萬元) if available directly
        cap_str = (item.get("MarketCap")
                   or item.get("市值")
                   or item.get("市值(百萬元)"))
        if cap_str is None:
            # Compute: close × shares (千股 → 股) / 1,000,000 → 百萬元
            close_str = (item.get("Close") or item.get("收盤價") or "0")
            shares_str = (item.get("IssuedShares") or item.get("發行股數") or "0")
            try:
                close = float(str(close_str).replace(",", ""))
                shares = float(str(shares_str).replace(",", ""))  # unit: 千股
                cap = close * shares * 1000 / 1_000_000           # → 百萬元
            except (ValueError, TypeError):
                cap = 0
        else:
            try:
                cap = float(str(cap_str).replace(",", ""))
            except (ValueError, TypeError):
                cap = 0
        if not re.match(r"^\d{4,6}$", code):
            continue
        pairs.append((cap, code))
    print(f"TPEX: {len(pairs)} OTC companies")
    return pairs


def fetch_top300_stocks():
    """
    Combine TWSE (上市) and TPEX (上櫃) market caps, return top-300 codes.
    """
    all_pairs = []

    try:
        all_pairs.extend(_fetch_twse_caps())
    except Exception as e:
        print(f"TWSE market cap error: {e}")

    try:
        all_pairs.extend(_fetch_tpex_caps())
    except Exception as e:
        print(f"TPEX market cap error: {e}")

    if not all_pairs:
        print("Warning: both market cap sources failed — no filter applied")
        return set()

    all_pairs.sort(reverse=True)
    top300 = {code for _, code in all_pairs[:300]}
    print(f"Top-300 combined: {len(top300)} codes  (top5: {[c for _,c in all_pairs[:5]]})")
    return top300


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

        # Debug: print lines 80~175 to find date format and structure
        print(f"  Page lines 80~175:")
        for j in range(80, min(len(lines), 176)):
            print(f"    [{j}] {lines[j]}")

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


def _try_parse_date_line(line, year):
    """
    Parse various date formats Yahoo Finance TW might use:
      05/30  |  5/30  |  5月30日  |  2026年5月30日  |  05/30（週五）
    Returns a date object or None.
    """
    # MM/DD or M/D (with optional Chinese weekday suffix)
    m = re.match(r"^(\d{1,2})/(\d{1,2})", line)
    if m:
        try:
            return datetime(year, int(m.group(1)), int(m.group(2))).date()
        except ValueError:
            pass

    # M月D日 or MM月DD日
    m = re.match(r"^(\d{1,2})月(\d{1,2})日", line)
    if m:
        try:
            return datetime(year, int(m.group(1)), int(m.group(2))).date()
        except ValueError:
            pass

    # YYYY年M月D日
    m = re.match(r"^\d{4}年(\d{1,2})月(\d{1,2})日", line)
    if m:
        try:
            return datetime(year, int(m.group(1)), int(m.group(2))).date()
        except ValueError:
            pass

    return None


def _parse_lines(lines, year, today, cutoff):
    """
    From the debug log, the confirmed page structure is:

        ...
        [date line]   e.g. "5月30日" or "05/30"
        ...
        、
        大田          ← company name (one line)
        8924          ← stock code (one line)
        法說會        ← event type (one line)
        聚陽          ← next company name
        1477          ← next stock code
        、
        ...

    Strategy: scan forward; maintain current_date.
    When we hit an event-type line, look at the 1~2 lines immediately BEFORE it
    for the stock code (and optionally the company name one line further back).
    """
    events = []
    current_date = None

    for i, line in enumerate(lines):
        # Detect date line
        d = _try_parse_date_line(line, year)
        if d is not None:
            current_date = d if today <= d <= cutoff else None
            continue

        if current_date is None:
            continue

        # Detect event type as a standalone line
        if line not in TARGET_EVENT_TYPES:
            continue

        event_type = line

        # The stock code is expected on the line immediately before (i-1)
        # and the company name one line before that (i-2).
        # Also handle i-1 being a separator like 、 and code at i-2.
        code = None
        name = ""

        for back in range(1, 5):
            if i - back < 0:
                break
            candidate = lines[i - back]
            # Skip separators
            if candidate in {"、", "，", ",", "／", "/", ""}:
                continue
            cm = re.match(r"^(\d{4,6})$", candidate)
            if cm:
                code = cm.group(1)
                # Name is the line before the code (skip separators)
                for nb in range(back + 1, back + 4):
                    if i - nb < 0:
                        break
                    nc = lines[i - nb]
                    if nc in {"、", "，", ",", "／", "/", ""}:
                        continue
                    if not re.match(r"^\d", nc) and nc not in TARGET_EVENT_TYPES:
                        name = nc
                    break
                break

        if code:
            events.append({
                "date": current_date,
                "code": code,
                "name": name,
                "event_type": event_type,
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
