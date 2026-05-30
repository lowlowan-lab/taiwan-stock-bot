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
    Load Yahoo Finance TW calendar with Selenium.
    Uses XPath to find elements that contain target event type text,
    then walks the DOM to extract date, stock code, and company name.
    Returns list of dicts: {date, code, name, event_type}
    """
    today = datetime.now().date()
    cutoff = today + timedelta(days=6)
    year = today.year

    driver = make_driver()
    events = []
    try:
        driver.get("https://tw.stock.yahoo.com/calendar")

        # Wait until at least one list item appears
        try:
            WebDriverWait(driver, 30).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "li, [class*='item']"))
            )
        except Exception:
            pass
        time.sleep(6)

        # --- Strategy 1: find elements whose text IS exactly a target event type,
        #     then walk ancestors to find date and stock code in nearby text ----
        xpath_event_labels = " or ".join(
            f"normalize-space(text())='{t}'" for t in TARGET_EVENT_TYPES
        )
        label_els = driver.find_elements(By.XPATH, f"//*[{xpath_event_labels}]")
        print(f"Found {len(label_els)} event-type label elements via XPath")

        for el in label_els:
            event_type = el.text.strip()
            if event_type not in TARGET_EVENT_TYPES:
                continue

            # Walk up to find a container that holds date + stock code
            container_text = ""
            node = el
            for _ in range(6):
                try:
                    node = node.find_element(By.XPATH, "..")
                    container_text = node.text.strip()
                    # Good container: has a 4-digit stock code AND a date-like pattern
                    if re.search(r"\b\d{4,6}\b", container_text) and re.search(r"\d{1,2}/\d{1,2}", container_text):
                        break
                except Exception:
                    break

            parsed = _extract_from_text(container_text, event_type, year, today, cutoff)
            if parsed:
                events.append(parsed)

        # --- Strategy 2: if strategy 1 found nothing, scan full page text -------
        if not events:
            print("Strategy 1 found nothing, falling back to full-text scan")
            soup = BeautifulSoup(driver.page_source, "html.parser")
            page_text = soup.get_text("\n", strip=True)
            events = _scan_text(page_text, year, today, cutoff)

        print(f"Total raw events parsed: {len(events)}")

        # Debug: show a few lines near event-type keywords
        soup2 = BeautifulSoup(driver.page_source, "html.parser")
        all_lines = [l.strip() for l in soup2.get_text("\n").split("\n") if l.strip()]
        sample = [l for l in all_lines if any(t in l for t in TARGET_EVENT_TYPES)]
        print(f"Sample lines with event types: {sample[:8]}")

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


def _extract_from_text(text, event_type, year, today, cutoff):
    """Try to extract one event dict from a container's full text."""
    code_m = re.search(r"\b(\d{4,6})\b", text)
    if not code_m:
        return None
    code = code_m.group(1)

    date_m = re.search(r"(\d{1,2})/(\d{1,2})", text)
    if not date_m:
        return None
    try:
        ev_date = datetime(year, int(date_m.group(1)), int(date_m.group(2))).date()
    except ValueError:
        return None
    if not (today <= ev_date <= cutoff):
        return None

    # Company name: remove code, date, event_type, leftover punctuation
    name = text
    name = re.sub(r"\b" + code + r"\b", "", name)
    name = re.sub(r"\d{1,2}/\d{1,2}(\s*\([^\)]*\))?", "", name)
    name = name.replace(event_type, "")
    name = re.sub(r"[\s　\n]+", " ", name).strip(" -–—|,，。")
    # Take the longest segment that looks like a company name
    parts = [p.strip() for p in re.split(r"\s+", name) if len(p.strip()) >= 2 and not re.match(r"^[\d/()（）週]+$", p.strip())]
    name = parts[0] if parts else ""

    return {"date": ev_date, "code": code, "name": name, "event_type": event_type}


def _scan_text(page_text, year, today, cutoff):
    """Line-by-line scan fallback."""
    events = []
    lines = [l.strip() for l in page_text.split("\n") if l.strip()]
    current_date = None

    for line in lines:
        # Date header?
        dm = re.match(r"^(\d{1,2})/(\d{1,2})", line)
        if dm:
            try:
                d = datetime(year, int(dm.group(1)), int(dm.group(2))).date()
                current_date = d if today <= d <= cutoff else None
            except ValueError:
                pass
            continue

        if not current_date:
            continue

        # Line contains an event type?
        for etype in TARGET_EVENT_TYPES:
            if etype in line:
                code_m = re.search(r"\b(\d{4,6})\b", line)
                if code_m:
                    code = code_m.group(1)
                    name = re.sub(r"\b\d{4,6}\b", "", line).replace(etype, "")
                    name = re.sub(r"\s+", " ", name).strip(" -")
                    events.append({"date": current_date, "code": code, "name": name, "event_type": etype})
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
    print("Fetching top-300 market cap stocks from Goodinfo...")
    top300 = fetch_top300_stocks()
    if not top300:
        print("Warning: top-300 fetch failed — showing all events without filter")

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
