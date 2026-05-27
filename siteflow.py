import os
import io
import time
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import requests
from datetime import datetime
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

URL_LISTED   = "https://www.esunsec.com.tw/tw-rank/b2brwd/page/afterHours/market/0001"
URL_OTC      = "https://www.esunsec.com.tw/tw-rank/b2brwd/page/afterHours/market/0002"

# ── Selenium 設定 ──────────────────────────────────────────

def make_driver():
    from selenium.webdriver.chrome.service import Service
    from webdriver_manager.chrome import ChromeDriverManager
    opts = Options()
    opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--window-size=1280,800")
    opts.add_argument("--remote-debugging-port=9222")
    opts.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36")
    service = Service(ChromeDriverManager().install())
    return webdriver.Chrome(service=service, options=opts)

# ── 抓資料 ─────────────────────────────────────────────────

def scrape_trust_table(url):
    """
    抓嘉實資訊盤後頁面的明細表格
    回傳 list of (date_str, trust_val) 按日期舊→新排列
    """
    driver = make_driver()
    results = []
    try:
        driver.get(url)
        # 等表格出現
        wait = WebDriverWait(driver, 20)
        wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "table tbody tr")))
        time.sleep(3)  # 等 JS 完全渲染

        rows = driver.find_elements(By.CSS_SELECTOR, "table tbody tr")
        for row in rows:
            cells = row.find_elements(By.TAG_NAME, "td")
            if len(cells) < 3:
                continue
            date_text = cells[0].text.strip()
            trust_text = cells[2].text.strip()  # 投信欄位

            # 過濾年份行（只有年份沒有月份）
            if not date_text or "/" not in date_text:
                continue
            # 清理數字
            try:
                trust_val = float(trust_text.replace(",", ""))
                results.append((date_text, trust_val))
            except:
                continue
    except Exception as e:
        print(f"Selenium error: {e}")
    finally:
        driver.quit()

    return list(reversed(results))  # 舊→新

# ── 畫圖 ───────────────────────────────────────────────────

def draw_chart(data_listed, data_otc):
    """
    合併上市+上櫃，畫近10日長條圖
    data 格式: [(date_str, value), ...]
    """
    # 合併：找共同日期
    otc_dict = dict(data_otc)
    combined = []
    for date, listed_val in data_listed:
        otc_val = otc_dict.get(date, 0)
        combined.append((date, listed_val + otc_val))

    # 取最近10筆
    combined = combined[-10:]
    if not combined:
        return None

    labels = [d[0] for d in combined]
    values = [d[1] for d in combined]

    fig, ax = plt.subplots(figsize=(10, 5))
    fig.patch.set_facecolor("#0d1b2a")
    ax.set_facecolor("#0d1b2a")

    colors = ["#ef4444" if v >= 0 else "#22c55e" for v in values]
    bars = ax.bar(labels, values, color=colors, width=0.6, zorder=3)

    for bar, val in zip(bars, values):
        y = bar.get_height()
        va = "bottom" if val >= 0 else "top"
        offset = max(abs(val) * 0.02, 0.5)
        offset = offset if val >= 0 else -offset
        ax.text(
            bar.get_x() + bar.get_width() / 2,
            y + offset,
            f"{val:+.1f}",
            ha="center", va=va,
            fontsize=9, color="white", fontweight="bold"
        )

    ax.axhline(0, color="#475569", linewidth=1, zorder=2)
    ax.set_title("Trust Fund Net Buy/Sell (Listed+OTC) - Past 10 Days (100M TWD)",
                 color="white", fontsize=11, fontweight="bold", pad=12)
    ax.set_ylabel("100M TWD", color="#94a3b8", fontsize=10)
    ax.tick_params(colors="white", labelsize=9)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_color("#1e293b")
    ax.spines["bottom"].set_color("#1e293b")
    ax.grid(axis="y", color="#1e293b", linewidth=0.8, zorder=1)

    buy_patch = mpatches.Patch(color="#ef4444", label="Net Buy")
    sell_patch = mpatches.Patch(color="#22c55e", label="Net Sell")
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
    print(f"Scraping listed market...")
    data_listed = scrape_trust_table(URL_LISTED)
    print(f"Got {len(data_listed)} rows from listed")

    print(f"Scraping OTC market...")
    data_otc = scrape_trust_table(URL_OTC)
    print(f"Got {len(data_otc)} rows from OTC")

    # 今日數字
    today_listed = data_listed[-1][1] if data_listed else None
    today_otc = data_otc[-1][1] if data_otc else None

    if today_listed is not None and today_otc is not None:
        total = today_listed + today_otc
        direction = "買超 🔴" if total >= 0 else "賣超 🟢"
        msg = (
            f"📈 *投信買賣超（上市＋上櫃）*\n"
            f"🗓 {date_str}\n\n"
            f"上市：`{today_listed:+.2f}` 億元\n"
            f"上櫃：`{today_otc:+.2f}` 億元\n"
            f"合計：`{total:+.2f}` 億元　{direction}"
        )
    elif today_listed is not None:
        msg = (
            f"📈 *投信買賣超*\n"
            f"🗓 {date_str}\n\n"
            f"上市：`{today_listed:+.2f}` 億元\n"
            f"⚠️ 上櫃資料抓取失敗"
        )
    else:
        msg = f"📈 *投信買賣超*\n🗓 {date_str}\n\n❌ 資料抓取失敗"

    send_telegram(msg)

    # 畫圖
    chart_buf = draw_chart(data_listed, data_otc)
    if chart_buf:
        send_telegram_photo(chart_buf,
            caption=f"Trust Fund Net Buy/Sell (Listed+OTC) - Past 10 Days (100M TWD) - {date_str}")
    else:
        send_telegram("⚠️ Chart unavailable")

    print("Done.")

main()
