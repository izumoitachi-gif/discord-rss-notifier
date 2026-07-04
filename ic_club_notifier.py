# -*- coding: utf-8 -*-
"""
IC倶楽部「毎日ニュース5分類」自動通知（GitHub Actions版・一回実行して終了）

森田さんの毎日ニュース5分類プロジェクト用。こころ(Codex)が設計した
毎日ニュース5分類_取得クエリ設計_厳重版.md のクエリバンクから、各分類
5本ずつ実データ検証済みのGoogle Newsクエリ（+③④はNHK政治カテゴリ直RSS）
を選抜して実装。2026-07-05、旧4ソース構成(direct_rss全般/gdelt/openalex)
が実データ検証で軒並み0件通過だったため全面差し替え。
notifier.py（AI技術ブログ用）とは完全に別系統・別Webhook・別チャンネル群。

2種類の入口ソースを使い分ける：
  google_news : Google News RSS検索（動的クエリ、掛け算構造で既に絞り込み済み）
  direct_rss  : 個別サイトの固定RSS + トピック別キーワードフィルタ

重要：TOPIC_FILTERS(掛け算式フィルタ)は "filter": True を明示したソースにのみ適用する。
google_newsは検索クエリ自体が「主語 AND 領域 AND 行動」の掛け算になっており、
Google側が意味理解込みで絞り込み済みのため、その結果にさらに単純文字列マッチの
TOPIC_FILTERSを重ねると、的確な記事まで機械的に弾いてしまうことが実証された
（例："高齢の親をAI詐欺から守る"は主語アンカー"高齢者"の完全一致がなく誤ってブロックされる）。
direct_rss(NHK政治カテゴリ全体等、母集団が広いソース)にはTOPIC_FILTERSを適用して絞り込む。

必要な環境変数（GitHub Secrets）：
  DISCORD_WEBHOOK_TOPIC1 ... ①｜後期高齢者ai挑戦
  DISCORD_WEBHOOK_TOPIC2 ... ②｜老後不安ガイド
  DISCORD_WEBHOOK_TOPIC3 ... ③｜高市政権
  DISCORD_WEBHOOK_TOPIC4 ... ④｜政治ニュース
  DISCORD_WEBHOOK_TOPIC5 ... ⑤｜自己肯定感サポート
"""

import sys
import io
import os

if sys.stdout.encoding is None or sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")

import feedparser
import requests
import json
import re
import time
import urllib.parse
from datetime import datetime

# ============================================================
# 設定：各トピック Google Newsクエリ中心の計5ソース（③④はNHK政治カテゴリRSSを1本含む）
# ============================================================

NEWS_TOPICS = {
    "①後期高齢者のAI挑戦": {
        "webhook_env": "DISCORD_WEBHOOK_TOPIC1",
        "sources": [
            {"type": "google_news",
             "query": '("高齢者" OR "シニア" OR "後期高齢者" OR "75歳以上") ("生成AI" OR ChatGPT OR AI) (活用 OR 講座 OR 使い方 OR 事例 OR 支援)'},
            {"type": "google_news",
             "query": '("高齢者" OR シニア) ("デジタルデバイド" OR "デジタル格差" OR "デジタル活用支援") (総務省 OR 自治体 OR 講習会 OR 支援員)'},
            {"type": "google_news",
             "query": '("高齢者" OR シニア) (スマホ OR スマートフォン OR LINE OR タブレット) (使い方 OR 講座 OR 教室 OR 支援 OR 相談)'},
            {"type": "google_news",
             "query": '("高齢者" OR シニア) (スマホ OR SNS OR インターネット OR AI) (詐欺 OR フィッシング OR 被害 OR 対策 OR 相談)'},
            {"type": "google_news",
             "query": '("高齢者" OR シニア) (AI OR スマホ OR デジタル) (自治体 OR 公民館 OR 社協 OR シルバー人材センター OR 講習会)'},
        ],
    },
    "②高齢者の老後不安を解消する完全ガイド": {
        "webhook_env": "DISCORD_WEBHOOK_TOPIC2",
        "sources": [
            {"type": "google_news",
             "query": '(高齢者 OR シニア OR 老後) ("老後資金" OR 年金 OR 生活費 OR 物価高 OR 医療費 OR 介護費) (不安 OR 対策 OR 支援 OR 制度 OR 改正 OR 調査)'},
            {"type": "google_news",
             "query": '("高齢者" OR シニア OR 高齢世帯 OR 独居) (孤独 OR 孤立 OR 見守り OR 居場所 OR つながり) (対策 OR 自治体 OR 支援 OR 調査)'},
            {"type": "google_news",
             "query": '("高齢者" OR シニア) (健康寿命 OR フレイル OR 認知症 OR 介護予防 OR 医療 OR 介護) (対策 OR 支援 OR 調査 OR 予防)'},
            {"type": "google_news",
             "query": '("高齢者" OR 老後) (介護保険 OR 在宅介護 OR 介護費 OR 介護離職 OR 施設) (改正 OR 負担 OR 支援 OR 対策)'},
            {"type": "google_news",
             "query": '("高齢者" OR シニア) (詐欺 OR 消費者被害 OR 投資詐欺 OR 特殊詐欺 OR 定期購入) (注意 OR 対策 OR 相談 OR 国民生活センター)'},
        ],
    },
    "③和の国の羅針盤：高市政権の挑戦": {
        "webhook_env": "DISCORD_WEBHOOK_TOPIC3",
        "sources": [
            {"type": "google_news",
             "query": '(高市政権 OR 高市内閣 OR 高市早苗) (閣議決定 OR 所信表明 OR 施政方針 OR 基本方針 OR 政策)'},
            {"type": "google_news",
             "query": '(高市政権 OR 高市早苗) (経済政策 OR 物価高 OR 減税 OR 給付 OR 財政 OR 成長戦略 OR 経済安全保障)'},
            {"type": "google_news",
             "query": '(高市政権 OR 高市早苗) (外交 OR 安全保障 OR 防衛 OR 日米 OR 中国 OR 台湾 OR 韓国)'},
            {"type": "google_news",
             "query": '(高市政権 OR 高市内閣) (支持率 OR 世論調査 OR 評価 OR 課題 OR 批判 OR 成果)'},
            {"type": "direct_rss", "url": "https://www3.nhk.or.jp/rss/news/cat4.xml",
             "must_include": ["高市"], "filter": True},
        ],
    },
    "④政治関連ニュース・日本、世界で今何が": {
        "webhook_env": "DISCORD_WEBHOOK_TOPIC4",
        "sources": [
            {"type": "google_news",
             "query": '(日本 OR 政府 OR 国会 OR 与党 OR 野党) (法案 OR 予算 OR 選挙 OR 政策 OR 改正 OR 閣議決定)'},
            {"type": "google_news",
             "query": '(米国 OR アメリカ OR 中国 OR 台湾 OR 中東 OR ウクライナ OR ロシア) (外交 OR 安全保障 OR 選挙 OR 制裁 OR 紛争 OR 停戦)'},
            {"type": "google_news",
             "query": '(台湾 OR Taiwan) (中国 OR 米国 OR 日本) (安全保障 OR 軍事 OR 外交 OR 選挙)'},
            {"type": "google_news",
             "query": '(米国 OR アメリカ OR Trump OR Congress) (選挙 OR 政権 OR 外交 OR 制裁 OR 法案 OR 予算)'},
            {"type": "direct_rss", "url": "https://www3.nhk.or.jp/rss/news/cat4.xml",
             "must_include": [], "filter": False},
        ],
    },
    "⑤家族ができる高齢者の自己肯定感向上サポート": {
        "webhook_env": "DISCORD_WEBHOOK_TOPIC5",
        "sources": [
            {"type": "google_news",
             "query": '("高齢者" OR シニア) ("自己肯定感" OR 自尊感情 OR 自尊心 OR 尊厳) (家族 OR 支援 OR 関わり方 OR ケア OR 声かけ)'},
            {"type": "google_news",
             "query": '("高齢者" OR 独居 OR シニア) (孤独 OR 孤立 OR 会話 OR 傾聴 OR 見守り OR つながり) (家族 OR 支援 OR 介護)'},
            {"type": "google_news",
             "query": '("高齢者" OR 認知症 OR 介護) (尊厳 OR 自己決定 OR 意思決定支援 OR 声かけ OR 接し方) (家族 OR 介護者)'},
            {"type": "google_news",
             "query": '("高齢者" OR シニア) (生きがい OR 役割 OR 社会参加 OR ボランティア OR 趣味 OR 孫) (家族 OR 支援 OR 促す OR 事例)'},
            {"type": "google_news",
             "query": '("高齢者" OR シニア) (世代間交流 OR 孫 OR 子ども OR 地域交流) (生きがい OR 自己肯定感 OR 孤独 OR 役割)'},
        ],
    },
}

# 共通ハード除外語（毎日ニュース5分類_取得クエリ設計_厳重版.md Section 12.1）
HARD_EXCLUDE = [
    "求人", "転職", "採用", "広告", "PR", "キャンペーン",
    "芸能", "占い", "スポーツだけ", "まとめサイトだけ",
]

# トピック別 掛け算式判定（同ファイル Section 12.2〜12.6）
# 各グループ(主語/領域/行動 等)は「グループ内はOR、グループ間はAND」
# NEWS_TOPICSの各sourceで "filter": True を明示した場合のみ適用する。
# google_newsは検索クエリ自体が既にこの掛け算構造で絞り込み済みのため未適用
# (二重適用すると的確な記事まで単純文字列マッチで弾かれることが実証された、2026-07-05)。
# NHK政治カテゴリ全体RSSのような母集団が広いdirect_rssにのみ効かせる。
TOPIC_FILTERS = {
    "①後期高齢者のAI挑戦": [
        ["高齢者", "シニア", "後期高齢者", "75歳以上", "高齢世代"],
        ["AI", "生成AI", "ChatGPT", "スマホ", "パソコン", "タブレット", "LINE", "デジタル", "ICT", "マイナポータル"],
        ["活用", "使い方", "講座", "教室", "支援", "事例", "体験", "詐欺対策"],
    ],
    "②高齢者の老後不安を解消する完全ガイド": [
        ["高齢者", "シニア", "老後", "高齢世帯", "独居高齢者", "年金生活"],
        ["老後資金", "年金", "生活費", "物価高", "医療費", "介護", "健康寿命", "フレイル", "認知症", "孤独", "孤立", "住まい", "詐欺"],
        ["対策", "支援", "制度", "改正", "相談", "予防", "調査", "白書", "給付"],
    ],
    "③和の国の羅針盤：高市政権の挑戦": [
        ["高市政権", "高市内閣", "高市早苗", "高市首相", "高市総理", "Takaichi", "Sanae Takaichi"],
        ["政策", "外交", "安全保障", "経済", "国会", "閣議", "支持率", "世論", "法案", "答弁"],
    ],
    "④政治関連ニュース・日本、世界で今何が": [
        ["日本", "政府", "国会", "与党", "野党", "米国", "中国", "欧州", "ロシア", "中東", "台湾", "韓国", "国連", "NATO"],
        ["政治", "選挙", "政権", "外交", "制裁", "紛争", "法案", "予算", "支持率", "首脳会談", "安全保障"],
    ],
    "⑤家族ができる高齢者の自己肯定感向上サポート": [
        ["高齢者", "シニア", "親", "祖父母", "認知症", "介護", "家族", "介護者"],
        ["自己肯定感", "自尊感情", "自尊心", "尊厳", "自己決定", "生きがい", "役割", "孤独", "孤立"],
        ["声かけ", "傾聴", "会話", "見守り", "接し方", "関わり方", "支援", "回想法", "ライフレビュー", "世代間交流"],
    ],
}

SEEN_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ic_club_seen_urls.json")
EMBED_COLOR = 0x2ECC71
POST_INTERVAL_SEC = 1.2
MAX_ENTRIES_PER_SOURCE = 5  # 1ソースあたり直近何件まで見るか（初回大量投稿防止）

# ============================================================
# 各ソースタイプの取得関数
# ============================================================


def log(msg):
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}")


def fetch_google_news(query):
    url = "https://news.google.com/rss/search?q=" + urllib.parse.quote(query) + "&hl=ja&gl=JP&ceid=JP:ja"
    feed = feedparser.parse(url)
    return [{"title": e.get("title", ""), "link": e.get("link", ""), "summary": e.get("summary", "")}
            for e in feed.entries[:MAX_ENTRIES_PER_SOURCE]]


def fetch_direct_rss(url, must_include):
    feed = feedparser.parse(url)
    results = []
    for e in feed.entries:
        title = e.get("title", "")
        summary = e.get("summary", "") or e.get("description", "")
        text = f"{title} {summary}"
        if must_include and not any(kw in text for kw in must_include):
            continue
        results.append({"title": title, "link": e.get("link", ""), "summary": summary})
        if len(results) >= MAX_ENTRIES_PER_SOURCE:
            break
    return results


def fetch_source(source):
    stype = source["type"]
    if stype == "google_news":
        return fetch_google_news(source["query"])
    if stype == "direct_rss":
        return fetch_direct_rss(source["url"], source.get("must_include", []))
    log(f"未知のソースタイプ: {stype}")
    return []


# ============================================================
# 共通処理
# ============================================================


def load_seen():
    if os.path.exists(SEEN_FILE):
        with open(SEEN_FILE, "r", encoding="utf-8") as f:
            return set(json.load(f))
    return set()


def save_seen(seen):
    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump(sorted(seen), f, ensure_ascii=False, indent=2)


def passes_hard_exclude(title, summary):
    text = f"{title} {summary}"
    return not any(word in text for word in HARD_EXCLUDE)


def passes_topic_filter(topic_name, title, summary):
    """こころの掛け算式判定(主語 AND 領域 AND 行動 等)。
    グループ内はOR、グループ間はAND。トピック未登録なら素通し。"""
    groups = TOPIC_FILTERS.get(topic_name)
    if not groups:
        return True
    text = f"{title} {summary}"
    return all(any(kw in text for kw in group) for group in groups)


def post_to_discord(webhook_url, topic_name, item):
    title = item.get("title", "(タイトルなし)")
    link = item.get("link", "")
    summary = item.get("summary", "")
    summary = re.sub(r"<[^>]+>", "", summary).strip()
    if len(summary) > 300:
        summary = summary[:300] + "…"

    payload = {
        "embeds": [
            {
                "title": title,
                "url": link,
                "description": summary,
                "color": EMBED_COLOR,
                "footer": {"text": topic_name},
            }
        ]
    }

    resp = requests.post(webhook_url, json=payload, timeout=15)
    if resp.status_code not in (200, 204):
        log(f"投稿失敗 [{topic_name}] status={resp.status_code} body={resp.text[:200]}")
        return False
    log(f"投稿成功 [{topic_name}] {title}")
    return True


def one_pass():
    log(f"=== 毎日ニュース5分類 巡回開始（{len(NEWS_TOPICS)}トピック） ===")
    seen = load_seen()
    new_count = 0

    for topic_name, config in NEWS_TOPICS.items():
        webhook_url = os.environ.get(config["webhook_env"], "")
        if not webhook_url:
            log(f"スキップ [{topic_name}] {config['webhook_env']} が未設定")
            continue

        for source in config["sources"]:
            apply_filter = source.get("filter", False)
            try:
                items = fetch_source(source)
            except Exception as e:
                log(f"取得失敗 [{topic_name}] type={source['type']} {e}")
                continue

            for item in items:
                link = item.get("link", "")
                if not link or link in seen:
                    continue
                title, summary = item.get("title", ""), item.get("summary", "")
                if not passes_hard_exclude(title, summary):
                    continue
                if apply_filter and not passes_topic_filter(topic_name, title, summary):
                    continue
                if post_to_discord(webhook_url, topic_name, item):
                    seen.add(link)
                    new_count += 1
                    time.sleep(POST_INTERVAL_SEC)

    save_seen(seen)
    log(f"=== 巡回完了：新着 {new_count} 件投稿 ===")


def main():
    missing = [c["webhook_env"] for c in NEWS_TOPICS.values() if not os.environ.get(c["webhook_env"])]
    if len(missing) == len(NEWS_TOPICS):
        log(f"全Webhook未設定（{', '.join(missing)}）。GitHub Secretsに登録してから実行してください。")
        sys.exit(1)
    one_pass()


if __name__ == "__main__":
    main()
