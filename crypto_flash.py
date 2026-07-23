#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ================================================================
# crypto_flash.py — 段階2: 21RSS→4分割+1CH集約(フック増設・故障許容)
# ----------------------------------------------------------------
# パパ要件5-47/5-48「21を4つに分けてCHに追加」→ **1CH+4フック**
# パパ要件「1本の単純化・速報の意味を守る=事後報告にしない」対応
# ----------------------------------------------------------------
# 環境変数 FLASH_GROUP=A/B/C/D で担当5-6 RSS を切替
# → 各RSSから最新5件取得
# → 速報キーワード判定 (BREAKING_TERMS 26語)
# → BTC/ETH/SOL/OTHER 銘柄振分
# → 日本語翻訳 (to_ja sl=en強制)
# → 既存 crypto_seen_news.json でURL dedup
# → 同じ MARKET_WEBHOOK_CRYPTO (₿|暗号資産速報 CH) にPush
#
# 4フック並列で全21RSSを5〜10分で1周する構造
# 各フック5-6 RSSに絞ることでGitHub Actions内実行時間短縮→scheduled発火通過率UP
# seen_urls共有で重複投稿ゼロ・pull-rebaseリトライで衝突回避
# ================================================================
import os, sys, io, json, time, urllib.request, urllib.error, urllib.parse, re

try:
    import feedparser
except ImportError:
    import subprocess
    subprocess.run([sys.executable, "-m", "pip", "install", "-q", "feedparser"])
    import feedparser

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
SEEN_NEWS_FILE = os.path.join(HERE, "crypto_seen_news.json")
WEBHOOKS_JSON = os.path.join(HERE, "market_webhooks.json")
UA = "KurenaiMarketMS/1.0 (izumoitachi@gmail.com; +https://discord.com)"
MAX_NEWS_HISTORY = 500
COL_FLASH = 0xFFC107
SYMBOL_EMOJI = {"BTC": "₿", "ETH": "Ξ", "SOL": "◎", "OTHER": "⚡"}
SYMBOL_TERMS = {
    "BTC": ["bitcoin", "btc", "ビットコイン"],
    "ETH": ["ethereum", "eth", "vitalik", "イーサリアム", "イーサ"],
    "SOL": ["solana", "sol", "ソラナ"],
}
BREAKING_TERMS = [
    "hack", "hacked", "exploit", "flash crash", "flash-crash", "surge",
    "plunge", "plummet", "soar", "rally", "crash", "liquidation",
    "ETF approval", "SEC lawsuit", "SEC settle", "bank run", "delist",
    "listing", "halted", "outage",
    "ハッキング", "急落", "急騰", "暴落", "暴騰", "承認", "規制",
    "上場", "上場廃止", "取引停止", "清算"
]

# 21RSS を4グループに分割 (5+5+5+6 本・信頼度/地域/分野別)
RSS_GROUPS = {
    "A": [  # 通信社系 5本
        ("Cointelegraph", "https://cointelegraph.com/rss"),
        ("Decrypt", "https://decrypt.co/feed"),
        ("The Block", "https://www.theblock.co/rss.xml"),
        ("CryptoSlate", "https://cryptoslate.com/feed/"),
        ("Bitcoinist", "https://bitcoinist.com/feed/"),
    ],
    "B": [  # アグリゲータ系 5本
        ("CryptoPotato", "https://cryptopotato.com/feed/"),
        ("NewsBTC", "https://www.newsbtc.com/feed/"),
        ("AMBCrypto", "https://ambcrypto.com/feed/"),
        ("BeInCrypto", "https://beincrypto.com/feed/"),
        ("CryptoBriefing", "https://cryptobriefing.com/feed/"),
    ],
    "C": [  # DeFi/専門 5本
        ("CoinGape", "https://coingape.com/feed/"),
        ("U.Today", "https://u.today/rss.php"),
        ("CryptoNews", "https://cryptonews.com/news/feed/"),
        ("Blockworks", "https://blockworks.com/rss.xml"),
        ("The Defiant", "https://thedefiant.io/api/feed"),
    ],
    "D": [  # JP+Reddit+その他 6本
        ("CryptoDaily", "https://cryptodaily.co.uk/feed"),
        ("CoinJournal", "https://coinjournal.net/news/feed/"),
        ("ZyCrypto", "https://zycrypto.com/feed/"),
        ("CoinSpeaker", "https://www.coinspeaker.com/news/feed/"),
        ("BitcoinMagazine", "http://bitcoinmagazine.com/feed"),
        ("DailyHodl", "https://dailyhodl.com/feed/"),
        # Reddit / 日本語RSS は Group D の任意追加候補(rate limit配慮で今回は6本)
    ],
}

# --- 日本語化(sl=en強制) ---
_JP_RE = re.compile(r"[ぁ-んァ-ヶ一-龠]")
_TR_FAIL_STREAK = 0
_TR_MAX = 3
def is_ja(t):
    if not t: return True
    return len(_JP_RE.findall(t)) >= max(3, int(len(t) * 0.12))
def to_ja(text):
    global _TR_FAIL_STREAK
    if not text or is_ja(text): return text
    if _TR_FAIL_STREAK >= _TR_MAX: return text
    try:
        url = "https://translate.googleapis.com/translate_a/single?" + urllib.parse.urlencode({
            "client": "gtx", "sl": "en", "tl": "ja", "dt": "t", "q": text[:4800]
        })
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=6) as r:
            data = json.loads(r.read().decode("utf-8"))
        _TR_FAIL_STREAK = 0
        return "".join(seg[0] for seg in data[0] if seg and seg[0]) or text
    except Exception:
        _TR_FAIL_STREAK += 1
        return text

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

def load_seen_news():
    try:
        return set(json.load(io.open(SEEN_NEWS_FILE, encoding="utf-8")))
    except Exception:
        return set()

def save_seen_news(s):
    io.open(SEEN_NEWS_FILE, "w", encoding="utf-8").write(
        json.dumps(sorted(list(s))[-MAX_NEWS_HISTORY:], ensure_ascii=False))

def is_breaking(title, summary=""):
    text = f"{title} {summary}".lower()
    for term in BREAKING_TERMS:
        if term.lower() in text:
            return True, term
    return False, None

def classify_symbol(text):
    low = text.lower()
    for sym, terms in SYMBOL_TERMS.items():
        if any(t.lower() in low for t in terms):
            return sym
    return "OTHER"

def fetch_group_breaking(group_key, seen_urls):
    sources = RSS_GROUPS.get(group_key, [])
    if not sources:
        print(f"Group '{group_key}' が未定義")
        return []
    buckets = {"BTC": [], "ETH": [], "SOL": [], "OTHER": []}
    for src_name, src_url in sources:
        try:
            req = urllib.request.Request(src_url, headers={"User-Agent": UA})
            raw = urllib.request.urlopen(req, timeout=10).read()
            f = feedparser.parse(raw)
            for e in f.entries[:5]:
                link = e.get("link", "")
                if not link or link in seen_urls:
                    continue
                title = (e.get("title", "") or "").strip()
                summary = (e.get("summary", "") or e.get("description", "") or "")[:200]
                hit, matched_term = is_breaking(title, summary)
                if hit:
                    sym = classify_symbol(f"{title} {summary}")
                    buckets[sym].append({
                        "source": src_name, "title": title, "link": link,
                        "matched": matched_term, "symbol": sym
                    })
        except Exception as ex:
            print(f"  RSS取得失敗: {src_name} - {ex}")
    # 銘柄別1件優先で最大4件(1フックあたり過剰にならない)
    picked = []
    for sym in ["BTC", "ETH", "SOL", "OTHER"]:
        picked.extend(buckets[sym][:1])
    return picked[:4]

def post_flash(webhook_url, items, group_key):
    if not items:
        return None
    embeds = []
    for it in items:
        ja_title = to_ja(it["title"])
        badge = SYMBOL_EMOJI.get(it.get("symbol", "OTHER"), "⚡")
        embeds.append({
            "title": f"{badge} {ja_title[:200]}",
            "url": it["link"],
            "color": COL_FLASH,
            "footer": {"text": f"{it['source']} / 速報: {it['matched']} / 紅月市場MS[flash-{group_key}]"},
        })
    body = {"content": f"**⚡ 暗号速報 {len(items)}件** (グループ{group_key}・{len(RSS_GROUPS[group_key])}RSS走査)",
            "embeds": embeds}
    req = urllib.request.Request(webhook_url, data=json.dumps(body, ensure_ascii=False).encode(),
        headers={"Content-Type": "application/json", "User-Agent": UA}, method="POST")
    while True:
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                return r.status
        except urllib.error.HTTPError as e:
            if e.code == 429:
                try: retry = float(json.loads(e.read()).get("retry_after", 1.0))
                except Exception: retry = 1.0
                time.sleep(retry + 0.3); continue
            return f"{e.code}"

def main():
    group_key = os.environ.get("FLASH_GROUP", "A").upper()
    hooks = load_webhooks()
    url = hooks.get("CRYPTO")
    if not url:
        print("MARKET_WEBHOOK_CRYPTO 未設定・スキップ")
        return
    seen = load_seen_news()
    print(f"Group {group_key} 開始・既視URL数: {len(seen)}")
    items = fetch_group_breaking(group_key, seen)
    if items:
        by_sym = {}
        for i in items:
            by_sym[i['symbol']] = by_sym.get(i['symbol'], 0) + 1
        print(f"速報{len(items)}件検出 (銘柄別: {by_sym})")
        st = post_flash(url, items, group_key)
        print(f"投稿: {st}")
        for i in items:
            seen.add(i["link"])
        save_seen_news(seen)
    else:
        print(f"Group {group_key} 速報なし({len(RSS_GROUPS[group_key])}RSS走査済み)")

if __name__ == "__main__":
    main()
