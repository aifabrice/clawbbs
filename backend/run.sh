#!/bin/zsh
cd /Users/fabrice/Services/ClawBBS/backend
source .venv/bin/activate

WORKERS=${CLAWBBS_UVICORN_WORKERS:-2}
exec uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers "$WORKERS"
