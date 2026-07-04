# -*- coding: utf-8 -*-
"""
IC倶楽部「毎日ニュース5分類」自動通知（GitHub Actions版・一回実行して終了）

森田さんの毎日ニュース5分類プロジェクト用。こころ(Codex)が設計した
毎日ニュース5分類_取得クエリ設計_厳重版.md から、各分類に「特化ソース3つ＋
Google News RSS 1つ」の計4本を選抜して実装。
notifier.py（AI技術ブログ用）とは完全に別系統・別Webhook・別チャンネル群。

4種類の入口ソースを使い分ける：
  google_news : Google News RSS検索（動的クエリ）
  direct_rss  : 個別サイトの固定RSS + トピック別キーワードフィルタ
  gdelt       : GDELT DOC API（英語圏の海外動向）
  openalex    : OpenAlex API（学術研究、JSON形式）

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
# 設定：各トピック「特化3つ＋Google News RSS 1つ」の計4ソース
# ============================================================

NEWS_TOPICS = {
    "①後期高齢者のAI挑戦": {
        "webhook_env": "DISCORD_WEBHOOK_TOPIC1",
        "sources": [
            {"type": "google_news",
             "query": '("高齢者" OR "シニア" OR "後期高齢者" OR "75歳以上") ("生成AI" OR ChatGPT OR AI) (活用 OR 講座 OR 使い方 OR 事例 OR 支援)'},
            {"type": "direct_rss", "url": "https://rss.itmedia.co.jp/rss/2.0/itmedia_all.xml",
             "must_include": ["高齢者", "シニア", "後期高齢者"]},
            {"type": "gdelt", "query": "elderly digital literacy AI training"},
            {"type": "google_news",
             "query": '("高齢者" OR シニア) ("デジタルデバイド" OR "デジタル格差" OR "デジタル活用支援") (総務省 OR 自治体 OR 講習会 OR 支援員)'},
        ],
    },
    "②高齢者の老後不安を解消する完全ガイド": {
        "webhook_env": "DISCORD_WEBHOOK_TOPIC2",
        "sources": [
            {"type": "google_news",
             "query": '(高齢者 OR シニア OR 老後) ("老後資金" OR 年金 OR 生活費 OR 物価高 OR 医療費 OR 介護費) (不安 OR 対策 OR 支援 OR 制度 OR 改正 OR 調査)'},
            {"type": "direct_rss", "url": "https://toyokeizai.net/list/feed/rss",
             "must_include": ["老後", "年金", "高齢者", "介護"]},
            {"type": "direct_rss", "url": "https://president.jp/list/rss",
             "must_include": ["老後", "年金", "高齢者", "介護", "孤独"]},
            {"type": "google_news",
             "query": '("高齢者" OR シニア OR 高齢世帯 OR 独居) (孤独 OR 孤立 OR 見守り OR 居場所 OR つながり) (対策 OR 自治体 OR 支援 OR 調査)'},
        ],
    },
    "③和の国の羅針盤：高市政権の挑戦": {
        "webhook_env": "DISCORD_WEBHOOK_TOPIC3",
        "sources": [
            {"type": "google_news",
             "query": '(高市政権 OR 高市内閣 OR 高市早苗) (閣議決定 OR 所信表明 OR 施政方針 OR 基本方針 OR 政策)'},
            {"type": "direct_rss", "url": "https://www3.nhk.or.jp/rss/news/cat0.xml",
             "must_include": ["高市"]},
            {"type": "gdelt", "query": "Takaichi Japan government policy"},
            {"type": "google_news",
             "query": '(高市政権 OR 高市早苗) (経済政策 OR 物価高 OR 減税 OR 給付 OR 財政 OR 成長戦略 OR 経済安全保障)'},
        ],
    },
    "④政治関連ニュース・日本、世界で今何が": {
        "webhook_env": "DISCORD_WEBHOOK_TOPIC4",
        "sources": [
            {"type": "google_news",
             "query": '(日本 OR 政府 OR 国会 OR 与党 OR 野党) (法案 OR 予算 OR 選挙 OR 政策 OR 改正 OR 閣議決定)'},
            {"type": "direct_rss", "url": "https://www3.nhk.or.jp/rss/news/cat6.xml",
             "must_include": []},
            {"type": "gdelt", "query": "world politics election government diplomacy"},
            {"type": "google_news",
             "query": '(米国 OR アメリカ OR 中国 OR 台湾 OR 中東 OR ウクライナ OR ロシア) (外交 OR 安全保障 OR 選挙 OR 制裁 OR 紛争 OR 停戦)'},
        ],
    },
    "⑤家族ができる高齢者の自己肯定感向上サポート": {
        "webhook_env": "DISCORD_WEBHOOK_TOPIC5",
        "sources": [
            {"type": "google_news",
             "query": '("高齢者" OR シニア) ("自己肯定感" OR 自尊感情 OR 自尊心 OR 尊厳) (家族 OR 支援 OR 関わり方 OR ケア OR 声かけ)'},
            {"type": "direct_rss", "url": "https://president.jp/list/rss",
             "must_include": ["自己肯定感", "尊厳", "生きがい", "家族の声かけ", "高齢者"]},
            {"type": "openalex", "query": "older adults self-esteem dignity family caregiver"},
            {"type": "google_news",
             "query": '("高齢者" OR 独居 OR シニア) (孤独 OR 孤立 OR 会話 OR 傾聴 OR 見守り OR つながり) (家族 OR 支援 OR 介護)'},
        ],
    },
}

# 共通ハード除外語（毎日ニュース5分類_取得クエリ設計_厳重版.md Section 12.1）
HARD_EXCLUDE = [
    "求人", "転職", "採用", "広告", "PR", "キャンペーン",
    "芸能", "占い", "スポーツだけ", "まとめサイトだけ",
]

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


def fetch_gdelt(query):
    url = ("https://api.gdeltproject.org/api/v2/doc/doc?query=" + urllib.parse.quote(query)
           + "&mode=ArtList&maxrecords=" + str(MAX_ENTRIES_PER_SOURCE) + "&format=rss&timespan=7d")
    try:
        feed = feedparser.parse(url)
        return [{"title": e.get("title", ""), "link": e.get("link", ""), "summary": e.get("summary", "")}
                for e in feed.entries[:MAX_ENTRIES_PER_SOURCE]]
    except Exception as e:
        log(f"GDELT取得失敗: {e}")
        return []


def fetch_openalex(query):
    url = "https://api.openalex.org/works?search=" + urllib.parse.quote(query) + f"&per-page={MAX_ENTRIES_PER_SOURCE}&sort=publication_date:desc"
    try:
        resp = requests.get(url, timeout=15, headers={"User-Agent": "ic-club-notifier (mailto:izumoitachi@gmail.com)"})
        if resp.status_code != 200:
            log(f"OpenAlex取得失敗: status={resp.status_code}")
            return []
        data = resp.json()
        results = []
        for w in data.get("results", []):
            title = w.get("title", "") or "(タイトルなし)"
            link = w.get("id", "") or w.get("doi", "")
            abstract_idx = w.get("abstract_inverted_index")
            summary = ""
            if abstract_idx:
                words = sorted(abstract_idx.items(), key=lambda kv: kv[1][0])
                summary = " ".join(w for w, _ in words)[:400]
            results.append({"title": title, "link": link, "summary": summary})
        return results
    except Exception as e:
        log(f"OpenAlex取得失敗: {e}")
        return []


def fetch_source(source):
    stype = source["type"]
    if stype == "google_news":
        return fetch_google_news(source["query"])
    if stype == "direct_rss":
        return fetch_direct_rss(source["url"], source.get("must_include", []))
    if stype == "gdelt":
        return fetch_gdelt(source["query"])
    if stype == "openalex":
        return fetch_openalex(source["query"])
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
            try:
                items = fetch_source(source)
            except Exception as e:
                log(f"取得失敗 [{topic_name}] type={source['type']} {e}")
                continue

            for item in items:
                link = item.get("link", "")
                if not link or link in seen:
                    continue
                if not passes_hard_exclude(item.get("title", ""), item.get("summary", "")):
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
