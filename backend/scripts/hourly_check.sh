#!/bin/zsh
set -e
REPO="/Users/fabrice/Services/ClawBBS"
LOG="$REPO/data/hourly_report.log"
mkdir -p "$REPO/data"

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

status="OK"
if ! run_tests >/dev/null 2>&1; then
  status="FAIL"
  # try restart once
  launchctl kickstart -k gui/$(id -u)/com.clawbbs.api >/dev/null 2>&1 || true
  sleep 2
  if run_tests >/dev/null 2>&1; then
    status="RECOVERED"
  else
    status="FAIL"
  fi
fi

echo "[$(stamp)] hourly_check: $status" >> "$LOG"

# auto-commit if repo changed (e.g., auto-fix ran elsewhere)
cd "$REPO"
if ! git diff --quiet; then
  git add .
  git commit -m "auto-fix: $(stamp)" || true
  git push || true
fi
