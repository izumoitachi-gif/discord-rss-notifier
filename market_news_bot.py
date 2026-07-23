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
    # 2026-07-23 世界市場ノイズ削減（実投稿見て発見・パパ指摘）
    "市場調査レポート", "市場規模", "業界動向 予測", "グローバルインフォメーション",
    "価格予測", "Tokenised Stock", "xStock", "Bitget",
    "自動水門", "半導体市場調査", "化学市場", "産業レポート",
    "アフィリエイト", "MLM", "副業", "投資助言", "投資顧問",
]

def rss(url, include=None, exclude=None, label=None, title_include=None, title_exclude=None):
    return {"type": "rss", "url": url, "include": include or [], "exclude": exclude or [],
            "label": label, "title_include": title_include or [], "title_exclude": title_exclude or []}

def gn(q, include=None, exclude=None, label=None, title_include=None, title_exclude=None):
    return {"type": "gn", "q": q, "include": include or [], "exclude": exclude or [],
            "label": label, "title_include": title_include or [], "title_exclude": title_exclude or []}

# ---- Phase 2: 一次開示・中銀政策（設計§7.2 DISCLOSURE_IMMEDIATE / MACRO_RELEASE準拠）----
# 「金融政策決定会合」「総裁講演」「為替介入」等の重要イベントだけ通す(緩めると「オペ結果」
# 「生活意識アンケート」等の日次ノイズが混ざる)
BOJ_POLICY_TERMS = ["金融政策決定会合", "決定会合", "総裁", "副総裁", "展望レポート",
                     "短観", "金融システムレポート", "介入", "利上げ", "利下げ",
                     "無担保コール", "指値オペ", "国債買入(通知)"]
# ECB Press全般が入る枠でECB内広報インタビュー等のノイズを除外
ECB_MACRO_TERMS = ["monetary policy", "interest rate", "policy rate", "inflation",
                    "outlook", "governing council", "Christine Lagarde", "de Guindos",
                    "銀行融資調査", "金融安定", "TLTRO", "APP", "PEPP", "リーガーデ",
                    "利上げ", "利下げ", "金融政策"]

# ---- Phase 3: 世界市場（クロスアセット・リスク選好・地政学）2026-07-23追加 ----
# パパ指摘「世界市場動いてない、RSSと絞り込みが足りない」→ 激裏カタログ(自分\激裏 ニュース・情報源
# 取得ルート全宇宙.md PART2/5)から実応答確認済みの7ソース＋Google News 1本の計8本で厳選構成。
# BBC/AlJazeera/DW/France24/CNN/NYTは全世界ニュース全般のフィードなのでtitle_includeで
# 市場語に絞り込む。Reuters公式RSSは廃止済みのためGoogle News経由(site:reuters.com)で代替。
WORLD_MARKET_TERMS = ["market", "markets", "stocks", "stock", "index", "indices", "S&P", "Nasdaq",
                      "Dow Jones", "dollar", "yen", "yuan", "euro", "pound", "bond", "bonds", "yield",
                      "yields", "rate hike", "rate cut", "interest rate", "inflation", "recession",
                      "GDP", "trade war", "tariff", "tariffs", "oil price", "crude", "gold price",
                      "risk-off", "risk-on", "sell-off", "selloff", "rally", "volatility", "股", "相場"]

TSE_TITLE_EXCLUDE = [
    # ETF/月次ルーチン開示のみ弾く。譲渡制限付株式/払込完了は重要度あり残す
    # (パパ実確認2026-07-23で「同じ5件が繰り返し」問題は根本的にはseen機構の話で、
    #  過剰フィルタで新規記事を消したことが遠因だった)
    "日々の開示事項",
    "運用実績", "月次運用", "月次資産運用",
    "投資証券に係る", "ETFの収益分配金",
]

TOPICS = [
    {"num": "④", "name": "TSE適時開示", "env": "TSE", "color": COL_TSE, "per_channel": 12, "sources": [
        rss("https://webapi.yanoshin.jp/webapi/tdnet/list/recent.rss",
            title_exclude=TSE_TITLE_EXCLUDE,
            label="TDnet（やのしんWEB-API）"),
    ]},
    {"num": "②/⑤", "name": "中銀・政策・SEC速報", "env": "MACRO", "color": COL_MACRO, "per_channel": 8, "sources": [
        rss("https://www.federalreserve.gov/feeds/press_all.xml",
            # Fed press全部は多すぎる(執行措置・支店人事等)ので金融政策・規制関連に絞る
            title_include=["Federal Reserve", "monetary", "FOMC", "interest rate", "rate decision",
                            "outlook", "Powell", "vice chair", "policy statement",
                            "利上げ", "利下げ", "金融政策"],
            title_exclude=["enforcement action", "consent order", "取締役会の割引率会議"],
            label="Federal Reserve Press Releases"),
        rss("https://www.ecb.europa.eu/rss/press.html",
            title_include=ECB_MACRO_TERMS, label="ECB Press"),
        rss("https://www.boj.or.jp/rss/whatsnew.xml",
            title_include=BOJ_POLICY_TERMS, label="日本銀行 新着情報"),
        rss("https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=8-K&company=&dateb=&owner=include&count=40&output=atom",
            label="SEC EDGAR 8-K (getcurrent)"),
    ]},
    {"num": "①", "name": "世界市場速報", "env": "WORLD", "color": 0x00E5FF, "per_channel": 8, "sources": [
        gn('(world markets OR stock market OR risk sentiment OR global economy) (乱高下 OR 急落 OR 急騰 OR 波乱 OR 動向)',
           label="Google News（世界市場）"),
        gn('site:reuters.com (market OR markets OR stocks OR economy)', label="Reuters（Google News経由）"),
        rss("https://feeds.bbci.co.uk/news/business/rss.xml", title_include=WORLD_MARKET_TERMS, label="BBC Business"),
        rss("https://www.aljazeera.com/xml/rss/all.xml", title_include=WORLD_MARKET_TERMS, label="Al Jazeera"),
        rss("https://rss.dw.com/rdf/rss-en-all", title_include=WORLD_MARKET_TERMS, label="Deutsche Welle"),
        rss("https://www.france24.com/en/rss", title_include=WORLD_MARKET_TERMS, label="France24"),
        rss("http://rss.cnn.com/rss/edition.rss", title_include=WORLD_MARKET_TERMS, label="CNN"),
        rss("https://rss.nytimes.com/services/xml/rss/nyt/World.xml", title_include=WORLD_MARKET_TERMS, label="NYT World"),
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
    # 末尾の発行元表記(- ロイター/- Reuters/- BBC等)を全部剥がす→dedupが効く
    t = re.sub(r"\s+-\s+[^-|｜]+$", "", title or "")
    t = re.sub(r"\s*\([^)]{2,50}\)\s*$", "", t)
    t = re.sub(r"\s*（[^）]{2,50}）\s*$", "", t)
    # 記号ゆらぎ吸収
    t = t.replace("　", " ").replace("’", "'").replace("’", "'")
    t = re.sub(r"[「」『』\"'“”]", "", t)
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
    # sl=auto は「末尾に日本語表記(- ロイター等)が3文字あるだけで元言語=ja」と誤判定し、
    # 翻訳せず原文をそのまま返すバグ実測（2026-07-23）。sl=en に固定して強制英日翻訳する。
    # is_ja() で先に日本語と判定されたものはそもそもここに到達しないので副作用なし。
    url = "https://translate.googleapis.com/translate_a/single?" + urllib.parse.urlencode({
        "client": "gtx", "sl": "en", "tl": "ja", "dt": "t", "q": text[:4800]
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
        # summaryはタイトルと冒頭18字丸被りだけを空扱いに緩和（旧: 25字未満も空にしていたが
        # TDnet/BOJ/Fedはそもそも短いor無いのでdesc空欄になる問題があった）
        if summ and summ[:18] == title[:18]:
            summ = ""
        summ = summ[:250] + ("…" if len(summ) > 250 else "")
        out.append((title, e.get("link", ""), summ, src.get("label") or entry_source or feed_label))
        if len(out) >= PER_SOURCE: break
    return out

# ---- 「何が起きたか」補完: summary空の時にGoogle Newsで関連記事1件を引く ----
# 呼び出し頻度を絞るため、TSE以外(=英語ニュース中心)かつsummary空の記事だけに適用する。
_REASON_FAIL_STREAK = 0
_REASON_MAX_FAIL = 3
_REASON_USED_HINTS = set()  # 同一ヒントを同一ジャンル内で二重に使わない
_STOP_WORDS = {"the","and","for","with","from","that","this","have","has","was","are","been",
               "will","its","not","new","one","two","2026","2025"}
def _extract_keywords(title):
    """タイトルから内容語(4文字以上)を抽出。固有名詞優先。"""
    tokens = re.findall(r"[A-Za-z][a-zA-Z0-9]{3,}|[ァ-ヶー]{3,}|[一-龠]{2,}", title)
    return [t for t in tokens if t.lower() not in _STOP_WORDS][:8]

def fetch_reason_hint(title, timeout=8):
    """タイトルから固有名詞を抽出→Google News検索→固有名詞1個以上マッチした結果のみ採用。
    無関係なK-POP等のノイズを弾く。"""
    global _REASON_FAIL_STREAK
    if _REASON_FAIL_STREAK >= _REASON_MAX_FAIL:
        return ""
    keywords = _extract_keywords(title)
    if not keywords:
        return ""
    # 2段構え検索: ①AND2件必須で高精度に取る ②見つからなければ緩めて1件必須で再検索
    # 「無関係キーワードが混入した瞬間NG」判定用の禁止語(=直近ノイズ実例)
    NOISE_TERMS = ["気候変動", "熱波", "山火事", "会計調査", "K-POP", "解散",
                    "grieve", "追悼", "訃報", "死去", "訳:",
                    "オークション", "紙幣", "auction", "auctioned",
                    "サンリオ", "アイドル", "音楽祭", "映画"]
    def _is_noise(hint_title):
        low = hint_title.lower()
        return any(nt.lower() in low for nt in NOISE_TERMS)

    def _too_similar(hint_title):
        """タイトルとhintの内容語(4字以上)が50%以上重複したら同じネタとみなす"""
        orig_kws = set(k.lower() for k in _extract_keywords(title))
        hint_kws = set(k.lower() for k in _extract_keywords(hint_title))
        if not orig_kws or not hint_kws:
            return False
        overlap = len(orig_kws & hint_kws)
        smaller = min(len(orig_kws), len(hint_kws))
        return smaller > 0 and (overlap / smaller) >= 0.5

    def _search(need, use_top):
        q = " ".join(keywords[:use_top])
        url = "https://news.google.com/rss/search?q=" + urllib.parse.quote(q) + "&hl=ja&gl=JP&ceid=JP:ja"
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        raw = urllib.request.urlopen(req, timeout=timeout).read()
        f = feedparser.parse(raw)
        for e in f.entries[:8]:
            hint_title = clean(e.get("title", ""))
            if not hint_title or hint_title[:18] == title[:18]:
                continue
            if _is_noise(hint_title):
                continue  # 無関係トピック混入は即弾く
            if hint_title in _REASON_USED_HINTS:
                continue  # 同一ヒントの重複禁止
            if _too_similar(hint_title):
                continue  # 元タイトルと内容がほぼ同じ続報も弾く
            hits = sum(1 for kw in keywords if kw.lower() in hint_title.lower())
            if hits >= need:
                return hint_title[:150]
        return ""
    try:
        need2 = 2 if len(keywords) >= 2 else 1
        result = _search(need=need2, use_top=4)
        if not result and need2 >= 2:
            result = _search(need=1, use_top=3)   # 緩めて再検索
        if result:
            _REASON_FAIL_STREAK = 0
            _REASON_USED_HINTS.add(result)
        return result
    except Exception:
        _REASON_FAIL_STREAK += 1
        return ""

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
    picked_kw_sets = []  # 既に採用した記事の内容語セット集(類似排除用)
    def _sim_dup(title):
        """既採用記事と内容語(4字以上)が50%以上重複したら類似記事とみなす"""
        new_kws = set(k.lower() for k in _extract_keywords(title))
        if not new_kws:
            return False
        for prev in picked_kw_sets:
            if not prev:
                continue
            overlap = len(new_kws & prev)
            smaller = min(len(new_kws), len(prev))
            if smaller > 0 and (overlap / smaller) >= 0.5:
                return True
        return False
    def add_item(item):
        key = item[1] or canonical_title(item[0])
        if key in picked_keys:
            return
        if _sim_dup(item[0]):
            return  # 中銀政策で同じ内容の連続記事を弾く
        picked_keys.add(key)
        picked_kw_sets.append(set(k.lower() for k in _extract_keywords(item[0])))
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
        # 補完は翻訳前のオリジナルタイトル(英語)でGoogle News検索→固有名詞照合
        # TSEは日本語一次開示で本文がPDFなので補完スキップ
        enriched = []
        for (ti, li, su, sr) in picked:
            if not su and t["env"] != "TSE":
                hint = fetch_reason_hint(ti)  # ← まだ翻訳前(英語含む原題)
                if hint:
                    su = f"🗞️関連: {hint}"
                    time.sleep(0.4)
            enriched.append((ti, li, su, sr))
        picked = [(to_ja(ti), li, to_ja(su), sr) for (ti, li, su, sr) in enriched]  # 英語→日本語（失敗時は原文）
        st = post(url, f"**{t['num']}｜{t['name']}**", picked, t["color"])
        for _, link, _, _ in picked: seen.add(link)
        print(f"{t['num']} {t['name']} … {len(picked)}件 ({st})")
        time.sleep(1.3)
    save_seen(seen); print("seen保存:", len(seen))

if __name__ == "__main__":
    main()
