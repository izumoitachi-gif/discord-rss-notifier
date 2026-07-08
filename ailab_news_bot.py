#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ================================================================
# ailab_news_bot.py — AIラボ鯖 15分類AIニュース自動配信（GitHub配布用・独立スクリプト）
# ----------------------------------------------------------------
# 方式: 朝活/激裏型。Google News検索RSS＋実証済みニュース母体を分類別クエリで切って投稿。
#       AI分類は使わず route_id / source で分類を固定（IC倶楽部方式）。
# 設計の正本: 自分/AIニュース15分類_取得クエリ設計_完全再設計版 1.md（2026-07-08）
# 2026-07-09: 初期実運用で「量は出るが質が荒い」ことを確認。
#   方針変更: 公式RSS/GitHub release/APIを主水路にしない。
#   トレンド朝活で効いているニュース母体を15分類のクエリ違いで使い回す。
#
# 使い方:
#   1) pip install feedparser
#   2) Webhookを環境変数で（GitHub Actionsは Secrets 推奨）:
#        DISCORD_AILAB_WEBHOOK_{OPENAI,CLAUDE,GEMINI,XAI,COPILOT,META,CHINA,
#                              LOCAL,IMGVID,AUDIO,TOOLS,PAPERS,GENERAL,RELEASE,WORLD}
#   3) ローカルテストは環境変数が無ければ同ディレクトリ ailab_webhooks.json を fallback（配布時 .gitignore）
#   4) python ailab_news_bot.py
#   5) 自動化: .github/workflows で cron '15,45 * * * *'
# 重複排除: 同ディレクトリ ailab_seen_urls.json / 投稿POSTには User-Agent 必須(無いとCloudflare403)
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
SEEN_FILE = os.path.join(HERE, "ailab_seen_urls.json")
WEBHOOKS_JSON = os.path.join(HERE, "ailab_webhooks.json")
PER_SOURCE = 2
PER_CHANNEL = 2
UA = "AILabNewsBot/1.0 (+https://discord.com)"
COL_BIZ, COL_FIELD, COL_SUM = 0x00E5FF, 0x00FF9C, 0xFF7A1A

GLOBAL_EXCLUDE = [
    "PR TIMES", "プレスリリース", "アットプレス", "valuepress",
    "株価", "決算", "ホールド評価", "求人", "採用", "セミナー", "イベント開催",
    "ライブ配信", "ウェビナー", "講座", "Investing.com", "ファイナンス", "金融ニュース",
    "使ってみた", "とは？", "とは何か", "徹底解説", "始め方", "初心者", "AIsmiley", "ai-market.jp",
    "キャンペーン", "無料公開", "広告", "Sponsored",
    "料金はいくら", "全プラン比較", "最適な選び方", "おすすめランキング", "資料請求",
    "日記", "使い倒し", "仕事術", "入門",
]

def rss(url, include=None, exclude=None, label=None, title_include=None, title_exclude=None):
    return {"type": "rss", "url": url, "include": include or [], "exclude": exclude or [],
            "label": label, "title_include": title_include or [], "title_exclude": title_exclude or []}

def gn(q, include=None, exclude=None, label=None, title_include=None, title_exclude=None):
    return {"type": "gn", "q": q, "include": include or [], "exclude": exclude or [],
            "label": label, "title_include": title_include or [], "title_exclude": title_exclude or []}

def sitemap(url, include=None, exclude=None, label=None, path_prefix=None, title_include=None, title_exclude=None):
    return {"type": "sitemap", "url": url, "include": include or [], "exclude": exclude or [],
            "label": label, "path_prefix": path_prefix or [], "title_include": title_include or [],
            "title_exclude": title_exclude or []}

# ---- 15分類（2026-07-09 ノイズ削減版）----
OPENAI_TERMS = ["OpenAI", "ChatGPT", "GPT", "Sora", "Codex", "Responses API", "API"]
CLAUDE_TERMS = ["Anthropic", "Claude", "Claude Code", "Claude API", "Claude Sonnet", "Claude Opus", "Claude Haiku"]
GEMINI_TERMS = ["Gemini", "DeepMind", "NotebookLM", "Veo", "Imagen", "Gemma", "Gemini API"]
XAI_TERMS = ["xAI", "Grok"]
COPILOT_TERMS = ["Copilot", "GitHub Copilot", "Microsoft 365 Copilot", "Copilot Studio", "Azure AI"]
META_TERMS = ["Meta AI", "Meta Llama", "Llama", "Llama 4"]
CHINA_TERMS = ["DeepSeek", "Qwen", "Kimi", "Zhipu", "GLM", "MiniMax", "Moonshot"]
LOCAL_TERMS = ["Hugging Face", "Ollama", "llama.cpp", "GGUF", "LM Studio", "vLLM", "ローカルLLM"]
IMGVID_TERMS = ["Midjourney", "Sora", "Runway", "Kling", "Veo", "Imagen", "Stable Diffusion", "Flux", "Luma AI", "Pika", "画像生成", "動画生成"]
AUDIO_TERMS = ["ElevenLabs", "Suno", "Udio", "Whisper", "VOICEVOX", "音声生成", "音楽生成", "音声合成", "TTS", "voice", "speech"]
TOOL_TERMS = ["Claude Code", "Codex", "Cursor", "GitHub Copilot", "n8n", "MCP", "LangChain", "Devin", "AIエージェント"]
PAPER_TERMS = ["LLM", "language model", "agent", "reasoning", "RAG", "GPT", "diffusion", "multimodal", "transformer", "benchmark"]
MODEL_RELEASE_TERMS = ["新モデル", "モデル公開", "オープンウェイト", "提供開始", "generally available", "open weights", "GPT", "Claude", "Gemini", "Grok", "Llama", "Qwen", "DeepSeek", "Mistral"]
POLICY_TERMS = ["AI規制", "AI政策", "AI法", "AI著作権", "AI safety", "AI regulation", "AI Act", "export controls", "semiconductor", "半導体"]

TOPICS = [
 # 🏢 企業別 ①〜⑦：主水路はGoogle News検索RSS。公式RSS/changelogは後順位の補助。
 {"num":"①","name":"チャッピー速報","env":"OPENAI","color":COL_BIZ,"sources":[
     gn('(OpenAI OR ChatGPT OR Codex OR Sora OR GPT) (発表 OR 公開 OR 提供開始 OR 新機能 OR 新モデル OR 料金 OR API) -求人 -株価 -広告',
        include=OPENAI_TERMS, exclude=["広告運用", "導入事例", "customer", "case study"]),
     rss("https://rss.itmedia.co.jp/rss/2.0/aiplus.xml", include=OPENAI_TERMS),
     rss("https://gigazine.net/news/rss_2.0/", include=OPENAI_TERMS),
     rss("https://zenn.dev/topics/ai/feed", include=OPENAI_TERMS)]},
 {"num":"②","name":"クロード速報","env":"CLAUDE","color":COL_BIZ,"sources":[
     gn('(Anthropic OR Claude OR "Claude Code" OR "Claude API") (発表 OR 公開 OR 提供開始 OR 新機能 OR 新モデル OR 料金 OR API) -MCPサーバ -脆弱性',
        include=CLAUDE_TERMS, exclude=["中转站", "プロキシ", "非公式", "ProductZine", "MVP", "PMF", "コミュニティアンバサダー"]),
     rss("https://rss.itmedia.co.jp/rss/2.0/aiplus.xml", include=CLAUDE_TERMS),
     rss("https://gigazine.net/news/rss_2.0/", include=CLAUDE_TERMS),
     rss("https://zenn.dev/topics/claude/feed", include=CLAUDE_TERMS),
     sitemap("https://www.anthropic.com/sitemap.xml",
        include=["claude", "sonnet", "opus", "haiku", "model", "api", "code", "safety"],
        exclude=["events/", "careers/", "legal/", "learn/", "pricing", "jobs", "interviewer",
                 "golden-gate", "persona-selection", "economic-index"],
        path_prefix=["/news/", "/engineering/"], label="Anthropic official sitemap")]},
 {"num":"③","name":"ジェミニ速報","env":"GEMINI","color":COL_BIZ,"sources":[
     gn('(Gemini OR "Gemini API" OR DeepMind OR NotebookLM OR Veo OR Imagen OR Gemma) (発表 OR 公開 OR 提供開始 OR 新機能 OR 新モデル OR API OR アップデート) -Pixel -写真',
        include=GEMINI_TERMS),
     rss("https://rss.itmedia.co.jp/rss/2.0/aiplus.xml", include=GEMINI_TERMS),
     rss("https://gigazine.net/news/rss_2.0/", include=GEMINI_TERMS),
     rss("https://blog.google/technology/ai/rss/", include=GEMINI_TERMS, exclude=["Pixel", "Google Photos", "写真"]),
     rss("https://deepmind.google/blog/rss.xml", include=GEMINI_TERMS),
     rss("https://zenn.dev/topics/gemini/feed", include=GEMINI_TERMS, title_include=["Gemini"])]},
 {"num":"④","name":"グロック速報","env":"XAI","color":COL_BIZ,"sources":[
     gn('(xAI OR Grok) (発表 OR 公開 OR 提供開始 OR 新機能 OR 新モデル OR API OR アップデート OR benchmark) -SpaceXAI -スペースXAI',
        include=XAI_TERMS, exclude=["スペースXAI", "カーサー共同", "買収直後のCursor", "note", "AIイラストクリエイター"]),
     rss("https://rss.itmedia.co.jp/rss/2.0/aiplus.xml", include=XAI_TERMS),
     rss("https://gigazine.net/news/rss_2.0/", include=XAI_TERMS),
     rss("https://hnrss.org/frontpage", include=XAI_TERMS)]},
 {"num":"⑤","name":"コパイロット速報","env":"COPILOT","color":COL_BIZ,"sources":[
     gn('("Microsoft Copilot" OR "GitHub Copilot" OR "Copilot Studio" OR "Azure AI" OR "M365 Copilot") (発表 OR 公開 OR 提供開始 OR 新機能 OR 料金 OR アップデート)',
        include=COPILOT_TERMS, exclude=["保険", "library", "farming", "school"]),
     rss("https://www.publickey1.jp/atom.xml", include=COPILOT_TERMS),
     rss("https://zenn.dev/topics/ai/feed", include=COPILOT_TERMS, title_include=["Copilot"]),
     rss("https://github.blog/changelog/label/copilot/feed/", include=COPILOT_TERMS, title_include=["Copilot"])]},
 {"num":"⑥","name":"メタAI速報","env":"META","color":COL_BIZ,"sources":[
     gn('("Meta AI" OR "Meta Llama" OR Llama) (発表 OR 公開 OR 提供開始 OR 新機能 OR 新モデル OR オープンソース OR benchmark) -"MUSIC FAIR" -音楽',
        include=META_TERMS, exclude=["MUSIC FAIR", "Garmin", "AIグラス"]),
     rss("https://rss.itmedia.co.jp/rss/2.0/aiplus.xml", include=META_TERMS),
     rss("https://gigazine.net/news/rss_2.0/", include=META_TERMS),
     rss("https://hnrss.org/frontpage", include=META_TERMS)]},
 {"num":"⑦","name":"中国AI速報","env":"CHINA","color":COL_BIZ,"sources":[
     gn('(DeepSeek OR Qwen OR Kimi OR Zhipu OR GLM OR MiniMax OR Moonshot) (発表 OR 公開 OR 提供開始 OR 新機能 OR 新モデル OR API OR オープンソース OR benchmark)',
        include=CHINA_TERMS, exclude=["BigGo ファイナンス"]),
     rss("https://rss.itmedia.co.jp/rss/2.0/aiplus.xml", include=CHINA_TERMS),
     rss("https://gigazine.net/news/rss_2.0/", include=CHINA_TERMS),
     rss("https://hnrss.org/frontpage", include=CHINA_TERMS)]},
 # 🔬 分野別 ⑧〜⑫
 {"num":"⑧","name":"ローカルLLM速報","env":"LOCAL","color":COL_FIELD,"sources":[
     gn('(ローカルLLM OR Ollama OR "Hugging Face" OR llama.cpp OR GGUF OR "LM Studio" OR vLLM) (発表 OR 公開 OR 提供開始 OR 新モデル OR GPU OR 推論 OR 高速化)',
        include=LOCAL_TERMS),
     rss("https://huggingface.co/blog/feed.xml", include=LOCAL_TERMS + ["model", "agents", "inference"]),
     rss("https://hnrss.org/frontpage", include=LOCAL_TERMS),
     rss("https://www.reddit.com/r/LocalLLaMA/top/.rss?t=day&limit=10", include=LOCAL_TERMS + ["LLM", "model"]),
     rss("https://ollama.com/blog/rss.xml", include=LOCAL_TERMS + ["Gemma", "model", "MLX"])]},
 {"num":"⑨","name":"画像・動画AI速報","env":"IMGVID","color":COL_FIELD,"sources":[
     gn('(Midjourney OR Sora OR Runway OR Kling OR Veo OR Imagen OR "Stable Diffusion" OR Flux OR "Luma AI" OR Pika) (発表 OR 公開 OR 提供開始 OR 新機能 OR 新モデル OR 動画生成 OR 画像生成 OR アップデート)',
        include=IMGVID_TERMS, exclude=["訴訟", "裁判", "著作権訴訟", "提訴", "係争", "映画スタジオ", "UNIVERSAL MUSIC", "VITURE", "スマートグラス", "医療", "サウナ", "超音波", "Spa"]),
     rss("https://gigazine.net/news/rss_2.0/", include=IMGVID_TERMS),
     rss("https://rss.itmedia.co.jp/rss/2.0/aiplus.xml", include=IMGVID_TERMS),
     rss("https://hnrss.org/frontpage", include=IMGVID_TERMS)]},
 {"num":"⑩","name":"音声・音楽AI速報","env":"AUDIO","color":COL_FIELD,"sources":[
     gn('(ElevenLabs OR Suno OR Udio OR Whisper OR VOICEVOX OR 音声生成AI OR 音楽生成AI OR 音声合成AI OR TTS) (発表 OR 公開 OR 提供開始 OR 新機能 OR 新モデル OR API OR アップデート) -薬歴 -薬局 -医療 -銀行',
        include=AUDIO_TERMS, exclude=["Moomoo", "Marble", "Sakana", "薬歴", "薬局"]),
     rss("https://gigazine.net/news/rss_2.0/", include=AUDIO_TERMS),
     rss("https://rss.itmedia.co.jp/rss/2.0/aiplus.xml", include=AUDIO_TERMS),
     rss("https://hnrss.org/frontpage", include=AUDIO_TERMS)]},
 {"num":"⑪","name":"AIツール・エージェント速報","env":"TOOLS","color":COL_FIELD,"sources":[
     gn('("Claude Code" OR Codex OR Cursor OR n8n OR MCP OR LangChain OR Devin OR AIエージェント) (発表 OR 公開 OR 提供開始 OR 新機能 OR リリース OR 事例) -PR',
        include=TOOL_TERMS, exclude=["認定試験", "講座"]),
     rss("https://zenn.dev/topics/ai/feed", include=TOOL_TERMS),
     rss("https://hnrss.org/frontpage", include=TOOL_TERMS),
     rss("https://www.publickey1.jp/atom.xml", include=TOOL_TERMS),
     rss("https://cursor.com/changelog/rss.xml", include=["Cursor", "agent", "MCP"]),
     rss("https://blog.n8n.io/rss", include=["n8n", "AI", "agent", "workflow", "MCP"])]},
 {"num":"⑫","name":"論文速報","env":"PAPERS","color":COL_FIELD,"sources":[
     rss("http://export.arxiv.org/rss/cs.AI", include=PAPER_TERMS),
     rss("http://export.arxiv.org/rss/cs.CL", include=PAPER_TERMS),
     rss("https://hnrss.org/frontpage", include=PAPER_TERMS)]},
 # 🌐 総合 ⑬〜⑮
 {"num":"⑬","name":"AI最新速報（日本語）","env":"GENERAL","color":COL_SUM,"sources":[
     gn('(生成AI OR AIエージェント OR ChatGPT OR Claude OR Gemini OR ローカルLLM) (発表 OR 公開 OR 提供開始 OR 導入 OR 活用 OR 事例) -求人 -株価',
        include=["AI", "生成AI", "ChatGPT", "Claude", "Gemini", "LLM", "エージェント"]),
     rss("https://rss.itmedia.co.jp/rss/2.0/aiplus.xml"),
     rss("https://gigazine.net/news/rss_2.0/", include=["AI","LLM","ChatGPT","Claude","Gemini","生成","OpenAI","Grok"]),
     rss("https://www.publickey1.jp/atom.xml", include=["AI","LLM","Claude","GPT","Copilot","生成","Gemini","エージェント"]),
     rss("https://zenn.dev/topics/ai/feed", include=["AI", "LLM", "ChatGPT", "Claude", "Gemini", "エージェント"])]},
 {"num":"⑭","name":"新モデルリリース速報","env":"RELEASE","color":COL_SUM,"sources":[
     gn('("新モデル" OR "モデル公開" OR "オープンウェイト" OR "提供開始" OR "generally available" OR "open weights") (OpenAI OR Anthropic OR Google OR Gemini OR Meta OR DeepSeek OR Qwen OR xAI OR Grok OR Mistral) -株 -決算 -Benchmark',
        include=MODEL_RELEASE_TERMS, exclude=["決算", "株", "ホールド評価", "限定公開", "グロービス", "学び放題", "政府が「待った」", "Межа", "Новини України"]),
     rss("https://rss.itmedia.co.jp/rss/2.0/aiplus.xml", include=MODEL_RELEASE_TERMS),
     rss("https://gigazine.net/news/rss_2.0/", include=MODEL_RELEASE_TERMS),
     rss("https://hnrss.org/frontpage", include=MODEL_RELEASE_TERMS)]},
 {"num":"⑮","name":"世界のAI動向・規制","env":"WORLD","color":COL_SUM,"sources":[
     gn('(AI規制 OR AI政策 OR AI法 OR AI著作権 OR "EU AI Act" OR "AI safety" OR "AI regulation" OR "AI export controls") (政府 OR EU OR 米国 OR 中国 OR 日本 OR 法案 OR 規制 OR policy OR regulation) -データセンター -イベント',
        include=POLICY_TERMS, exclude=["Data Center Japan", "出展", "展示会", "ライブ配信", "無料公開"]),
     rss("https://www3.nhk.or.jp/rss/news/cat6.xml", include=POLICY_TERMS + ["AI", "半導体", "中国", "米国"]),
     rss("https://www3.nhk.or.jp/rss/news/cat5.xml", include=POLICY_TERMS + ["AI", "半導体", "経済安全保障"]),
     rss("https://feeds.bbci.co.uk/news/world/rss.xml", include=POLICY_TERMS + ["AI", "semiconductor"]),
     rss("https://rss.itmedia.co.jp/rss/2.0/aiplus.xml", include=POLICY_TERMS)]},
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

def is_release_version_noise(title):
    t = title.strip()
    low = t.lower()
    if any(x.lower() in low for x in MODEL_RELEASE_TERMS + ["codex", "claude", "gemini", "grok", "llama", "qwen", "deepseek"]):
        return False
    return bool(re.fullmatch(r"v?\d+(\.\d+){1,4}([._-]?(alpha|beta|rc)\.?\d*)?", low) or low in {"stable", "nightly"})

def canonical_title(title):
    t = re.sub(r"\s+-\s+[^|]+(?:\s+\|.*)?$", "", title or "")
    t = re.sub(r"\s*\([^)]{2,50}\)\s*$", "", t)
    t = re.sub(r"\s*（[^）]{2,50}）\s*$", "", t)
    return re.sub(r"\s+", " ", t).strip().lower()

def title_from_url(url):
    path = urllib.parse.urlparse(url).path.strip("/")
    slug = path.split("/")[-1] if path else urllib.parse.urlparse(url).netloc
    slug = urllib.parse.unquote(slug)
    return re.sub(r"[-_]+", " ", slug).strip().title()

def fetch_sitemap(src):
    try:
        req = urllib.request.Request(src["url"], headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=15) as resp:
            xml = resp.read(500000).decode("utf-8", "replace")
    except Exception as e:
        print("    sitemap失敗:", src["url"][:50], e); return []
    include = src.get("include") or []
    exclude = GLOBAL_EXCLUDE + (src.get("exclude") or [])
    title_include = src.get("title_include") or []
    title_exclude = src.get("title_exclude") or []
    seen_titles = set()
    prefixes = src.get("path_prefix") or []
    entries = []
    for block in re.findall(r"<url>(.*?)</url>", xml, re.S):
        loc_m = re.search(r"<loc>(.*?)</loc>", block, re.S)
        if not loc_m: continue
        loc = clean(loc_m.group(1))
        parsed = urllib.parse.urlparse(loc)
        path = parsed.path or "/"
        if prefixes and not any(path.startswith(prefix) for prefix in prefixes): continue
        title = title_from_url(loc)
        lastmod_m = re.search(r"<lastmod>(.*?)</lastmod>", block, re.S)
        lastmod = clean(lastmod_m.group(1)) if lastmod_m else ""
        filter_text = " ".join([title, path])
        if title_include and not contains_any(title, title_include): continue
        if title_exclude and contains_any(title, title_exclude): continue
        if include and not contains_any(filter_text, include): continue
        if exclude and contains_any(filter_text, exclude): continue
        summ = f"official page updated: {lastmod[:10]}" if lastmod else ""
        entries.append((lastmod, title, loc, summ, src.get("label") or parsed.netloc))
    entries.sort(key=lambda x: x[0], reverse=True)
    return [(title, loc, summ, label) for lastmod, title, loc, summ, label in entries[:PER_SOURCE]]

# ---- 日本語化（英語タイトル/要約をGoogle翻訳で和訳・失敗時は原文＝重要ニュースは英語でも可）----
# ★deep_translator(内部でrequests使用)はライブラリ側にtimeoutを渡す口が無く、
#   GitHub Actionsのクラウド側IPからだと接続がハングして戻ってこないことがある
#   （ローカルでは問題なくAction上でだけ無限待ちした実例2026-07-08）。
#   スレッド+timeoutで包んでも、ワーカースレッド自体が本当にハングした場合は
#   Pythonプロセス終了時のスレッドjoin待ちで結局終わらない恐れがある。
#   なので外部ライブラリを経由せず urllib.request.urlopen(timeout=...) で
#   Google翻訳の非公式エンドポイントを直接叩く＝ソケットレベルの本物のタイムアウトにする。
_JP_RE = re.compile(r"[ぁ-んァ-ヶ一-龠]")
_TR_FAIL_STREAK = 0
_TR_TIMEOUT_SEC = 6
_TR_MAX_FAIL_STREAK = 3   # これだけ連続で失敗/タイムアウトしたら以後は翻訳を諦める(原文のまま)

def is_ja(t):
    if not t: return True
    return len(_JP_RE.findall(t)) >= max(3, int(len(t) * 0.12))   # 既に日本語なら翻訳しない

def _gtranslate(text, timeout):
    url = "https://translate.googleapis.com/translate_a/single?" + urllib.parse.urlencode({
        "client": "gtx", "sl": "auto", "tl": "ja", "dt": "t", "q": text[:4800]
    })
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as resp:   # timeout=ソケットレベルで確実に打ち切る
        data = json.loads(resp.read().decode("utf-8"))
    return "".join(seg[0] for seg in data[0] if seg and seg[0])

def to_ja(t):
    global _TR_FAIL_STREAK
    if not t or is_ja(t): return t
    if _TR_FAIL_STREAK >= _TR_MAX_FAIL_STREAK:
        return t   # 回路遮断中：翻訳サーバーに繋がらない状況と判断し、以後は叩かず原文のまま
    try:
        result = _gtranslate(t, _TR_TIMEOUT_SEC)
        _TR_FAIL_STREAK = 0
        return result or t
    except Exception:
        _TR_FAIL_STREAK += 1
        if _TR_FAIL_STREAK >= _TR_MAX_FAIL_STREAK:
            print(f"    翻訳サーバー応答なし({_TR_TIMEOUT_SEC}s×{_TR_MAX_FAIL_STREAK}回連続) → 以後は原文のまま投稿")
        return t   # タイムアウト/失敗時は原文のまま（重要ニュースは英語でも可）

def load_webhooks():
    m = {}
    for k, v in os.environ.items():
        if k.startswith("DISCORD_AILAB_WEBHOOK_"):
            m[k.replace("DISCORD_AILAB_WEBHOOK_", "")] = v
    if not m and os.path.exists(WEBHOOKS_JSON):
        for r in json.load(io.open(WEBHOOKS_JSON, encoding="utf-8")):
            if r.get("webhook_url"):
                m[r["env"].replace("DISCORD_AILAB_WEBHOOK_", "")] = r["webhook_url"]
    return m

def load_seen():
    try: return set(json.load(io.open(SEEN_FILE, encoding="utf-8")))
    except Exception: return set()

def save_seen(s):
    io.open(SEEN_FILE, "w", encoding="utf-8").write(json.dumps(sorted(s), ensure_ascii=False))

def fetch(src):
    if src["type"] == "sitemap":
        return fetch_sitemap(src)
    url = gn_url(src["q"]) if src["type"] == "gn" else src["url"]
    try:
        f = feedparser.parse(url)
    except Exception as e:
        print("    fetch失敗:", url[:50], e); return []
    feed_label = clean((f.feed.get("title", "") if getattr(f, "feed", None) else "")) or urllib.parse.urlparse(url).netloc
    include = src.get("include") or src.get("must") or []
    exclude = GLOBAL_EXCLUDE + (src.get("exclude") or [])
    title_include = src.get("title_include") or []
    title_exclude = src.get("title_exclude") or []
    out = []
    seen_titles = set()
    for e in f.entries[: PER_SOURCE * 5]:
        title = clean(e.get("title", ""))
        if not title: continue
        summ = clean(e.get("summary", "") or e.get("description", ""))
        entry_source = e.get("source", {})
        if isinstance(entry_source, dict):
            entry_source = clean(entry_source.get("title", ""))
        else:
            entry_source = ""
        filter_text = " ".join([title, summ, entry_source])
        if is_release_version_noise(title): continue
        title_key = canonical_title(title)
        if title_key in seen_titles: continue
        if title_include and not contains_any(title, title_include): continue
        if title_exclude and contains_any(title, title_exclude): continue
        if include and not contains_any(filter_text, include): continue
        if exclude and contains_any(filter_text, exclude): continue
        seen_titles.add(title_key)
        if len(summ) < 25 or summ[:18] == title[:18]: summ = ""
        summ = summ[:160] + ("…" if len(summ) > 160 else "")
        out.append((title, e.get("link", ""), summ, src.get("label") or entry_source or feed_label))
        if len(out) >= PER_SOURCE: break
    return out

def collect_topic_items(t, seen, sleep_sec=0.4):
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

    # Google Newsだけで枠を埋めない。朝活方式として、実証済み母体を分類別に混ぜる。
    for rows in source_hits:
        if len(picked) >= PER_CHANNEL:
            break
        add_item(rows[0])

    if len(picked) < PER_CHANNEL:
        for rows in source_hits:
            for item in rows[1:]:
                if len(picked) >= PER_CHANNEL:
                    break
                add_item(item)
            if len(picked) >= PER_CHANNEL:
                break
    return picked[:PER_CHANNEL]

def post(url, header, items, color):
    embeds = []
    for title, link, summ, src in items:
        emb = {"title": title[:250], "url": link, "color": color, "footer": {"text": src[:100]}}
        if summ: emb["description"] = summ
        embeds.append(emb)
    body = {"content": header, "embeds": embeds[:10]}
    r = urllib.request.Request(url, data=json.dumps(body).encode(),
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
