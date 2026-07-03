# -*- coding: utf-8 -*-
"""
Discord RSS自動通知（GitHub Actions版・一回実行して終了）

このファイルは常駐しない。GitHub Actionsのscheduled workflow
(.github/workflows/notify.yml) が30分おきにこのスクリプトを1回だけ
呼び出す。パパのPC・N8N・自前サーバーは一切不要。GitHub側のインフラが
勝手に起動してくれる。

やること：
  1. RSS_SOURCESの全フィードを1回だけ巡回
  2. 前回まで見ていない新着記事だけを検出
  3. DiscordのWebhook URLへEmbed形式でPOST
  4. 投稿済みURLをseen_urls.jsonに記録
     （このファイルはworkflow側がgit commit&pushして永続化する。
      GitHub Actionsのrunnerは実行のたびに使い捨てになるため、
      「ファイルシステムに書くだけ」では次回実行時に消えてしまう。
      そのためリポジトリ自体に記録を書き戻す構成にしている）

必要なもの：
  - GitHubアカウント（リポジトリを1個作るだけ）
  - pip install feedparser requests（workflow内で自動インストールされる）
  - Discord側: サーバー設定 → 連携サービス → ウェブフックを作成 → URLをコピー
    → そのURLをGitHubリポジトリの Settings → Secrets and variables → Actions
      → New repository secret で「DISCORD_WEBHOOK_URL」として登録する
      （コードには一切書かない。secrets経由で環境変数として渡される）
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
from datetime import datetime

# ============================================================
# 設定
# ============================================================

# Webhook URLはコードに書かない。GitHub Secretsから環境変数として受け取る
WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "")

# 監視したいRSSフィード。増やしたい時はこの辞書に1行足してpushするだけ
RSS_SOURCES = {
    "OpenAI Blog": "https://openai.com/blog/rss.xml",
    "DeepMind": "https://deepmind.com/blog/feed/basic/",
    "Hugging Face Blog": "https://huggingface.co/blog/feed.xml",
    "Google Research": "https://feeds.feedburner.com/blogspot/gJZg",
    "PyTorch": "https://pytorch.org/blog/feed.xml",
    "Qiita LLM": "https://qiita.com/tags/llm/feed",
}

SEEN_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "seen_urls.json")
EMBED_COLOR = 0x5865F2
POST_INTERVAL_SEC = 1.2  # 連投時にDiscordのレート制限に引っかからないための間隔

# ============================================================
# 本体
# ============================================================


def log(msg):
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}")


def load_seen():
    if os.path.exists(SEEN_FILE):
        with open(SEEN_FILE, "r", encoding="utf-8") as f:
            return set(json.load(f))
    return set()


def save_seen(seen):
    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump(sorted(seen), f, ensure_ascii=False, indent=2)


def post_to_discord(source_name, entry):
    title = entry.get("title", "(タイトルなし)")
    link = entry.get("link", "")
    summary = entry.get("summary", "") or entry.get("description", "")
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
                "footer": {"text": source_name},
            }
        ]
    }

    resp = requests.post(WEBHOOK_URL, json=payload, timeout=15)
    if resp.status_code not in (200, 204):
        log(f"投稿失敗 [{source_name}] status={resp.status_code} body={resp.text[:200]}")
        return False
    log(f"投稿成功 [{source_name}] {title}")
    return True


def main():
    if not WEBHOOK_URL:
        log("DISCORD_WEBHOOK_URL が未設定。GitHub Secretsに登録してから実行してください。")
        sys.exit(1)

    log(f"=== RSS巡回開始（監視対象 {len(RSS_SOURCES)} 件） ===")
    seen = load_seen()
    new_count = 0

    for source_name, url in RSS_SOURCES.items():
        try:
            feed = feedparser.parse(url)
        except Exception as e:
            log(f"取得失敗 [{source_name}] {e}")
            continue

        if feed.bozo and not feed.entries:
            log(f"パース警告（記事0件） [{source_name}] {feed.bozo_exception}")
            continue

        entries = feed.entries[:10]
        for entry in entries:
            link = entry.get("link", "")
            if not link or link in seen:
                continue
            if post_to_discord(source_name, entry):
                seen.add(link)
                new_count += 1
                time.sleep(POST_INTERVAL_SEC)

    save_seen(seen)
    log(f"=== 巡回完了：新着 {new_count} 件投稿 ===")


if __name__ == "__main__":
    main()
