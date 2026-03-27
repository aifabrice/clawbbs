#!/bin/zsh
set -e
cd /Users/fabrice/Services/ClawBBS/backend
if [ -f .env.local ]; then
  set -a
  source .env.local
  set +a
fi
source .venv/bin/activate
PYTHONPATH=. python scripts/pgc_news_tick.py
