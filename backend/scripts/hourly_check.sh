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
if ! git diff --quiet; then
  git add .
  git commit -m "auto-fix: $(stamp)" || true
  git push -u origin auto-fix || true
fi

# optional: push hourly status to Feishu group via webhook
if [[ -n "$FEISHU_WEBHOOK_URL" ]]; then
  msg="[ClawBBS Hourly] $(stamp) | $run_status | $counts"
  payload=$(python - <<PY
import json
print(json.dumps({"msg_type":"text","content":{"text": "$msg"}}))
PY
)
  curl -s -X POST -H 'Content-Type: application/json' -d "$payload" "$FEISHU_WEBHOOK_URL" >/dev/null 2>&1 || true
fi
