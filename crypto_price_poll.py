#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ================================================================
# crypto_price_poll.py — 金融市場Bot Phase1(A案v2) BTC/ETH/SOL 1時間ポーリング速報
# ----------------------------------------------------------------
# WebSocket常駐(Fly.io等の課金常駐)ではなく、GitHub Actions cron(1時間毎)で
# 価格APIを叩き、毎回必ず現在値を投稿する（閾値超のみだと「1件も来ない日」が
# 発生しうるためパパの指摘で採用=常時スナップショット＋閾値超過時のみ強調表示）。
# 閾値を超えた時だけ「何が原因で動いたか」の手がかりとしてGoogle Newsの直近ニュースを
# 添付する（数値だけ出しても意味がない、というパパの指摘への対応）。
#
# 設計正本: 自分/金融市場_全ジャンル入力・取得クエリ設計_パットン市場通知Bot.md §7.2
# 実装計画: .claude/中期記憶/Discord/金融市場Bot_パットンMS_20260716/01_計画_実装Phase.md
#
# 2026-07-23 v2の変更点（パパ指摘に基づく）:
#   1) データソース: Binance公式API→CoinGeckoに変更
#      理由: GitHub Actionsランナー(米国リージョン)からBinance.comを叩くと
#      HTTP 451(Unavailable For Legal Reasons)で地理的ブロックされることが実機で判明。
#      CoinGeckoは取引所ではなく価格集約APIのため地理的制限を受けない。
#   2) 投稿頻度: 5分毎→1時間毎（「投稿頻繁じゃなくていい」との指摘）
#   3) 閾値超過時: 単なる数値だけでなく、Google Newsでその銘柄名+急変動キーワードを
#      検索し、直近ニュースのタイトルを添付（「何がどう動いてどう変わった、
#      何の原因でとかの情報無いと基準がない」との指摘への対応）
#   4) 投稿先チャンネルは非公開カテゴリへ移動済み（phase1b_private_category.py）
#      @everyoneのVIEW_CHANNEL拒否・パパのみ許可=他メンバーへのノイズを完全に消す
#
# 使い方:
#   1) Webhookを環境変数で: MARKET_WEBHOOK_CRYPTO
#   2) ローカルテストは環境変数が無ければ同ディレクトリ market_webhooks.json を fallback
#   3) python crypto_price_poll.py
#   4) 自動化: .github/workflows/crypto_price_notify.yml で cron '0 * * * *'（毎時0分）
# 履歴: 同ディレクトリ crypto_price_history.json（symbol毎に直近72件=3日分の
#       {ts_ms, price} を保持。1時間前に一番近いスナップショットと比較する）
# ================================================================
import os, sys, io, json, time, urllib.request, urllib.error, urllib.parse

try:
    import feedparser
except ImportError:
    import subprocess
    subprocess.run([sys.executable, "-m", "pip", "install", "-q", "feedparser"])
    import feedparser

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
HISTORY_FILE = os.path.join(HERE, "crypto_price_history.json")
WEBHOOKS_JSON = os.path.join(HERE, "market_webhooks.json")
UA = "KurenaiMarketMS/1.0 (izumoitachi@gmail.com; +https://discord.com)"

# CoinGecko ID / Discord表示ラベル / ニュース検索用の日英表記
SYMBOLS = ["bitcoin", "ethereum", "solana"]
SYMBOL_LABEL = {"bitcoin": "₿ BTC", "ethereum": "Ξ ETH", "solana": "◎ SOL"}
SYMBOL_NEWS_QUERY = {
    "bitcoin": '(ビットコイン OR Bitcoin OR BTC)',
    "ethereum": '(イーサリアム OR Ethereum OR ETH)',
    "solana": '(ソラナ OR Solana OR SOL)',
}
PRICE_MOVE_THRESHOLD_PCT = {"bitcoin": 3.0, "ethereum": 3.0, "solana": 5.0}
MAX_HISTORY_PER_SYMBOL = 72  # 1時間間隔×72 = 3日分
TARGET_INTERVAL_MS = 60 * 60 * 1000  # 1時間前と比較
TOLERANCE_MS = 20 * 60 * 1000        # ±20分まで許容
COL_NORMAL = 0x00E5FF
COL_ALERT_UP = 0x00E676
COL_ALERT_DOWN = 0xFF3B30

def load_webhooks():
    m = {}
    for k, v in os.environ.items():
        if k.startswith("MARKET_WEBHOOK_"):
            m[k.replace("MARKET_WEBHOOK_", "")] = v
    if "CRYPTO" not in m and os.path.exists(WEBHOOKS_JSON):
        with io.open(WEBHOOKS_JSON, encoding="utf-8") as f:
            data = json.load(f)
        for slug, w in data.items():
            if w.get("url"):
                m.setdefault(slug, w["url"])
    return m

def load_history():
    try:
        with io.open(HISTORY_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {s: [] for s in SYMBOLS}

def save_history(hist):
    with io.open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(hist, f, ensure_ascii=False)

def fetch_prices():
    """CoinGecko coins/markets（地理的制限なし・APIキー不要。simple/priceは24h高値/安値を
    返さないため、こちらを使う）"""
    ids = ",".join(SYMBOLS)
    url = "https://api.coingecko.com/api/v3/coins/markets?vs_currency=usd&ids=" + ids
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=20) as r:
        rows = json.loads(r.read())
    return {row["id"]: row for row in rows}

def nearest_past(snapshots, now_ms):
    if not snapshots:
        return None
    target_time = now_ms - TARGET_INTERVAL_MS
    best = min(snapshots, key=lambda s: abs(s["ts_ms"] - target_time))
    if abs(best["ts_ms"] - target_time) > TOLERANCE_MS:
        return None
    return best

def fetch_reason_news(symbol_id, change_pct, is_alert):
    """パパ要件5-29「その数値か？」対応：閾値超過時2件・通常時も1件は原因ヒント添付する
    ノイズ回避のためNOISE_TERMSで無関係トピック弾く+同一hint重複禁止"""
    NOISE_TERMS = ["気候変動", "熱波", "山火事", "K-POP", "解散", "追悼", "訃報", "死去",
                    "オークション", "紙幣", "映画", "アイドル", "音楽祭"]
    # 通常時は0.3%未満でも呼び出し側から意味ある閾値を渡すので受け入れる（パパ要件5-29対応）
    direction_up = change_pct > 0
    if is_alert:
        direction_q = "急騰 OR 高騰 OR 上昇" if direction_up else "急落 OR 暴落 OR 下落"
        wanted = 2
    else:
        direction_q = "上昇 OR 高値 OR 反発 OR 買い" if direction_up else "下落 OR 安値 OR 売り"
        wanted = 1
    q = f"{SYMBOL_NEWS_QUERY[symbol_id]} ({direction_q})"
    url = "https://news.google.com/rss/search?q=" + urllib.parse.quote(q) + "&hl=ja&gl=JP&ceid=JP:ja"
    try:
        f = feedparser.parse(url)
        items = []
        for e in f.entries[:8]:
            title = (e.get("title", "") or "").strip()
            link = e.get("link", "")
            if not title or not link:
                continue
            if any(nt in title for nt in NOISE_TERMS):
                continue
            items.append((title[:80], link))
            if len(items) >= wanted:
                break
        return items
    except Exception as e:
        print(f"    ニュース検索失敗: {e}")
        return []

def build_embed(symbol_id, data, past_snapshot, now_ms):
    price = float(data["current_price"])
    label = SYMBOL_LABEL[symbol_id]
    threshold = PRICE_MOVE_THRESHOLD_PCT[symbol_id]

    if past_snapshot:
        change_pct = (price - past_snapshot["price"]) / past_snapshot["price"] * 100.0
        elapsed_min = (now_ms - past_snapshot["ts_ms"]) / 60000.0
        is_alert = abs(change_pct) >= threshold
        change_txt = f"{change_pct:+.2f}%（約{elapsed_min:.0f}分前比）"
    else:
        change_pct = None
        is_alert = False
        change_txt = "（履歴不足・次回から変化率を計算）"

    reason_lines = ""
    # パパ要件5-29「その数値か？」対応：閾値超過じゃなくても24h変化率で理由ヒント取る
    change_24h = data.get("price_change_percentage_24h") or 0.0
    ref_change = change_pct if change_pct is not None else change_24h
    if is_alert:
        color = COL_ALERT_UP if change_pct > 0 else COL_ALERT_DOWN
        title = f"🚨 {label} 1時間変化率 {change_pct:+.2f}%（閾値±{threshold}%超）"
        news = fetch_reason_news(symbol_id, change_pct, is_alert=True)
        if news:
            reason_lines = "\n\n**なぜ動いた？（原因の手がかり）:**\n" + "\n".join(
                f"・[{t}]({l})" for t, l in news)
        else:
            reason_lines = "\n\n（関連ニュース見つからず・単独の値動きの可能性）"
    else:
        color = COL_NORMAL
        # 通常時も24h変化率でヒント添付(パパ要件5-29「その数値か？」対応・毎回背景を出す)
        # 24h変化率で方向判定(1h変化がゼロ近くても1日単位では意味ある動きがあるため)
        news = fetch_reason_news(symbol_id, change_24h if abs(change_24h) >= 0.2 else 0.5, is_alert=False)
        if news:
            reason_lines = f"\n\n**背景（24h {change_24h:+.2f}%の要因ヒント）:** [{news[0][0]}]({news[0][1]})"
        title = f"{label}  ${price:,.2f}"

    day_high = data.get("high_24h")
    day_low = data.get("low_24h")
    day_vol = data.get("total_volume")
    desc_lines = [f"現在値: **${price:,.2f}**", f"変化: {change_txt}"]
    if day_high is not None and day_low is not None:
        desc_lines.append(f"24h高値/安値: ${float(day_high):,.2f} / ${float(day_low):,.2f}")
    if day_vol is not None:
        desc_lines.append(f"24h出来高(USD): ${float(day_vol):,.0f}")
    desc = "\n".join(desc_lines) + reason_lines

    return {"title": title, "description": desc, "color": color,
            "footer": {"text": "1時間ポーリング（GitHub Actions）/ 紅月市場MS"}}

def post_webhook(url, embeds):
    body = {"embeds": embeds}
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST",
        headers={"Content-Type": "application/json", "User-Agent": UA})
    while True:
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                return r.status
        except urllib.error.HTTPError as e:
            if e.code == 429:
                try:
                    retry = float(json.loads(e.read()).get("retry_after", 1.0))
                except Exception:
                    retry = 1.0
                time.sleep(retry + 0.3)
                continue
            return f"{e.code}:{e.read().decode('utf-8','replace')[:150]}"

def main():
    hooks = load_webhooks()
    url = hooks.get("CRYPTO")
    if not url:
        print("MARKET_WEBHOOK_CRYPTO 未設定・スキップ")
        return
    hist = load_history()
    now_ms = int(time.time() * 1000)
    prices = fetch_prices()

    embeds = []
    for symbol_id in SYMBOLS:
        data = prices.get(symbol_id)
        if not data:
            print(f"{symbol_id}: レスポンスなし・スキップ")
            continue
        snapshots = hist.setdefault(symbol_id, [])
        past = nearest_past(snapshots, now_ms)
        embeds.append(build_embed(symbol_id, data, past, now_ms))
        snapshots.append({"ts_ms": now_ms, "price": float(data["current_price"])})
        hist[symbol_id] = snapshots[-MAX_HISTORY_PER_SYMBOL:]
        time.sleep(0.5)

    if embeds:
        st = post_webhook(url, embeds)
        print(f"投稿: {len(embeds)}件 ({st})")
    save_history(hist)
    print("履歴保存完了")

if __name__ == "__main__":
    main()
