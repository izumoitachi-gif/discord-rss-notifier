#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ================================================================
# crypto_debug_late.py — 段階1: 検知デバッグ(Late Slot可視化)
# ----------------------------------------------------------------
# パパ要件5-47「発火でエルサ通過しなかったら検知デバグ、デバグはPythonで
# 一定溜まったら古い方から削除が1、それでだめだったら21を4つに分けて」
# ----------------------------------------------------------------
# GitHub Actions APIから crypto_price_notify workflow の直近runsを取得
# → cron発火予定時刻(scheduled)と実際start時刻の差(遅延秒)を計算
# → crypto_debug_late.jsonl に1行=1record追加
# → 1000行超えたら古い100行FIFO削除
# → 遅延パターンをSOURCELOG CHへ集計投稿(30分毎)
# ================================================================
import io, sys, os, json, time, urllib.request, urllib.error
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

HERE = os.path.dirname(os.path.abspath(__file__))
DEBUG_FILE = os.path.join(HERE, "crypto_debug_late.jsonl")
WEBHOOKS_JSON = os.path.join(HERE, "market_webhooks.json")
UA = "KurenaiMarketMS/1.0 (izumoitachi@gmail.com)"
OWNER = "izumoitachi-gif"
REPO = "discord-rss-notifier"
WF_ID_CRYPTO = 318578372
WF_ID_MARKET = 318553620
MAX_LINES = 1000
FIFO_DELETE = 100

def load_webhooks():
    m = {}
    for k, v in os.environ.items():
        if k.startswith("MARKET_WEBHOOK_"):
            m[k.replace("MARKET_WEBHOOK_", "")] = v
    if "SOURCELOG" not in m and os.path.exists(WEBHOOKS_JSON):
        with io.open(WEBHOOKS_JSON, encoding="utf-8") as f:
            data = json.load(f)
        for slug, w in data.items():
            if w.get("url"):
                m.setdefault(slug, w["url"])
    return m

def fetch_runs(wf_id, gh_token, limit=20):
    """Public repoならtoken不要でも取れるが、rate limit回避のためあれば使う"""
    url = f"https://api.github.com/repos/{OWNER}/{REPO}/actions/workflows/{wf_id}/runs?per_page={limit}"
    headers = {"User-Agent": UA, "Accept": "application/vnd.github+json"}
    if gh_token:
        headers["Authorization"] = f"token {gh_token}"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())

def calc_delay(run):
    """scheduled発火予定時刻 vs 実start時刻の差(秒)"""
    if run.get("event") != "schedule":
        return None  # 手動やworkflow_runは対象外
    from datetime import datetime, timezone
    # GitHubは通常 created_at ≒ scheduled trigger時刻、run_started_at が実start
    created = run.get("created_at")
    started = run.get("run_started_at") or created
    if not created or not started:
        return None
    def parse(iso):
        return datetime.strptime(iso.replace("Z", "+0000"), "%Y-%m-%dT%H:%M:%S%z")
    return int((parse(started) - parse(created)).total_seconds())

def load_seen_ids():
    seen = set()
    if os.path.exists(DEBUG_FILE):
        with io.open(DEBUG_FILE, encoding="utf-8") as f:
            for line in f:
                try:
                    entry = json.loads(line)
                    seen.add(entry.get("run_id"))
                except Exception:
                    pass
    return seen

def append_and_rotate(new_entries):
    """新規追記+FIFO 1000行制限"""
    if os.path.exists(DEBUG_FILE):
        with io.open(DEBUG_FILE, encoding="utf-8") as f:
            lines = f.readlines()
    else:
        lines = []
    for e in new_entries:
        lines.append(json.dumps(e, ensure_ascii=False) + "\n")
    # ローテーション
    if len(lines) > MAX_LINES:
        lines = lines[FIFO_DELETE:]
        print(f"FIFO削除: 古い{FIFO_DELETE}行削除・現{len(lines)}行")
    with io.open(DEBUG_FILE, "w", encoding="utf-8") as f:
        f.writelines(lines)
    return len(lines)

def summarize(new_entries):
    """遅延の統計をSOURCELOG向けサマリに"""
    schedule_entries = [e for e in new_entries if e.get("event") == "schedule"]
    if not schedule_entries:
        return None
    delays = [e.get("delay_sec") for e in schedule_entries if e.get("delay_sec") is not None]
    if not delays:
        return None
    avg = sum(delays) / len(delays)
    max_delay = max(delays)
    min_delay = min(delays)
    conclusions = {}
    for e in schedule_entries:
        c = e.get("conclusion", "unknown")
        conclusions[c] = conclusions.get(c, 0) + 1
    return {
        "count": len(schedule_entries),
        "delay_avg_sec": int(avg),
        "delay_max_sec": max_delay,
        "delay_min_sec": min_delay,
        "conclusions": conclusions,
    }

def post_debug_summary(sourcelog_url, crypto_stat, market_stat):
    """SOURCELOG CHに遅延サマリを投稿"""
    if not sourcelog_url:
        print("SOURCELOG webhook未設定・投稿スキップ")
        return
    lines = ["**🔍 Late Slot 検知デバッグ(直近scheduled発火の遅延統計)**"]
    for name, s in [("crypto", crypto_stat), ("market", market_stat)]:
        if s is None:
            lines.append(f"- **{name}**: schedule発火なし(新規0件・詰まってる可能性)")
            continue
        lines.append(f"- **{name}**: {s['count']}件 / 遅延平均{s['delay_avg_sec']}秒 / 最大{s['delay_max_sec']}秒 / 結果{s['conclusions']}")
    body = {"content": "\n".join(lines)}
    req = urllib.request.Request(sourcelog_url, data=json.dumps(body, ensure_ascii=False).encode(),
        headers={"Content-Type": "application/json", "User-Agent": UA}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            print(f"SOURCELOG投稿: {r.status}")
    except urllib.error.HTTPError as e:
        print(f"SOURCELOG投稿失敗: {e.code}")

def collect_for_workflow(wf_id, wf_name, seen_ids, gh_token):
    """指定workflowの直近runsを取得→新規のみentry化"""
    try:
        data = fetch_runs(wf_id, gh_token)
    except Exception as ex:
        print(f"{wf_name} runs取得失敗: {ex}")
        return []
    entries = []
    for r in data.get("workflow_runs", []):
        run_id = r["id"]
        if run_id in seen_ids:
            continue
        entry = {
            "ts_utc": int(time.time()),
            "workflow": wf_name,
            "run_id": run_id,
            "created_at": r.get("created_at"),
            "run_started_at": r.get("run_started_at"),
            "event": r.get("event"),
            "status": r.get("status"),
            "conclusion": r.get("conclusion"),
            "delay_sec": calc_delay(r),
        }
        entries.append(entry)
    return entries

def main():
    hooks = load_webhooks()
    sourcelog_url = hooks.get("SOURCELOG")
    gh_token = os.environ.get("GH_READ_TOKEN", "")  # 任意・rate limit回避のみ

    seen_ids = load_seen_ids()
    crypto_new = collect_for_workflow(WF_ID_CRYPTO, "crypto", seen_ids, gh_token)
    market_new = collect_for_workflow(WF_ID_MARKET, "market", seen_ids, gh_token)
    all_new = crypto_new + market_new
    if not all_new:
        print("新規runsなし・スキップ")
        return
    total_lines = append_and_rotate(all_new)
    print(f"新規追加: crypto {len(crypto_new)}件・market {len(market_new)}件・全ログ{total_lines}行")

    crypto_stat = summarize(crypto_new)
    market_stat = summarize(market_new)
    post_debug_summary(sourcelog_url, crypto_stat, market_stat)

if __name__ == "__main__":
    main()
