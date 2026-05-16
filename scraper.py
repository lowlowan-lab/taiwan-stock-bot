import requests
import os
import io
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from datetime import datetime, timedelta

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
    "Referer": "https://www.esunsec.com.tw/",
}

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

# ── 投信買賣超（上市+上櫃）─────────────────────────────────

def fetch_twse_trust(date_str):
    """抓證交所上市投信買賣超，date_str 格式 YYYYMMDD"""
    url = "https://www.twse.com.tw/rwd/zh/fund/BFI82U"
    params = {"date": date_str, "type": "day", "response": "json"}
    try:
        r = requests.get(url, params=params, timeout=15)
        data = r.json()
        if data.get("stat") != "OK":
            return None
        # 找投信那列
        for row in data.get("data", []):
            if "投信" in row[0]:
                buy = float(row[2].replace(",", ""))
                sell = float(row[3].replace(",", ""))
                net = float(row[4].replace(",", ""))
                return net  # 億元
        return None
    except Exception as e:
        print(f"TWSE trust fetch error {date_str}: {e}")
        return None

def fetch_tpex_trust(date_str):
    """抓櫃買中心上櫃投信買賣超，date_str 格式 YYYYMMDD"""
    url = "https://www.tpex.org.tw/rwd/zh/fund/BFI82U"
    params = {"date": date_str, "type": "day", "response": "json"}
    try:
        r = requests.get(url, params=params, timeout=15)
        data = r.json()
        if data.get("stat") != "OK":
            return None
        for row in data.get("data", []):
            if "投信" in row[0]:
                net = float(row[4].replace(",", ""))
                return net
        return None
    except Exception as e:
        print(f"TPEx trust fetch error {date_str}: {e}")
        return None

def get_past_trading_days(n=10):
    """取得過去 n 個交易日（排除週末，簡易版）"""
    days = []
    d = datetime.now()
    # 如果是收盤後推播，今天也算
    while len(days) < n:
        if d.weekday() < 5:  # 週一到週五
            days.append(d)
        d -= timedelta(days=1)
    return list(reversed(days))

def fetch_10day_trust():
    """抓過去10個交易日的投信上市+上櫃買賣超"""
    trading_days = get_past_trading_days(10)
    results = []
    for d in trading_days:
        date_str = d.strftime("%Y%m%d")
        label = d.strftime("%m/%d")
        twse = fetch_twse_trust(date_str)
        tpex = fetch_tpex_trust(date_str)
        if twse is not None and tpex is not None:
            total = twse + tpex
            results.append((label, total))
        elif twse is not None:
            results.append((label, twse))
        elif tpex is not None:
            results.append((label, tpex))
        else:
            results.append((label, None))
    return results

# ── 畫圖 ───────────────────────────────────────────────────

def draw_trust_chart(data):
    """畫10天投信買賣超長條圖，回傳 bytes"""
    labels = [d[0] for d in data if d[1] is not None]
    values = [d[1] for d in data if d[1] is not None]

    if not values:
        return None

    fig, ax = plt.subplots(figsize=(10, 5))
    fig.patch.set_facecolor("#0d1b2a")
    ax.set_facecolor("#0d1b2a")

    colors = ["#ef4444" if v >= 0 else "#22c55e" for v in values]
    bars = ax.bar(labels, values, color=colors, width=0.6, zorder=3)

    # 數值標籤
    for bar, val in zip(bars, values):
        y = bar.get_height()
        va = "bottom" if val >= 0 else "top"
        offset = 0.5 if val >= 0 else -0.5
        ax.text(
            bar.get_x() + bar.get_width() / 2,
            y + offset,
            f"{val:+,.0f}",
            ha="center", va=va,
            fontsize=9, color="white", fontweight="bold"
        )

    ax.axhline(0, color="#475569", linewidth=1, zorder=2)
    ax.set_title("投信買賣超（上市＋上櫃）近10日", color="white", fontsize=14, fontweight="bold", pad=12)
    ax.set_ylabel("億元", color="#94a3b8", fontsize=10)
    ax.tick_params(colors="white", labelsize=9)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_color("#1e293b")
    ax.spines["bottom"].set_color("#1e293b")
    ax.yaxis.label.set_color("#94a3b8")
    ax.grid(axis="y", color="#1e293b", linewidth=0.8, zorder=1)

    buy_patch = mpatches.Patch(color="#ef4444", label="買超（紅）")
    sell_patch = mpatches.Patch(color="#22c55e", label="賣超（綠）")
    ax.legend(handles=[buy_patch, sell_patch], facecolor="#0d1b2a",
              labelcolor="white", fontsize=9, loc="upper left")

    plt.tight_layout()
    buf = io.BytesIO()
    plt.savefig(buf, format="png", dpi=150, facecolor=fig.get_facecolor())
    buf.seek(0)
    plt.close(fig)
    return buf

# ── Telegram ───────────────────────────────────────────────

def send_telegram(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "Markdown"
    }
    r = requests.post(url, json=payload)
    print(f"Telegram text: {r.status_code}")

def send_telegram_photo(buf, caption=""):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto"
    r = requests.post(url, data={"chat_id": TELEGRAM_CHAT_ID, "caption": caption},
                      files={"photo": ("chart.png", buf, "image/png")})
    print(f"Telegram photo: {r.status_code}")

# ── 原本的排行資料 ─────────────────────────────────────────

def fetch_data(url, params):
    r = requests.get(url, params=params, headers=HEADERS, timeout=30)
    data = r.json()
    return data["ResultSet"]["Result"][:10]

def format_message(label, rows):
    lines = [f"*{label}*"]
    lines.append("`#   代碼    名稱       買賣超(千)`")
    lines.append("`" + "─" * 34 + "`")
    for i, row in enumerate(rows, 1):
        code = row["V2"].replace("AS", "").replace("AP", "")
        name = row["V3"][:6]
        amount = f"{int(row['V9']):,}"
        change = float(row["V5"])
        arrow = "▲" if change > 0 else "▼" if change < 0 else "－"
        lines.append(f"`{i:2}. {code:<6} {name:<7} {arrow} {amount:>10}`")
    return "\n".join(lines)

# ── 主程式 ─────────────────────────────────────────────────

def main():
    date_str = datetime.now().strftime("%Y/%m/%d")
    today_str = datetime.now().strftime("%Y%m%d")

    # 1) 投信買賣超加總（今日）
    print("Fetching trust fund data...")
    twse_today = fetch_twse_trust(today_str)
    tpex_today = fetch_tpex_trust(today_str)

    if twse_today is not None and tpex_today is not None:
        total_today = twse_today + tpex_today
        arrow = "🔴 買超" if total_today >= 0 else "🟢 賣超"
        trust_summary = (
            f"📈 *投信今日買賣超（上市＋上櫃）*\n"
            f"🗓 {date_str}\n\n"
            f"上市：`{twse_today:+,.2f}` 億元\n"
            f"上櫃：`{tpex_today:+,.2f}` 億元\n"
            f"合計：`{total_today:+,.2f}` 億元 {arrow}"
        )
    else:
        trust_summary = f"📈 *投信今日買賣超*\n❌ 資料尚未更新（{date_str}）"

    send_telegram(trust_summary)

    # 2) 畫10天圖表
    print("Fetching 10-day trust data...")
    chart_data = fetch_10day_trust()
    chart_buf = draw_trust_chart(chart_data)
    if chart_buf:
        send_telegram_photo(chart_buf, caption="投信買賣超近10日趨勢（上市＋上櫃，億元）")

    # 3) 原本的排行資料
    messages = [f"📊 *三大法人買賣超排行*\n🗓 {date_str}"]
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
