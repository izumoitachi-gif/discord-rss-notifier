#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ================================================================
# crypto_price_poll.py — 金融市場Bot Phase1(A案) BTC/ETH/SOL 5分ポーリング速報
# ----------------------------------------------------------------
# WebSocket常駐(Fly.io等の課金常駐)ではなく、GitHub Actions cron(5分間隔=仕様上の最短)で
# Binance REST APIを叩き、毎回必ず現在値を投稿する(閾値超のみだと「1件も来ない日」が
# 発生しうるためパパの指摘で採用=常時スナップショット＋閾値超過時のみ強調表示)。
# APIキー不要（Binance Public REST・登録不要）。
#
# 設計正本: 自分/金融市場_全ジャンル入力・取得クエリ設計_パットン市場通知Bot.md §7.2
# 実装計画: .claude/中期記憶/Discord/金融市場Bot_パットンMS_20260716/01_計画_実装Phase.md
#
# 2026-07-23 確定の経緯:
#   当初Phase1はBinance WebSocket常駐(binance_ws_client.py)で設計したが、
#   「いちいちPython立ち上げてらんねーよ」「タスクスケジューラ禁止」「Fly.io課金NG(生活費最優先)」
#   の3点でパパPC手動起動・Windowsタスクスケジューラ・Fly.io常駐を全部除外。
#   GitHub Actions cronの最短5分間隔(*/5 * * * *、これ未満は仕様上サイレントに無視される)に
#   格下げして採用。ミリ秒観測は諦めるが¥0・パパの手作業ゼロで運用できる。
#
# 使い方:
#   1) Webhookを環境変数で: MARKET_WEBHOOK_CRYPTO
#   2) ローカルテストは環境変数が無ければ同ディレクトリ market_webhooks.json を fallback
#   3) python crypto_price_poll.py
#   4) 自動化: .github/workflows/crypto_price_notify.yml で cron '*/5 * * * *'
# 履歴: 同ディレクトリ crypto_price_history.json（symbol毎に直近288件=24時間分の
#       {ts_ms, price, quote_volume} を保持。5分前に一番近いスナップショットと比較する）
# ================================================================
import os, sys, io, json, time, urllib.request, urllib.error, urllib.parse

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
HISTORY_FILE = os.path.join(HERE, "crypto_price_history.json")
WEBHOOKS_JSON = os.path.join(HERE, "market_webhooks.json")
UA = "KurenaiMarketMS/1.0 (izumoitachi@gmail.com; +https://discord.com)"

SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
SYMBOL_LABEL = {"BTCUSDT": "₿ BTC", "ETHUSDT": "Ξ ETH", "SOLUSDT": "◎ SOL"}
PRICE_MOVE_THRESHOLD_PCT = {"BTCUSDT": 3.0, "ETHUSDT": 3.0, "SOLUSDT": 5.0}  # binance_ws_client.pyと同じ閾値
MAX_HISTORY_PER_SYMBOL = 288  # 5分間隔×288 = 24時間分
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

def fetch_tickers():
    symbols_json = json.dumps(SYMBOLS).replace(" ", "")
    url = "https://api.binance.com/api/v3/ticker/24hr?symbols=" + urllib.parse.quote(symbols_json)
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read())

def nearest_5min_ago(snapshots, now_ms, target_ms=5 * 60 * 1000):
    """5分前に一番近いスナップショットを返す（無ければNone）"""
    if not snapshots:
        return None
    target_time = now_ms - target_ms
    best = min(snapshots, key=lambda s: abs(s["ts_ms"] - target_time))
    # 5分±3分の範囲外なら「5分前」として使うには離れすぎ
    if abs(best["ts_ms"] - target_time) > 3 * 60 * 1000:
        return None
    return best

def build_embed(symbol, ticker, past_snapshot, now_ms):
    price = float(ticker["lastPrice"])
    label = SYMBOL_LABEL[symbol]
    threshold = PRICE_MOVE_THRESHOLD_PCT[symbol]

    if past_snapshot:
        change_pct = (price - past_snapshot["price"]) / past_snapshot["price"] * 100.0
        elapsed_min = (now_ms - past_snapshot["ts_ms"]) / 60000.0
        is_alert = abs(change_pct) >= threshold
        change_txt = f"{change_pct:+.2f}%（約{elapsed_min:.0f}分前比）"
    else:
        change_pct = None
        is_alert = False
        change_txt = "（履歴不足・次回から変化率を計算）"

    if is_alert:
        color = COL_ALERT_UP if change_pct > 0 else COL_ALERT_DOWN
        title = f"🚨 {label} 5分変化率 {change_pct:+.2f}%（閾値±{threshold}%超）"
    else:
        color = COL_NORMAL
        title = f"{label}  ${price:,.2f}"

    desc = (
        f"現在値: **${price:,.2f}**\n"
        f"変化: {change_txt}\n"
        f"24h高値/安値: ${float(ticker['highPrice']):,.2f} / ${float(ticker['lowPrice']):,.2f}\n"
        f"24h出来高: {float(ticker['volume']):,.2f} {symbol.replace('USDT','')}"
    )
    return {"title": title, "description": desc, "color": color,
            "footer": {"text": f"5分ポーリング（GitHub Actions・仕様上の最短間隔）/ 紅月市場MS"}}

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
    tickers = {t["symbol"]: t for t in fetch_tickers()}

    embeds = []
    for symbol in SYMBOLS:
        ticker = tickers.get(symbol)
        if not ticker:
            print(f"{symbol}: レスポンスなし・スキップ")
            continue
        snapshots = hist.setdefault(symbol, [])
        past = nearest_5min_ago(snapshots, now_ms)
        embeds.append(build_embed(symbol, ticker, past, now_ms))
        snapshots.append({"ts_ms": now_ms, "price": float(ticker["lastPrice"]),
                           "quote_volume": float(ticker["quoteVolume"])})
        hist[symbol] = snapshots[-MAX_HISTORY_PER_SYMBOL:]

    if embeds:
        st = post_webhook(url, embeds)
        print(f"投稿: {len(embeds)}件 ({st})")
    save_history(hist)
    print("履歴保存完了")

if __name__ == "__main__":
    main()
