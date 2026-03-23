#!/usr/bin/env bash
set -euo pipefail

BASE_DIR="$(cd "$(dirname "$0")" && pwd)"
MODE="${1:-morning}"

case "$MODE" in
  morning)
    exec python3 "$BASE_DIR/run_morning_report.py"
    ;;
  close)
    exec python3 "$BASE_DIR/run_daily_close.py"
    ;;
  status)
    exec python3 "$BASE_DIR/run_status.py"
    ;;
  *)
    echo "Usage: $0 {morning|close|status}" >&2
    exit 2
    ;;
esac
