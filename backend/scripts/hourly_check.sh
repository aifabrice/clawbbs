#!/bin/zsh
set -e
REPO="/Users/fabrice/Services/ClawBBS"
LOG="$REPO/data/hourly_report.log"
AUTOFIX_LOG="$REPO/ops/hourly_autofix.log"
mkdir -p "$REPO/data" "$REPO/ops"
FEISHU_WEBHOOK_URL="${FEISHU_WEBHOOK_URL:-}"
if [[ -z "$FEISHU_WEBHOOK_URL" && -f "$REPO/data/feishu_webhook.txt" ]]; then
  FEISHU_WEBHOOK_URL=$(head -n 1 "$REPO/data/feishu_webhook.txt" | tr -d '\r\n')
fi
FEISHU_APP_ID="${FEISHU_APP_ID:-}"
FEISHU_APP_SECRET="${FEISHU_APP_SECRET:-}"
FEISHU_CHAT_ID="${FEISHU_CHAT_ID:-oc_453d3784e752918f1b45b8b14df815de}"
if [[ -z "$FEISHU_APP_ID" || -z "$FEISHU_APP_SECRET" ]]; then
  if [[ -f "$HOME/.openclaw/openclaw.json" ]]; then
    read -r FEISHU_APP_ID FEISHU_APP_SECRET <<<"$(/usr/bin/python3 - <<'PY'
import json, os
path=os.path.expanduser('~/.openclaw/openclaw.json')
try:
    with open(path,'r') as f:
        data=json.load(f)
    feishu=data.get('channels',{}).get('feishu',{})
    app_id=feishu.get('appId','')
    app_secret=feishu.get('appSecret','')
    print(f"{app_id}\t{app_secret}")
except Exception:
    print("\t")
PY
)"
  fi
fi

stamp() {
  date "+%Y-%m-%d %H:%M:%S"
}

cd "$REPO/backend"
source .venv/bin/activate

# Seed data to keep demo alive
PYTHONPATH=. python scripts/seed.py >/dev/null 2>&1 || true

run_tests() {
  python scripts/smoke_test.py
}

run_status="OK"
if ! run_tests >/dev/null 2>&1; then
  run_status="FAIL"
  # try restart once
  launchctl kickstart -k gui/$(id -u)/com.clawbbs.api >/dev/null 2>&1 || true
  sleep 2
  if run_tests >/dev/null 2>&1; then
    run_status="RECOVERED"
  else
    run_status="FAIL"
  fi
fi

counts=$(python - <<'PY'
import json, urllib.request
base='http://127.0.0.1:8000'
try:
    with urllib.request.urlopen(base + '/agent/feed', timeout=5) as r:
        data=json.loads(r.read().decode('utf-8'))
        posts=len(data.get('items',[]))
except Exception:
    posts='?'
try:
    with urllib.request.urlopen(base + '/agent/skills', timeout=5) as r:
        data=json.loads(r.read().decode('utf-8'))
        skills=len(data.get('items',[]))
except Exception:
    skills='?'
print(f"posts={posts} skills={skills}")
PY
)

echo "[$(stamp)] hourly_check: $run_status | $counts" >> "$LOG"

echo "[$(stamp)] $run_status | $counts" >> "$AUTOFIX_LOG"

# auto-commit to auto-fix (always update hourly log)
cd "$REPO"
if git show-ref --verify --quiet refs/heads/auto-fix; then
  git checkout auto-fix >/dev/null 2>&1 || true
else
  git checkout -b auto-fix >/dev/null 2>&1 || true
fi
# ensure hourly log is tracked
if [ ! -f "$AUTOFIX_LOG" ]; then
  echo "# ClawBBS hourly checks" > "$AUTOFIX_LOG"
fi

changes=$(git status --porcelain | awk '{print $2}' | grep -v '^ops/hourly_autofix.log$' | head -n 5 | paste -sd ',' -)
if [[ -z "$changes" ]]; then
  changes="仅日志"
fi

if ! git diff --quiet; then
  git add .
  git commit -m "auto-fix: $(stamp)" || true
  git push -u origin auto-fix || true
fi

# push hourly status to Feishu group (webhook preferred, else app token)
msg="[ClawBBS Hourly] $(stamp) | $run_status | $counts | 变更: $changes"
if [[ -n "$FEISHU_WEBHOOK_URL" ]]; then
  payload=$(python - <<PY
import json
print(json.dumps({"msg_type":"text","content":{"text": "$msg"}}))
PY
)
  curl -s -X POST -H 'Content-Type: application/json' -d "$payload" "$FEISHU_WEBHOOK_URL" >/dev/null 2>&1 || true
elif [[ -n "$FEISHU_APP_ID" && -n "$FEISHU_APP_SECRET" && -n "$FEISHU_CHAT_ID" ]]; then
  FEISHU_MSG="$msg" FEISHU_APP_ID="$FEISHU_APP_ID" FEISHU_APP_SECRET="$FEISHU_APP_SECRET" FEISHU_CHAT_ID="$FEISHU_CHAT_ID" python - <<'PY' || true
import json, os, urllib.request
app_id=os.environ.get('FEISHU_APP_ID','')
app_secret=os.environ.get('FEISHU_APP_SECRET','')
chat_id=os.environ.get('FEISHU_CHAT_ID','')
msg=os.environ.get('FEISHU_MSG','')
if not (app_id and app_secret and chat_id and msg):
    raise SystemExit(0)
# tenant token
data=json.dumps({"app_id": app_id, "app_secret": app_secret}).encode('utf-8')
req=urllib.request.Request(
    "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
    data=data,
    headers={"Content-Type":"application/json"},
)
resp=json.loads(urllib.request.urlopen(req, timeout=10).read().decode('utf-8'))
token=resp.get('tenant_access_token')
if not token:
    raise SystemExit(0)
body={
    "receive_id": chat_id,
    "msg_type": "text",
    "content": json.dumps({"text": msg})
}
req=urllib.request.Request(
    "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=chat_id",
    data=json.dumps(body).encode('utf-8'),
    headers={"Content-Type":"application/json","Authorization": f"Bearer {token}"},
)
urllib.request.urlopen(req, timeout=10).read()
PY
fi
