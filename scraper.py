import requests
import os
from datetime import datetime

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
    "Referer": "https://www.esunsec.com.tw/",
}

# 三大法人買賣金額統計（總額，上市+上櫃）
TWSE_BFI_URL = "https://www.twse.com.tw/rwd/zh/fund/BFI82U"          # 上市
TPEX_3INSTI_URL = "https://www.tpex.org.tw/openapi/v1/tpex_3insti_summary"  # 上櫃（openapi，回最新一日）

TARGETS = [
    {
        "label": "🟢 外資買進 TOP10",
        "url": "https://sjis.esunsec.com.tw/b2brwdCommon/jsondata/0c/0f/17/twstockdata.xdjjson",
        "params": {"a": "b", "b": "C", "d": "50", "x": "rank-chip0005-1", "c": "1"}
    },
    {
        "label": "🔴 外資賣出 TOP10",
        "url": "https://sjis.esunsec.com.tw/b2brwdCommon/jsondata/4a/cf/09/twstockdata.xdjjson",
        "params": {"a": "b", "b": "T", "d": "50", "x": "rank-chip0005-1", "c": "1"}
    },
    {
        "label": "🟡 投信買進 TOP10",
        "url": "https://sjis.esunsec.com.tw/b2brwdCommon/jsondata/a2/fc/b7/twstockdata.xdjjson",
        "params": {"a": "b", "b": "C", "d": "50", "x": "rank-chip0013-1", "c": "1"}
    },
    {
        "label": "🟠 投信賣出 TOP10",
        "url": "https://sjis.esunsec.com.tw/b2brwdCommon/jsondata/e3/f2/5a/twstockdata.xdjjson",
        "params": {"a": "b", "b": "T", "d": "50", "x": "rank-chip0013-1", "c": "1"}
    },
]

def _to_int(s):
    try:
        return int(str(s).replace(",", "").strip())
    except (ValueError, TypeError):
        return 0


def fetch_institutional_totals():
    """外資、投信買賣超總額（上市+上櫃），回傳 (外資億, 投信億)；失敗回 None。

    以 TPEX openapi 的最新交易日為準，再用同一天抓 TWSE，確保上市/上櫃同日。
    """
    try:
        # 上櫃（openapi 自動回最新一日）
        tp = requests.get(TPEX_3INSTI_URL, headers=HEADERS, timeout=30).json()
        tpex_foreign = tpex_trust = 0
        roc_date = None
        for r in tp:
            inv = r.get("Investor", "").strip()
            if inv == "外資及陸資合計":
                tpex_foreign = _to_int(r.get("Net"))
                roc_date = r.get("Date")
            elif inv == "投信":
                tpex_trust = _to_int(r.get("Net"))

        # 民國日期 1150612 → 西元 20260612
        if roc_date and len(roc_date) == 7:
            day_date = f"{int(roc_date[:3]) + 1911}{roc_date[3:]}"
        else:
            day_date = datetime.now().strftime("%Y%m%d")

        # 上市
        tw = requests.get(
            TWSE_BFI_URL,
            params={"response": "json", "type": "day", "dayDate": day_date},
            headers=HEADERS, timeout=30,
        ).json()
        twse_foreign = twse_trust = 0
        for row in tw.get("data", []):
            nm = row[0].strip()
            net = _to_int(row[3])
            if nm in ("外資及陸資(不含外資自營商)", "外資自營商"):
                twse_foreign += net
            elif nm == "投信":
                twse_trust = net

        return (twse_foreign + tpex_foreign) / 1e8, (twse_trust + tpex_trust) / 1e8
    except Exception as e:
        print(f"Institutional totals error: {e}")
        return None


def fetch_data(url, params):
    r = requests.get(url, params=params, headers=HEADERS, timeout=30)
    data = r.json()
    return data["ResultSet"]["Result"][:10]

def format_message(label, rows):
    lines = [f"*{label}*"]
    lines.append("`#   代碼    名稱       買賣超(百萬)`")
    lines.append("`" + "─" * 34 + "`")
    for i, row in enumerate(rows, 1):
        code = row["V2"].replace("AS", "").replace("AP", "")
        name = row["V3"][:6]
        # V9 原始單位為千元，轉成百萬元（÷1000）讓數字短一點
        amount = f"{int(row['V9']) / 1000:,.0f}"
        change = float(row["V5"])
        arrow = "▲" if change > 0 else "▼" if change < 0 else "－"
        lines.append(f"`{i:2}. {code:<6} {name:<7} {arrow} {amount:>10}`")
    return "\n".join(lines)

def send_telegram(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    r = requests.post(url, json={
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "Markdown"
    })
    print(f"Telegram: {r.status_code}")

def main():
    date_str = datetime.now().strftime("%Y/%m/%d")
    header = f"📊 *三大法人買賣超排行*\n🗓 {date_str}"

    totals = fetch_institutional_totals()
    if totals:
        foreign_yi, trust_yi = totals
        header += (
            f"\n\n外資：`{foreign_yi:+.1f}` 億元"
            f"\n投信：`{trust_yi:+.1f}` 億元"
        )

    messages = [header]

    for target in TARGETS:
        print(f"Fetching: {target['label']}")
        try:
            rows = fetch_data(target["url"], target["params"])
            msg = format_message(target["label"], rows)
            messages.append(msg)
        except Exception as e:
            messages.append(f"❌ {target['label']} 失敗：{e}")

    send_telegram("\n\n".join(messages))
    print("Done.")

main()
