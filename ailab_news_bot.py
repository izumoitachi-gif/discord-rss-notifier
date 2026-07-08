#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ================================================================
# ailab_news_bot.py — AIラボ鯖 15分類AIニュース自動配信（GitHub配布用・独立スクリプト）
# ----------------------------------------------------------------
# 方式: 公式RSS＋GitHubリリースAtom＋Google News RSS を feedparser で取得し Discord Webhook へ Embed 投稿。
#       AI分類は使わず route_id / source で分類を固定（IC倶楽部方式）。
# 設計の正本: 自分/AIニュース15分類_取得クエリ設計_完全再設計版 1.md（2026-07-08）
# ★全ソースは 2026-07-08 に scratchpad/verify_sources.py で HTTP200＋entries>0 を実測確認済み。
#   （設計の誤字は修正済み: ggorg→ggerganov[空→不採用], getcursor/cursor[404]→cursor.com/changelog,
#    anthropic-cookbook commits[404]→不採用, Qwen→Qwen3）
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
PER_SOURCE = 3
PER_CHANNEL = 4
UA = "AILabNewsBot/1.0 (+https://discord.com)"
COL_BIZ, COL_FIELD, COL_SUM = 0x00E5FF, 0x00FF9C, 0xFF7A1A

def rss(url, must=None): return {"type": "rss", "url": url, "must": must}
def gn(q):               return {"type": "gn",  "q": q}

# ---- 15分類（全ソース 2026-07-08 実測検証済み）----
TOPICS = [
 # 🏢 企業別 ①〜⑦
 {"num":"①","name":"チャッピー速報","env":"OPENAI","color":COL_BIZ,"sources":[
     rss("https://openai.com/news/rss.xml"),
     rss("https://github.com/openai/codex/releases.atom"),
     gn('(OpenAI OR ChatGPT OR Codex OR Sora OR GPT) (発表 OR 更新 OR 新モデル OR API OR 料金)')]},
 {"num":"②","name":"クロード速報","env":"CLAUDE","color":COL_BIZ,"sources":[
     rss("https://tim-hilde.github.io/anthropic-rss/rss.xml"),                         # Claude本体(日本語)
     rss("https://raw.githubusercontent.com/taobojlen/anthropic-rss-feed/main/anthropic_news_rss.xml"),  # Claude本体
     rss("https://github.com/anthropics/claude-code/releases.atom"),                   # Claude Code更新
     rss("https://github.com/anthropics/anthropic-sdk-python/releases.atom"),          # API/SDK更新
     rss("https://github.com/modelcontextprotocol/specification/releases.atom"),       # MCP仕様
     gn('(Anthropic OR Claude OR "Claude Code" OR MCP) (発表 OR 更新 OR 新モデル OR API OR セキュリティ)')]},
 {"num":"③","name":"ジェミニ速報","env":"GEMINI","color":COL_BIZ,"sources":[
     rss("https://blog.google/technology/ai/rss/"),
     rss("https://deepmind.google/blog/rss.xml"),
     gn('(Google OR Gemini OR DeepMind OR NotebookLM OR Veo OR Imagen OR Gemma) (発表 OR 更新 OR 新モデル OR API)')]},
 {"num":"④","name":"グロック速報","env":"XAI","color":COL_BIZ,"sources":[
     gn('(xAI OR Grok OR "Grok 4" OR "Grok 3" OR "イーロン マスク AI") (発表 OR 更新 OR 新モデル OR API OR X)')]},
 {"num":"⑤","name":"コパイロット速報","env":"COPILOT","color":COL_BIZ,"sources":[
     rss("https://news.microsoft.com/source/topics/ai/feed/"),                         # MS公式AI
     rss("https://github.com/microsoft/vscode-copilot-release/releases.atom"),
     gn('("Microsoft Copilot" OR "GitHub Copilot" OR "Copilot Studio" OR "Azure AI" OR "M365 Copilot") (発表 OR 更新 OR 新機能 OR 料金)')]},
 {"num":"⑥","name":"メタAI速報","env":"META","color":COL_BIZ,"sources":[
     rss("https://github.com/meta-llama/llama-models/releases.atom"),
     gn('("Meta AI" OR Llama OR "Llama 4" OR "Meta Llama" OR FAIR) (発表 OR 更新 OR 新モデル OR オープンソース)')]},
 {"num":"⑦","name":"中国AI速報","env":"CHINA","color":COL_BIZ,"sources":[
     rss("https://github.com/QwenLM/Qwen3/releases.atom"),
     rss("https://github.com/deepseek-ai/DeepSeek-V3/releases.atom"),
     rss("https://github.com/THUDM/GLM-4/releases.atom"),
     gn('(DeepSeek OR Qwen OR Kimi OR GLM OR Zhipu OR "Moonshot AI" OR MiniMax) (発表 OR 更新 OR 新モデル OR 中国AI)')]},
 # 🔬 分野別 ⑧〜⑫
 {"num":"⑧","name":"ローカルLLM速報","env":"LOCAL","color":COL_FIELD,"sources":[
     rss("https://huggingface.co/blog/feed.xml"),
     rss("https://ollama.com/blog/rss.xml"),
     rss("https://github.com/ollama/ollama/releases.atom"),
     rss("https://github.com/vllm-project/vllm/releases.atom"),
     gn('(ローカルLLM OR Ollama OR "Hugging Face" OR llama.cpp OR GGUF OR 量子化 OR "LM Studio" OR vLLM) (更新 OR 新モデル OR GPU OR 推論)')]},
 {"num":"⑨","name":"画像・動画AI速報","env":"IMGVID","color":COL_FIELD,"sources":[
     rss("https://github.com/comfyanonymous/ComfyUI/releases.atom"),
     gn('(Midjourney OR Sora OR Runway OR Kling OR Veo OR Imagen OR "Stable Diffusion" OR Flux OR Luma OR Pika) (発表 OR 更新 OR 新モデル OR 動画生成 OR 画像生成)')]},
 {"num":"⑩","name":"音声・音楽AI速報","env":"AUDIO","color":COL_FIELD,"sources":[
     gn('(Whisper OR ElevenLabs OR Suno OR Udio OR 音声合成 OR 音楽生成 OR 文字起こし OR VOICEVOX OR "voice AI") (発表 OR 更新 OR 新モデル OR API)')]},
 {"num":"⑪","name":"AIツール・エージェント速報","env":"TOOLS","color":COL_FIELD,"sources":[
     rss("https://github.com/openai/codex/releases.atom"),
     rss("https://cursor.com/changelog/rss.xml"),
     rss("https://github.com/n8n-io/n8n/releases.atom"),
     rss("https://github.com/langchain-ai/langchain/releases.atom"),
     gn('("Claude Code" OR Codex OR Cursor OR n8n OR MCP OR LangChain OR Devin OR AIエージェント) (更新 OR 発表 OR 新機能 OR リリース)')]},
 {"num":"⑫","name":"論文速報","env":"PAPERS","color":COL_FIELD,"sources":[
     rss("http://export.arxiv.org/rss/cs.AI", ["LLM","language model","agent","reasoning","RAG","GPT","diffusion","multimodal","RLHF"]),
     rss("http://export.arxiv.org/rss/cs.CL", ["LLM","language model","instruction","reasoning","RAG","dialogue","transformer"])]},
 # 🌐 総合 ⑬〜⑮
 {"num":"⑬","name":"AI最新速報（日本語）","env":"GENERAL","color":COL_SUM,"sources":[
     rss("https://rss.itmedia.co.jp/rss/2.0/aiplus.xml"),                               # ITmedia AI+（AI専門）
     rss("https://zenn.dev/topics/ai/feed"),
     rss("https://zenn.dev/topics/llm/feed"),
     rss("https://www.publickey1.jp/atom.xml", ["AI","LLM","Claude","GPT","Copilot","生成","Gemini"]),
     rss("https://gigazine.net/news/rss_2.0/", ["AI","LLM","ChatGPT","Claude","Gemini","生成","OpenAI"]),
     gn('(生成AI OR AIエージェント OR ChatGPT OR Claude OR Gemini OR ローカルLLM) (発表 OR 更新 OR 活用 OR 導入 OR 事例)')]},
 {"num":"⑭","name":"新モデルリリース速報","env":"RELEASE","color":COL_SUM,"sources":[
     gn('("新モデル" OR "モデル公開" OR "オープンウェイト" OR ベンチマーク OR 提供開始) (OpenAI OR Anthropic OR Google OR Meta OR DeepSeek OR Qwen OR xAI)'),
     gn('("new model" OR "model release" OR "open weights" OR "generally available" OR benchmark) (OpenAI OR Anthropic OR Google OR Meta OR DeepSeek OR Qwen OR xAI)')]},
 {"num":"⑮","name":"世界のAI動向・規制","env":"WORLD","color":COL_SUM,"sources":[
     rss("https://www.euractiv.com/sections/artificial-intelligence/feed/"),
     gn('(AI規制 OR AI政策 OR "生成AI 規制" OR AI著作権 OR "半導体 輸出規制" OR データセンター OR "AI 投資")'),
     gn('("AI regulation" OR "AI Act" OR "AI safety" OR "export controls" OR "AI copyright" OR "data center") when:7d')]},
]

def gn_url(q):
    return "https://news.google.com/rss/search?q=" + urllib.parse.quote(q) + "&hl=ja&gl=JP&ceid=JP:ja"

def clean(t):
    t = re.sub(r"<[^>]+>", " ", t or "")
    t = t.replace("&nbsp;", " ").replace("&amp;", "&").replace("&#39;", "'").replace("&quot;", '"').replace("&lt;", "<").replace("&gt;", ">")
    return re.sub(r"\s+", " ", t).strip()

# ---- 日本語化（英語タイトル/要約をGoogle翻訳で和訳・失敗時は原文＝重要ニュースは英語でも可）----
try:
    from deep_translator import GoogleTranslator
    _TR_OK = True
except ImportError:
    import subprocess
    subprocess.run([sys.executable, "-m", "pip", "install", "-q", "deep-translator"])
    try:
        from deep_translator import GoogleTranslator
        _TR_OK = True
    except Exception:
        _TR_OK = False

_JP_RE = re.compile(r"[ぁ-んァ-ヶ一-龠]")
def is_ja(t):
    if not t: return True
    return len(_JP_RE.findall(t)) >= max(3, int(len(t) * 0.12))   # 既に日本語なら翻訳しない
def to_ja(t):
    if not t or not _TR_OK or is_ja(t): return t
    try:
        return GoogleTranslator(source="auto", target="ja").translate(t[:4800]) or t
    except Exception:
        return t   # 翻訳失敗時は原文のまま（重要ニュースは英語でも可）

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
    url = gn_url(src["q"]) if src["type"] == "gn" else src["url"]
    try:
        f = feedparser.parse(url)
    except Exception as e:
        print("    fetch失敗:", url[:50], e); return []
    src_label = clean((f.feed.get("title", "") if getattr(f, "feed", None) else "")) or urllib.parse.urlparse(url).netloc
    must = src.get("must")
    out = []
    for e in f.entries[: PER_SOURCE * 5]:
        title = clean(e.get("title", ""))
        if not title: continue
        if must and not any(w.lower() in title.lower() for w in must): continue
        summ = clean(e.get("summary", "") or e.get("description", ""))
        if len(summ) < 25 or summ[:18] == title[:18]: summ = ""
        summ = summ[:160] + ("…" if len(summ) > 160 else "")
        out.append((title, e.get("link", ""), summ, src_label))
        if len(out) >= PER_SOURCE: break
    return out

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
        picked, keys = [], set()
        for src in t["sources"]:
            for title, link, summ, srclabel in fetch(src):
                if not link or link in seen or link in keys: continue
                keys.add(link); picked.append((title, link, summ, srclabel))
            time.sleep(0.4)
        picked = picked[: PER_CHANNEL]
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
