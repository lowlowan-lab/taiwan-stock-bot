import os
import re
import requests
from datetime import datetime, timedelta, date, timezone
from bs4 import BeautifulSoup

import market_holidays

WATCHLIST_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "us_watchlist.txt")
ASIA_WATCHLIST_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "asia_watchlist.txt")

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
# Nasdaq 只給粗略時段，換算成台灣時段（不受美國日光節約影響）：
#   美股盤前（美東早上發布）→ 台灣當天傍晚 → 台灣盤後（同日）
#   美股盤後（美東收盤後發布）→ 台灣隔天清晨 → 台灣盤前（日期 +1）

# TradingEconomics 美國經濟行事曆（impact 三星＝高影響事件）
TE_US_CALENDAR_URL = "https://tradingeconomics.com/united-states/calendar"

# Yahoo Finance 報價（亞股財報日；免費，需 cookie + crumb）
YAHOO_QUOTE_URL = "https://query1.finance.yahoo.com/v7/finance/quote"
TW_TZ = timezone(timedelta(hours=8))

# 雲達期貨交易行事曆 API（台指期結算日）
YUANTA_CAL_URL = "https://www.yuantafutures.com.tw/api/TradeCal01"
TX_ROW_PREFIX = "大台指期貨(TX)"

# ETF 成分股定期審核生效日（規則計算，非爬蟲）
#   ftse_q：富時台灣50，3/6/9/12 月第三個週五生效
#   ftse_h：富時高股息，6/12 月第三個週五生效
#   msci_h：MSCI ESG高股息30，5/11 月最後一個營業日生效
#   before/after：調整期窗口＝生效日前 before、後 after 個交易日（生效日當天另標「生效」）
ETF_REBALANCE = [
    {"code": "0050", "name": "元大台灣50", "kind": "ftse_q", "before": 0, "after": 0},    # 只有生效日當天
    {"code": "006208", "name": "富邦台50", "kind": "ftse_q", "before": 0, "after": 4},    # 生效日(含)起 5 個交易日
    {"code": "0056", "name": "元大高股息", "kind": "ftse_h", "before": 0, "after": 4},    # 生效日(含)起 5 個交易日
    {"code": "00878", "name": "國泰永續高股息", "kind": "msci_h", "before": 2, "after": 5},
]

# 00919（臺灣指數公司編，無乾淨生效日公式）：鎖定已知年份，依官方/新聞公告手動補列。
# 格式：(code, name, 公告日, 生效日, 過渡期交易日數)；過渡期自生效日起（含）往後算交易日。
ETF_FIXED_REBALANCES = [
    # 2026 上半年審核：6/2 公布 18進18出，過渡期 8 個交易日（生效日起往後）。【確定】
    ("00919", "群益台灣精選高息", date(2026, 6, 2), date(2026, 6, 2), 8),
    # 2026 下半年審核：尚未官方公告，依 2025/12（公告12/16、生效12/17）型態推估。【估算，待 12 月公告校正】
    ("00919", "群益台灣精選高息", date(2026, 12, 15), date(2026, 12, 16), 8),

    # 00929 復華台灣科技優息（半年審 6／12 月，過渡期 8 交易日，生效日起往後）
    # 2026 上半年審核：公告 6/26、生效 6/29（22進22出）。【確定】
    ("00929", "復華台灣科技優息", date(2026, 6, 26), date(2026, 6, 29), 8),
    # 2026 下半年審核：尚未公告，依 2025/12（公告12/26、生效12/29）型態推估。【估算，待 12 月公告校正】
    ("00929", "復華台灣科技優息", date(2026, 12, 25), date(2026, 12, 28), 8),
    # 之後每年依公告補上（例：2027/06、2027/12 …），見年度複查提醒
]

# 每年 1/1–1/3 推播帶上：提醒人工複查 ETF 換股規則有沒有被基金/指數公司改掉
ETF_REVIEW_REMINDER = (
    "⚠️ <b>年度規則複查</b>\n"
    "請對照官網確認 ETF 換股規則是否仍為：\n"
    "  0050／006208：季審 3／6／9／12 月，第三個週五生效\n"
    "  0056：半年審 6／12 月，第三個週五生效\n"
    "  00878：半年審 5／11 月，月底營業日生效（此檔最常改，重點看）\n"
    "  00919：半年審 5／12 月，無公式 → 請到 ETF_FIXED_REBALANCES 補下一年度的公告日/生效日\n"
    "  00929：半年審 6／12 月，無公式 → 同樣到 ETF_FIXED_REBALANCES 補下一年度日期"
)

# 事件類型顯示順序
EVENT_TYPE_ORDER = ["市場休市", "法說會", "股東會", "亞股", "美股", "流動性事件"]


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


# ── 美股 (Nasdaq 自選清單財報 + TradingEconomics 三星經濟事件) ──────────────────

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
    day = today - timedelta(days=1)  # 多抓前一天：美盤後財報換台灣時間會 +1 天進入窗口
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
                us_time = (row.get("time") or "").strip()
                if us_time == "time-pre-market":
                    tw_date, timing = day, "盤後"
                elif us_time == "time-after-hours":
                    tw_date, timing = day + timedelta(days=1), "盤前"
                else:
                    tw_date, timing = day, ""
                if not (today <= tw_date <= cutoff):
                    continue
                events.append({
                    "date": tw_date,
                    "code": ticker,
                    "name": name,
                    "event_type": "美股",
                    "rank": order,
                    "timing": timing,
                })
        except Exception as e:
            print(f"US earnings fetch error ({day}): {e}")
        day += timedelta(days=1)

    print(f"US earnings: {total} total rows, {len(events)} in watchlist")
    return events


def fetch_us_econ_events(today, cutoff):
    """抓 TradingEconomics 美國行事曆中 impact 三星（calendar-date-3）的高影響事件。"""
    events = []
    try:
        r = requests.get(TE_US_CALENDAR_URL, headers=HEADERS, timeout=30)
        r.raise_for_status()
        soup = BeautifulSoup(r.content, "html.parser")
        tbl = soup.find("table", id="calendar")
        if not tbl:
            print("TE calendar table not found")
            return events

        total3 = 0
        for tr in tbl.find_all("tr"):
            # 重要度藏在時間 span 的 class：calendar-date-3 = 三星
            span = tr.find("span", class_="calendar-date-3")
            if not span:
                continue
            total3 += 1

            td0 = tr.find("td")
            datecls = [c for c in (td0.get("class") or [])
                       if re.fullmatch(r"\d{4}-\d{2}-\d{2}", c)] if td0 else []
            if not datecls:
                continue

            # TE 顯示 UTC（絕對時間，不受美國日光節約影響）→ +8 換台灣時間。
            # 跨午夜時台灣日期自然變 T+1；盤前/盤後以台灣股市 09:00 開盤判斷。
            timestr = span.get_text(strip=True)
            try:
                tw_dt = datetime.strptime(f"{datecls[0]} {timestr}", "%Y-%m-%d %I:%M %p") \
                    + timedelta(hours=8)
                ev_date = tw_dt.date()
                timing = "盤前" if tw_dt.hour < 9 else "盤後"
            except ValueError:
                # 無可解析時間（如 All Day）→ 退回用頁面日期、不標盤前後
                try:
                    ev_date = datetime.strptime(datecls[0], "%Y-%m-%d").date()
                except ValueError:
                    continue
                timing = ""
            if not (today <= ev_date <= cutoff):
                continue

            a = tr.find("a", class_="calendar-event")
            name = (a.get_text(strip=True) if a else tr.get("data-event", "")).strip()
            if not name:
                continue

            events.append({
                "date": ev_date,
                "code": "",
                "name": name,
                "event_type": "美股",
                "rank": -1,        # 經濟事件排在個股財報前面
                "timing": timing,
            })

        print(f"US econ (3-star): {total3} in page, {len(events)} in window")
    except Exception as e:
        print(f"US econ fetch error: {e}")
    return events


# ── 亞股 (日／韓個股財報，Yahoo Finance) ────────────────────────────────────────

def load_asia_watchlist():
    """讀 asia_watchlist.txt，回傳 [(symbol, name), ...]（保留順序）。"""
    out = []
    try:
        with open(ASIA_WATCHLIST_FILE, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                sym, _, name = line.partition(",")
                sym = sym.strip()
                if sym:
                    out.append((sym, name.strip() or sym))
        print(f"Asia watchlist: {len(out)} tickers loaded")
    except Exception as e:
        print(f"Asia watchlist load error: {e}")
    return out


def fetch_asia_earnings(today, cutoff, watchlist):
    """用 Yahoo Finance 抓亞股下一次財報日，落在區間內的加入（估計日期標『預估』）。"""
    events = []
    if not watchlist:
        return events
    order = {sym: i for i, (sym, _) in enumerate(watchlist)}
    names = dict(watchlist)
    symbols = [sym for sym, _ in watchlist]

    try:
        s = requests.Session()
        s.headers.update(HEADERS)
        s.get("https://fc.yahoo.com", timeout=30)
        crumb = s.get("https://query1.finance.yahoo.com/v1/test/getcrumb", timeout=30).text.strip()

        results = []
        for i in range(0, len(symbols), 40):
            r = s.get(YAHOO_QUOTE_URL,
                      params={"symbols": ",".join(symbols[i:i + 40]), "crumb": crumb},
                      timeout=30)
            results += r.json().get("quoteResponse", {}).get("result", [])

        for q in results:
            sym = q.get("symbol")
            ts = q.get("earningsTimestampStart") or q.get("earningsTimestamp")
            if not ts:
                continue
            ev_date = datetime.fromtimestamp(ts, TW_TZ).date()
            if not (today <= ev_date <= cutoff):
                continue
            events.append({
                "date": ev_date,
                "code": sym.split(".")[0],
                "name": names.get(sym, sym),
                "event_type": "亞股",
                "rank": order.get(sym, 1e9),
                "timing": "預估" if q.get("isEarningsDateEstimate") else "",
            })
        print(f"Asia earnings: {len(results)} quotes, {len(events)} in window")
    except Exception as e:
        print(f"Asia earnings fetch error: {e}")
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


# ── 流動性事件 (ETF 成分股調整) ─────────────────────────────────────────────────

def _first_friday(year, month):
    first = date(year, month, 1)
    return date(year, month, 1 + (4 - first.weekday()) % 7)


def _third_friday(year, month):
    return _first_friday(year, month) + timedelta(days=14)


def _last_business_day(year, month):
    d = (date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)) - timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


# 審核月份（公告與生效共用）
ETF_REVIEW_MONTHS = {
    "ftse_q": (3, 6, 9, 12),
    "ftse_h": (6, 12),
    "msci_h": (5, 11),
}
# 有「成分股審核公告日」規則的：FTSE 系列＝審核月第一個週五公告（生效前兩週）
# 00878(MSCI) 無明確公告日規則，不產生公告事件
ETF_ANNOUNCE_KINDS = {"ftse_q", "ftse_h"}


def _etf_effective_dates(kind, year):
    months = ETF_REVIEW_MONTHS.get(kind, ())
    if kind in ("ftse_q", "ftse_h"):
        return [_third_friday(year, m) for m in months]
    if kind == "msci_h":
        return [_last_business_day(year, m) for m in months]
    return []


def _etf_announce_dates(kind, year):
    if kind not in ETF_ANNOUNCE_KINDS:
        return []
    return [_first_friday(year, m) for m in ETF_REVIEW_MONTHS.get(kind, ())]


def _trading_window(eff, before, after):
    """生效日 + 前 before / 後 after 個交易日（跳過週六日）。"""
    days = [eff]
    d, cnt = eff, 0
    while cnt < before:
        d -= timedelta(days=1)
        if d.weekday() < 5:
            days.append(d)
            cnt += 1
    d, cnt = eff, 0
    while cnt < after:
        d += timedelta(days=1)
        if d.weekday() < 5:
            days.append(d)
            cnt += 1
    return days


def fetch_etf_rebalances(today, cutoff):
    """規則計算四檔 ETF 成分股調整日，生效日及前後窗口落在區間內就加入。"""
    events = []
    years = {today.year - 1, today.year, cutoff.year}
    for year in sorted(years):
        for etf in ETF_REBALANCE:
            # 成分股審核公告日（僅 FTSE 系列）
            for ann in _etf_announce_dates(etf["kind"], year):
                if today <= ann <= cutoff:
                    events.append({
                        "date": ann,
                        "code": etf["code"],
                        "name": f"{etf['name']}成分股調整",
                        "event_type": "流動性事件",
                        "rank": int(etf["code"]),
                        "timing": "公告",
                    })
            # 生效日 + 前後過渡窗口（每檔各自的 before/after）
            for eff in _etf_effective_dates(etf["kind"], year):
                for d in _trading_window(eff, etf["before"], etf["after"]):
                    if today <= d <= cutoff:
                        events.append({
                            "date": d,
                            "code": etf["code"],
                            "name": f"{etf['name']}成分股調整",
                            "event_type": "流動性事件",
                            "rank": int(etf["code"]),
                            "timing": "生效" if d == eff else "調整期",
                        })
    print(f"ETF rebalance window events: {len(events)}")
    return events


def _forward_trading_days(start, n):
    """從 start（含）起算 n 個交易日（跳過週六日）。"""
    days, d = [], start
    while len(days) < n:
        if d.weekday() < 5:
            days.append(d)
        d += timedelta(days=1)
    return days


def fetch_fixed_rebalances(today, cutoff):
    """展開手動維護的 00919 等已知換股窗口（公告日 + 生效日起 N 個交易日過渡期）。"""
    events = []
    for code, name, ann, eff, span in ETF_FIXED_REBALANCES:
        full_name = f"{name}成分股調整"
        rank = int(code)
        window = _forward_trading_days(eff, span)
        # 公告日（若與生效日不同天才單獨列）
        if ann != eff and today <= ann <= cutoff:
            events.append({
                "date": ann, "code": code, "name": full_name,
                "event_type": "流動性事件", "rank": rank, "timing": "公告",
            })
        for d in window:
            if today <= d <= cutoff:
                if d == eff:
                    timing = "公告生效" if ann == eff else "生效"
                else:
                    timing = "調整期"
                events.append({
                    "date": d, "code": code, "name": full_name,
                    "event_type": "流動性事件", "rank": rank, "timing": timing,
                })
    print(f"Fixed (00919) rebalance events: {len(events)}")
    return events


# ── 市場休市 (台股 + 美股) ──────────────────────────────────────────────────────

def fetch_market_holidays(today, cutoff):
    """六大市場休市日（台/美/日/韓/中/港），落在區間內就加入。"""
    markets = [
        (market_holidays.twse_holidays, "台股", 0),
        (market_holidays.us_market_holidays, "美股", 1),
        (market_holidays.jpx_holidays, "日股", 2),
        (market_holidays.krx_holidays, "韓股", 3),
        (market_holidays.sse_holidays, "陸股", 4),
        (market_holidays.hkex_holidays, "港股", 5),
    ]
    events = []
    for year in sorted({today.year, cutoff.year}):
        for func, label, rank in markets:
            for d, name in func(year).items():
                if today <= d <= cutoff:
                    disp = f"{label}休市（{name}）" if name else f"{label}休市"
                    events.append({"date": d, "code": "", "name": disp,
                                   "event_type": "市場休市", "rank": rank, "timing": ""})
    print(f"Market holidays in window: {len(events)}")
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
                code = ev.get("code")
                code_part = f" (<code>{_esc(code)}</code>)" if code else ""
                lines.append(f"  {name}{code_part}{suffix}")

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

    print("Fetching US economic events (TradingEconomics 3-star)...")
    econ_events = fetch_us_econ_events(today, cutoff)
    filtered += econ_events

    print("Fetching Asia earnings (亞股 watchlist)...")
    asia_events = fetch_asia_earnings(today, cutoff, load_asia_watchlist())
    filtered += asia_events

    print("Fetching TX futures settlement (流動性事件)...")
    tx_events = fetch_tx_settlements(today, cutoff)
    filtered += tx_events

    print("Computing ETF rebalance windows (流動性事件)...")
    etf_events = fetch_etf_rebalances(today, cutoff)
    filtered += etf_events

    print("Expanding fixed (00919) rebalance windows (流動性事件)...")
    fixed_events = fetch_fixed_rebalances(today, cutoff)
    filtered += fixed_events

    print("Fetching market holidays (市場休市)...")
    filtered += fetch_market_holidays(today, cutoff)

    events_by_date: dict = {}
    for ev in filtered:
        events_by_date.setdefault(ev["date"], []).append(ev)

    msg = format_message(events_by_date)
    if today.month == 1 and today.day <= 3:
        msg = msg + "\n\n" + ETF_REVIEW_REMINDER
    print(msg)
    send_telegram(msg)
    print("Done.")


main()
