"""台股（TWSE 官方休市表）與美股（NYSE 規則計算）休市日查詢。

被 calendar_events.py（市場休市類別）、turnover.py / scraper.py（台股休市不推播）、
us_daily.py（美股昨日休市改推提示）共用。
"""
import requests
from datetime import date, timedelta

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
}

TWSE_HOLIDAY_URL = "https://www.twse.com.tw/rwd/zh/holidaySchedule/holidaySchedule"

_twse_cache = {}   # year -> {date: 名稱}


# ── 台股（證交所官方休市表）──────────────────────────────────────────────────

def twse_holidays(year):
    """回傳當年台股實際休市日 {date: 名稱}。排除只是「交易日」註記的列。"""
    if year in _twse_cache:
        return _twse_cache[year]
    out = {}
    try:
        r = requests.get(
            TWSE_HOLIDAY_URL,
            params={"response": "json", "queryYear": year - 1911},
            headers=HEADERS, timeout=30,
        )
        for row in r.json().get("data", []):
            dstr, name = row[0], (row[1] if len(row) > 1 else "")
            # 「開始交易日 / 最後交易日」等是交易日註記，非休市；但「市場無交易」是休市
            if "交易" in name and "無交易" not in name:
                continue
            try:
                out[date.fromisoformat(dstr)] = name
            except ValueError:
                continue
    except Exception as e:
        print(f"TWSE holiday fetch error: {e}")
    _twse_cache[year] = out
    return out


def is_twse_closed(d):
    """台股當天是否休市（週末或國定假日）。"""
    if d.weekday() >= 5:
        return True
    return d in twse_holidays(d.year)


# ── 美股（NYSE/NASDAQ 規則計算）────────────────────────────────────────────────

def _nth_weekday(year, month, weekday, n):
    first = date(year, month, 1)
    return date(year, month, 1 + (weekday - first.weekday()) % 7 + (n - 1) * 7)


def _last_weekday(year, month, weekday):
    d = (date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)) - timedelta(days=1)
    return d - timedelta(days=(d.weekday() - weekday) % 7)


def _observed(d):
    """假日落週六→前一個週五；落週日→次一個週一。"""
    if d.weekday() == 5:
        return d - timedelta(days=1)
    if d.weekday() == 6:
        return d + timedelta(days=1)
    return d


def _easter(year):
    a = year % 19; b = year // 100; c = year % 100
    d = b // 4; e = b % 4; f = (b + 8) // 25; g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30; i = c // 4; k = c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7; m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31; day = ((h + l - 7 * m + 114) % 31) + 1
    return date(year, month, day)


def us_market_holidays(year):
    """NYSE/NASDAQ 全天休市日 {date: 英文名}。（不含半日提早收盤）"""
    hol = {}
    # 元旦：落週六則不補假（不像其他假日往前補週五）
    ny = date(year, 1, 1)
    if ny.weekday() == 6:
        hol[date(year, 1, 2)] = "New Year's Day"
    elif ny.weekday() != 5:
        hol[ny] = "New Year's Day"
    hol[_nth_weekday(year, 1, 0, 3)] = "Martin Luther King Jr. Day"
    hol[_nth_weekday(year, 2, 0, 3)] = "Washington's Birthday"
    hol[_easter(year) - timedelta(days=2)] = "Good Friday"
    hol[_last_weekday(year, 5, 0)] = "Memorial Day"
    if year >= 2022:
        hol[_observed(date(year, 6, 19))] = "Juneteenth"
    hol[_observed(date(year, 7, 4))] = "Independence Day"
    hol[_nth_weekday(year, 9, 0, 1)] = "Labor Day"
    hol[_nth_weekday(year, 11, 3, 4)] = "Thanksgiving"
    hol[_observed(date(year, 12, 25))] = "Christmas"
    return hol


def is_us_closed(d):
    """美股當天是否休市（週末或國定假日）。"""
    if d.weekday() >= 5:
        return True
    return d in us_market_holidays(d.year)
