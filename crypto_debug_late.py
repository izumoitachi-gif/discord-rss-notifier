#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ================================================================
# crypto_debug_late.py — 段階1: 検知デバッグ(詰まり検知時のみログ・パパ設計)
# ----------------------------------------------------------------
# パパ要件5-47/5-49「ログになければ発火成功、つまり確定」
# =crypto_debug_late.jsonl は詰まりを検知した時だけ記録する異常系ログ
# =ログの有無自体がworkflow発火の可否判定になる
# ----------------------------------------------------------------
# 詰まり検知条件:
#   ① no_schedule_at_all: scheduled event=schedule のrunが0件
#   ② stagnation: 最新scheduled発火から15分以上経過(cron 5分/15分毎の想定を超過)
#   ③ consecutive_failure: 直近5runs中3件以上がfailure
#
# 詰まり検知した時のみ:
#   - crypto_debug_late.jsonl に1行追記(FIFO 1000行ローテーション)
#   - SOURCELOG CHへ警告Push
# 検知しなかった=すべて正常=発火成功確定 → ログ書かず・SOURCELOG投稿なし
# ================================================================
import io, sys, os, json, time, urllib.request, urllib.error
from datetime import datetime, timezone, timedelta

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

HERE = os.path.dirname(os.path.abspath(__file__))
DEBUG_FILE = os.path.join(HERE, "crypto_debug_late.jsonl")
WEBHOOKS_JSON = os.path.join(HERE, "market_webhooks.json")
UA = "KurenaiMarketMS/1.0 (izumoitachi@gmail.com)"
OWNER = "izumoitachi-gif"
REPO = "discord-rss-notifier"

# 監視対象workflow: 名前とcron間隔上限(この時間以上scheduled発火なければ詰まり)
WATCH_WORKFLOWS = [
    {"id": 318578372, "name": "crypto_price_notify",  "max_gap_min": 15},  # 5分毎cron→15分空きで詰まり
    {"id": 318553620, "name": "market_notify",         "max_gap_min": 45},  # 25,55/8,38/0*/3→45分空きで詰まり
]
# crypto_flash A/B/C/Dも監視対象に追加(存在してれば)
CRYPTO_FLASH_NAMES = ["crypto_flash_A", "crypto_flash_B", "crypto_flash_C", "crypto_flash_D"]

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

def gh_api(path, gh_token=None):
    headers = {"User-Agent": UA, "Accept": "application/vnd.github+json"}
    if gh_token:
        headers["Authorization"] = f"token {gh_token}"
    req = urllib.request.Request(f"https://api.github.com{path}", headers=headers)
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())

def parse_iso(s):
    return datetime.strptime(s.replace("Z", "+0000"), "%Y-%m-%dT%H:%M:%S%z")

def detect_stagnation(runs, wf_name, max_gap_min):
    """詰まり検知(3種類)。詰まってなければNone返す=ログ書かない"""
    now = datetime.now(timezone.utc)
    schedule_runs = [r for r in runs if r.get("event") == "schedule"]
    if not schedule_runs:
        return {"type": "no_schedule_at_all", "workflow": wf_name,
                "detail": f"scheduled event=schedule のrunが0件・cron発火してない"}
    latest = max(schedule_runs, key=lambda r: r["created_at"])
    delta = now - parse_iso(latest["created_at"])
    if delta > timedelta(minutes=max_gap_min):
        return {"type": "stagnation", "workflow": wf_name,
                "detail": f"最新schedule発火から{int(delta.total_seconds()/60)}分経過(閾値{max_gap_min}分)",
                "last_schedule_at": latest["created_at"]}
    recent_5 = sorted(runs, key=lambda r: r["created_at"], reverse=True)[:5]
    fails = [r for r in recent_5 if r.get("conclusion") == "failure"]
    if len(fails) >= 3:
        return {"type": "consecutive_failure", "workflow": wf_name,
                "detail": f"直近5runs中{len(fails)}件failure"}
    return None  # 詰まってない=発火成功=ログ書かない

def resolve_flash_workflows(gh_token):
    """crypto_flash A/B/C/D workflowのidを名前から解決"""
    try:
        data = gh_api(f"/repos/{OWNER}/{REPO}/actions/workflows?per_page=100", gh_token)
    except Exception:
        return []
    results = []
    for w in data.get("workflows", []):
        path = w.get("path", "")
        for name in CRYPTO_FLASH_NAMES:
            if name in path:
                results.append({"id": w["id"], "name": name, "max_gap_min": 30})
                break
    return results

def fetch_runs(wf_id, gh_token, limit=20):
    return gh_api(f"/repos/{OWNER}/{REPO}/actions/workflows/{wf_id}/runs?per_page={limit}", gh_token)

def append_and_rotate(entries):
    if os.path.exists(DEBUG_FILE):
        with io.open(DEBUG_FILE, encoding="utf-8") as f:
            lines = f.readlines()
    else:
        lines = []
    for e in entries:
        lines.append(json.dumps(e, ensure_ascii=False) + "\n")
    if len(lines) > MAX_LINES:
        lines = lines[FIFO_DELETE:]
        print(f"FIFO削除: 古い{FIFO_DELETE}行削除・現{len(lines)}行")
    with io.open(DEBUG_FILE, "w", encoding="utf-8") as f:
        f.writelines(lines)
    return len(lines)

def post_stagnation_alert(sourcelog_url, stagnations):
    if not sourcelog_url or not stagnations:
        return
    lines = ["**⚠️ 詰まり検知 (Late Slot / scheduled発火失敗)**"]
    for s in stagnations:
        icon = {"no_schedule_at_all":"🔴","stagnation":"🟡","consecutive_failure":"❌"}.get(s["type"],"⚠️")
        lines.append(f"- {icon} `{s['workflow']}` [{s['type']}] {s['detail']}")
    body = {"content": "\n".join(lines)}
    req = urllib.request.Request(sourcelog_url, data=json.dumps(body, ensure_ascii=False).encode(),
        headers={"Content-Type": "application/json", "User-Agent": UA}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            print(f"SOURCELOG警告投稿: {r.status}")
    except urllib.error.HTTPError as e:
        print(f"SOURCELOG警告失敗: {e.code}")

def main():
    hooks = load_webhooks()
    sourcelog_url = hooks.get("SOURCELOG")
    gh_token = os.environ.get("GH_READ_TOKEN", "")

    watch = list(WATCH_WORKFLOWS) + resolve_flash_workflows(gh_token)
    print(f"監視対象workflow: {len(watch)}本")

    stagnations = []
    for wf in watch:
        try:
            data = fetch_runs(wf["id"], gh_token)
            issue = detect_stagnation(data.get("workflow_runs", []), wf["name"], wf["max_gap_min"])
            status = "✅発火成功(ログ書かず)" if issue is None else f"⚠️詰まり検知[{issue['type']}]"
            print(f"  {wf['name']}: {status}")
            if issue:
                issue["ts_utc"] = int(time.time())
                stagnations.append(issue)
        except Exception as ex:
            print(f"  {wf['name']}: 監視失敗 {ex}")

    if not stagnations:
        print("=== 全workflow発火成功確定・ログ書かず・SOURCELOG投稿なし ===")
        return
    total = append_and_rotate(stagnations)
    print(f"詰まり{len(stagnations)}件検知・ログ全{total}行")
    post_stagnation_alert(sourcelog_url, stagnations)

if __name__ == "__main__":
    main()
