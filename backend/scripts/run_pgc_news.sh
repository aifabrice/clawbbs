#!/bin/zsh
set -e
cd /Users/fabrice/Services/ClawBBS/backend
source .venv/bin/activate
PYTHONPATH=. python scripts/pgc_news_tick.py
