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

# ── 抓證交所上市投信買賣超 ─────────────────────────────────

def fetch_twse_trust(date_str):
    """抓證交所上市投信買賣超，date_str 格式 YYYYMMDD，回傳億元"""
    url = "https://www.twse.com.tw/rwd/zh/fund/BFI82U"
    params = {"date": date_str, "type": "day", "response": "json"}
    try:
        r = requests.get(url, params=params, timeout=15)
        data = r.json()
        if data.get("stat") != "OK":
            return None
        for row in data.get("data", []):
            if row[0].startswith("投信"):
                net = float(row[3].replace(",", "")) / 1e8  # 元 → 億
                return net
        return None
    except Exception as e:
        print(f"TWSE trust fetch error {date_str}: {e}")
        return None

def fetch_tpex_trust(date_str):
    """抓櫃買中心上櫃投信買賣超，date_str 格式 YYYYMMDD，回傳億元"""
    url = "https://www.tpex.org.tw/rwd/zh/fund/BFI82U"
    params = {"date": date_str, "type": "day", "response": "json"}
    try:
        r = requests.get(url, params=params, timeout=15)
        data = r.json()
        if data.get("stat") != "OK":
            return None
        for row in data.get("data", []):
            if "投信" in row[0]:
                net = float(row[3].replace(",", "")) / 1e8  # 元 → 億
                return net
        return None
    except Exception as e:
        print(f"TPEx trust fetch error {date_str}: {e}")
        return None

# ── 取得過去 N 個交易日 ────────────────────────────────────

def get_past_trading_days(n=10):
    """取得過去 n 個交易日（排除週末）"""
    days = []
    d = datetime.now()
    while len(days) < n:
        if d.weekday() < 5:
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
            results.append((label, twse + tpex))
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
    valid = [(d, v) for d, v in data if v is not None]
    if not valid:
        return None

    labels = [d[0] for d in valid]
    values = [d[1] for d in valid]

    fig, ax = plt.subplots(figsize=(10, 5))
    fig.patch.set_facecolor("#0d1b2a")
    ax.set_facecolor("#0d1b2a")

    colors = ["#ef4444" if v >= 0 else "#22c55e" for v in values]
    bars = ax.bar(labels, values, color=colors, width=0.6, zorder=3)

    for bar, val in zip(bars, values):
        y = bar.get_height()
        va = "bottom" if val >= 0 else "top"
        offset = max(abs(val) * 0.02, 0.3)
        offset = offset if val >= 0 else -offset
        ax.text(
            bar.get_x() + bar.get_width() / 2,
            y + offset,
            f"{val:+.1f}",
            ha="center", va=va,
            fontsize=9, color="white", fontweight="bold"
        )

    ax.axhline(0, color="#475569", linewidth=1, zorder=2)
    ax.set_title("投信買賣超（上市＋上櫃）近10日（億元）",
                 color="white", fontsize=14, fontweight="bold", pad=12)
    ax.set_ylabel("億元", color="#94a3b8", fontsize=10)
    ax.tick_params(colors="white", labelsize=9)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_color("#1e293b")
    ax.spines["bottom"].set_color("#1e293b")
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
    r = requests.post(url, json={
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "Markdown"
    })
    print(f"Telegram text: {r.status_code}")

def send_telegram_photo(buf, caption=""):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto"
    r = requests.post(url,
        data={"chat_id": TELEGRAM_CHAT_ID, "caption": caption},
        files={"photo": ("chart.png", buf, "image/png")}
    )
    print(f"Telegram photo: {r.status_code}")

# ── 主程式 ─────────────────────────────────────────────────

def main():
    date_str = datetime.now().strftime("%Y/%m/%d")
    today_str = datetime.now().strftime("%Y%m%d")

    print("Fetching today's trust fund data...")
    twse_today = fetch_twse_trust(today_str)
    tpex_today = fetch_tpex_trust(today_str)

    if twse_today is not None and tpex_today is not None:
        total = twse_today + tpex_today
        direction = "買超 🔴" if total >= 0 else "賣超 🟢"
        msg = (
            f"📈 *投信買賣超（上市＋上櫃）*\n"
            f"🗓 {date_str}\n\n"
            f"上市：`{twse_today:+.2f}` 億元\n"
            f"上櫃：`{tpex_today:+.2f}` 億元\n"
            f"合計：`{total:+.2f}` 億元　{direction}"
        )
    elif twse_today is not None:
        msg = (
            f"📈 *投信買賣超（僅上市）*\n"
            f"🗓 {date_str}\n\n"
            f"上市：`{twse_today:+.2f}` 億元\n"
            f"⚠️ 上櫃資料暫無"
        )
    else:
        msg = f"📈 *投信買賣超*\n🗓 {date_str}\n\n❌ 資料尚未更新，請稍後再查"

    send_telegram(msg)

    print("Fetching 10-day trust data...")
    chart_data = fetch_10day_trust()
    chart_buf = draw_trust_chart(chart_data)
    if chart_buf:
        send_telegram_photo(chart_buf, caption="投信買賣超近10日（上市＋上櫃，億元）")
    else:
        send_telegram("⚠️ 圖表無法生成（資料不足）")

    print("Done.")

main()
