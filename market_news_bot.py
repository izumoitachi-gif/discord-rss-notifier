#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ================================================================
# market_news_bot.py — 金融市場Bot Phase 2/3（開示・政策・市場ニュース速報）
# ----------------------------------------------------------------
# ailab_news_bot.py（AIラボ15分類・実運用中）の構造をそのまま継承した派生スクリプト。
# 方式: rss()/gn() ヘルパーでsourceを宣言→4層フィルタ→重複排除→Webhook投稿。
# AI分類は使わず route_id / topic で分類を固定（IC倶楽部/AIラボと同じ方式）。
#
# 設計正本: 自分/金融市場_全ジャンル入力・取得クエリ設計_パットン市場通知Bot.md §7.2/§Phase2-3
# 実装計画: .claude/中期記憶/Discord/金融市場Bot_パットンMS_20260716/01_計画_実装Phase.md
# 接続前チェック: 同フォルダ 06_接続前チェックリスト.md（source別に実URL確認済み・2026-07-23）
#
# 2026-07-23 実URL確認結果（WebFetch/curlで実応答を確認済み・推測ではない）:
#   ✅ TDnet    : https://webapi.yanoshin.jp/webapi/tdnet/list/recent.rss（無料・登録不要・やのしん非公式API）
#   ✅ SEC EDGAR: https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent（無料・User-Agent必須・登録不要）
#   ✅ Fed RSS  : https://www.federalreserve.gov/feeds/press_all.xml（無料・登録不要）
#   ✅ ECB RSS  : https://www.ecb.europa.eu/rss/press.html（無料・登録不要・.htmlだが中身はRSS）
#   ✅ BOJ RSS  : https://www.boj.or.jp/rss/whatsnew.xml（無料・登録不要・新着全般につきtitle_includeで政策系に絞る）
#   ⏸️ EDINET   : https://api.edinet-fsa.go.jp/api/v2/documents.json（Subscription-Key必須＝氏名/メール/電話番号の
#                 個人情報登録＋多要素認証が必要と判明。個人情報入力はパパ自身の作業が必要なため今回は保留）
#   ⏸️ FRED     : https://api.stlouisfed.org/fred/series/observations（32文字APIキー必須＝要アカウント登録。保留）
#
# 使い方:
#   1) pip install feedparser
#   2) Webhookを環境変数で（GitHub Actionsは Secrets 推奨）:
#        MARKET_WEBHOOK_TSE / MARKET_WEBHOOK_MACRO / MARKET_WEBHOOK_SOURCELOG
#   3) ローカルテストは環境変数が無ければ同ディレクトリ market_webhooks.json を fallback
#   4) python market_news_bot.py
#   5) 自動化: .github/workflows/market_notify.yml で cron '25,55 * * * *'
# 重複排除: 同ディレクトリ market_seen_urls.json（ailab_seen_urls.jsonとは別ファイル）
# 投稿POSTには User-Agent 必須（特にSEC EDGARはUser-Agentにメール等連絡先を含めるfair access policy）
# ================================================================
import os, sys, io, json, time, re, urllib.parse, urllib.request, urllib.error
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
try:
    import feedparser
except ImportError:
    import subprocess
    subprocess.run([sys.executable, "-m", "pip", "install", "-q", "feedparser"])
    import feedparser

HERE = os.path.dirname(os.path.abspath(__file__))
SEEN_FILE = os.path.join(HERE, "market_seen_urls.json")
WEBHOOKS_JSON = os.path.join(HERE, "market_webhooks.json")
PER_SOURCE = 5     # TDnetのような高頻度一次開示は多めに拾う（AIラボの2件より広め）
PER_CHANNEL_DEFAULT = 6
# SEC EDGARはfair access policyでUser-Agentに連絡先を含める必要がある
UA = "KurenaiMarketMS/1.0 (izumoitachi@gmail.com; +https://discord.com)"
COL_TSE, COL_MACRO = 0x00E5FF, 0xFFC107

GLOBAL_EXCLUDE = [
    "PR TIMES", "プレスリリース", "アットプレス", "valuepress",
    "求人", "採用", "セミナー", "イベント開催", "ライブ配信", "ウェビナー",
    "キャンペーン", "無料公開", "広告", "Sponsored",
]

def rss(url, include=None, exclude=None, label=None, title_include=None, title_exclude=None):
    return {"type": "rss", "url": url, "include": include or [], "exclude": exclude or [],
            "label": label, "title_include": title_include or [], "title_exclude": title_exclude or []}

def gn(q, include=None, exclude=None, label=None, title_include=None, title_exclude=None):
    return {"type": "gn", "q": q, "include": include or [], "exclude": exclude or [],
            "label": label, "title_include": title_include or [], "title_exclude": title_exclude or []}

# ---- Phase 2: 一次開示・中銀政策（設計§7.2 DISCLOSURE_IMMEDIATE / MACRO_RELEASE準拠）----
BOJ_POLICY_TERMS = ["金融政策", "決定会合", "総裁", "副総裁", "講演", "為替", "国債買入", "オペ", "展望レポート",
                     "生活意識", "短観", "金融システムレポート", "金融政策決定会合"]

TOPICS = [
    {"num": "④", "name": "TSE適時開示", "env": "TSE", "color": COL_TSE, "per_channel": 12, "sources": [
        rss("https://webapi.yanoshin.jp/webapi/tdnet/list/recent.rss",
            label="TDnet（やのしんWEB-API）"),
    ]},
    {"num": "②/⑤", "name": "中銀・政策・SEC速報", "env": "MACRO", "color": COL_MACRO, "per_channel": 8, "sources": [
        rss("https://www.federalreserve.gov/feeds/press_all.xml",
            label="Federal Reserve Press Releases"),
        rss("https://www.ecb.europa.eu/rss/press.html",
            label="ECB Press"),
        rss("https://www.boj.or.jp/rss/whatsnew.xml",
            title_include=BOJ_POLICY_TERMS, label="日本銀行 新着情報"),
        rss("https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=8-K&company=&dateb=&owner=include&count=40&output=atom",
            label="SEC EDGAR 8-K (getcurrent)"),
    ]},
]

def gn_url(q):
    return "https://news.google.com/rss/search?q=" + urllib.parse.quote(q) + "&hl=ja&gl=JP&ceid=JP:ja"

def clean(t):
    t = re.sub(r"<[^>]+>", " ", t or "")
    t = t.replace("&nbsp;", " ").replace("&amp;", "&").replace("&#39;", "'").replace("&quot;", '"').replace("&lt;", "<").replace("&gt;", ">")
    return re.sub(r"\s+", " ", t).strip()

def contains_any(text, terms):
    if not terms: return True
    low = text.lower()
    for term in terms:
        needle = str(term).lower()
        if not needle:
            continue
        if len(needle) <= 2 and needle.isascii() and needle.isalnum():
            if re.search(rf"(?<![a-z0-9]){re.escape(needle)}(?![a-z0-9])", low):
                return True
            continue
        if needle in low:
            return True
    return False

def canonical_title(title):
    t = re.sub(r"\s+-\s+[^|]+(?:\s+\|.*)?$", "", title or "")
    t = re.sub(r"\s*\([^)]{2,50}\)\s*$", "", t)
    t = re.sub(r"\s*（[^）]{2,50}）\s*$", "", t)
    return re.sub(r"\s+", " ", t).strip().lower()

# ---- 日本語化（ailab_news_bot.pyから移植・英語タイトル/要約をGoogle翻訳で和訳・失敗時は原文）----
# SEC/Fed/ECBは英語のまま来るため必須。ailab_news_bot.pyと同じソケットレベルtimeout＋回路遮断方式。
_JP_RE = re.compile(r"[ぁ-んァ-ヶ一-龠]")
_TR_FAIL_STREAK = 0
_TR_TIMEOUT_SEC = 6
_TR_MAX_FAIL_STREAK = 3

def is_ja(t):
    if not t: return True
    return len(_JP_RE.findall(t)) >= max(3, int(len(t) * 0.12))

def _gtranslate(text, timeout):
    url = "https://translate.googleapis.com/translate_a/single?" + urllib.parse.urlencode({
        "client": "gtx", "sl": "auto", "tl": "ja", "dt": "t", "q": text[:4800]
    })
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return "".join(seg[0] for seg in data[0] if seg and seg[0])

def to_ja(t):
    global _TR_FAIL_STREAK
    if not t or is_ja(t): return t
    if _TR_FAIL_STREAK >= _TR_MAX_FAIL_STREAK:
        return t
    try:
        result = _gtranslate(t, _TR_TIMEOUT_SEC)
        _TR_FAIL_STREAK = 0
        return result or t
    except Exception:
        _TR_FAIL_STREAK += 1
        if _TR_FAIL_STREAK >= _TR_MAX_FAIL_STREAK:
            print(f"    翻訳サーバー応答なし({_TR_TIMEOUT_SEC}s×{_TR_MAX_FAIL_STREAK}回連続) → 以後は原文のまま投稿")
        return t

def load_webhooks():
    m = {}
    for k, v in os.environ.items():
        if k.startswith("MARKET_WEBHOOK_"):
            m[k.replace("MARKET_WEBHOOK_", "")] = v
    if not m and os.path.exists(WEBHOOKS_JSON):
        with io.open(WEBHOOKS_JSON, encoding="utf-8") as f:
            data = json.load(f)
        for slug, w in data.items():
            if w.get("url"):
                m[slug] = w["url"]
    return m

def load_seen():
    try: return set(json.load(io.open(SEEN_FILE, encoding="utf-8")))
    except Exception: return set()

def save_seen(s):
    io.open(SEEN_FILE, "w", encoding="utf-8").write(json.dumps(sorted(s), ensure_ascii=False))

def fetch(src):
    url = gn_url(src["q"]) if src["type"] == "gn" else src["url"]
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        raw = urllib.request.urlopen(req, timeout=20).read()
        f = feedparser.parse(raw)
    except Exception as e:
        print("    fetch失敗:", url[:70], e); return []
    feed_label = clean((f.feed.get("title", "") if getattr(f, "feed", None) else "")) or urllib.parse.urlparse(url).netloc
    include = src.get("include") or []
    exclude = GLOBAL_EXCLUDE + (src.get("exclude") or [])
    title_include = src.get("title_include") or []
    title_exclude = src.get("title_exclude") or []
    out = []
    seen_titles = set()
    for e in f.entries[: PER_SOURCE * 6]:
        title = clean(e.get("title", ""))
        if not title: continue
        summ = clean(e.get("summary", "") or e.get("description", ""))
        entry_source = e.get("source", {})
        if isinstance(entry_source, dict):
            entry_source = clean(entry_source.get("title", ""))
        else:
            entry_source = ""
        filter_text = " ".join([title, summ, entry_source])
        title_key = canonical_title(title)
        if title_key in seen_titles: continue
        if title_include and not contains_any(title, title_include): continue
        if title_exclude and contains_any(title, title_exclude): continue
        if include and not contains_any(filter_text, include): continue
        if exclude and contains_any(filter_text, exclude): continue
        seen_titles.add(title_key)
        if len(summ) < 25 or summ[:18] == title[:18]: summ = ""
        summ = summ[:200] + ("…" if len(summ) > 200 else "")
        out.append((title, e.get("link", ""), summ, src.get("label") or entry_source or feed_label))
        if len(out) >= PER_SOURCE: break
    return out

def collect_topic_items(t, seen, sleep_sec=0.4):
    per_channel = t.get("per_channel", PER_CHANNEL_DEFAULT)
    source_hits = []
    keys = set()
    for src in t["sources"]:
        rows = []
        for title, link, summ, srclabel in fetch(src):
            title_key = canonical_title(title)
            if not link or link in seen or link in keys or title_key in keys:
                continue
            keys.add(link)
            keys.add(title_key)
            rows.append((title, link, summ, srclabel))
        if rows:
            source_hits.append(rows)
        if sleep_sec:
            time.sleep(sleep_sec)

    picked = []
    picked_keys = set()
    def add_item(item):
        key = item[1] or canonical_title(item[0])
        if key in picked_keys:
            return
        picked_keys.add(key)
        picked.append(item)

    for rows in source_hits:
        for item in rows:
            if len(picked) >= per_channel:
                break
            add_item(item)
        if len(picked) >= per_channel:
            break
    return picked[:per_channel]

def post(url, header, items, color):
    embeds = []
    for title, link, summ, src in items:
        emb = {"title": title[:250], "url": link, "color": color, "footer": {"text": src[:100]}}
        if summ: emb["description"] = summ
        embeds.append(emb)
    body = {"content": header, "embeds": embeds[:10]}
    r = urllib.request.Request(url, data=json.dumps(body, ensure_ascii=False).encode(),
        headers={"Content-Type": "application/json", "User-Agent": UA}, method="POST")
    while True:
        try:
            with urllib.request.urlopen(r, timeout=20) as x: return x.status
        except urllib.error.HTTPError as e:
            if e.code == 429:
                try: retry = float(json.loads(e.read()).get("retry_after", 1.0))
                except Exception: retry = 1.0
                time.sleep(retry + 0.3); continue
            return f"{e.code}:{e.read().decode('utf-8','replace')[:120]}"

def main():
    hooks = load_webhooks(); seen = load_seen()
    print(f"webhooks={len(hooks)} seen={len(seen)} topics={len(TOPICS)}")
    for t in TOPICS:
        url = hooks.get(t["env"])
        if not url:
            print(f"{t['num']} {t['name']} … webhook未設定スキップ"); continue
        picked = collect_topic_items(t, seen)
        if not picked:
            print(f"{t['num']} {t['name']} … 新規なし"); continue
        picked = [(to_ja(ti), li, to_ja(su), sr) for (ti, li, su, sr) in picked]  # 英語→日本語（失敗時は原文）
        st = post(url, f"**{t['num']}｜{t['name']}**", picked, t["color"])
        for _, link, _, _ in picked: seen.add(link)
        print(f"{t['num']} {t['name']} … {len(picked)}件 ({st})")
        time.sleep(1.3)
    save_seen(seen); print("seen保存:", len(seen))

if __name__ == "__main__":
    main()
