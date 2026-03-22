import fcntl
import json
import os
import sys
from pathlib import Path

from sqlmodel import Session

from app.config import PGC_ENABLED
from app.db import engine, init_db
from app.services.pgc_news import run_pgc_news_tick


LOG_PATH = Path("/Users/fabrice/Services/ClawBBS/data/pgc_news.log")
LOCK_PATH = Path("/Users/fabrice/Services/ClawBBS/data/pgc_news.lock")


def log(line: str) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(line.rstrip() + "\n")


def main() -> int:
    if not PGC_ENABLED:
        log("PGC tick skipped: PGC_ENABLED=0")
        return 0
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOCK_PATH.open("w", encoding="utf-8") as lock_file:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            log("PGC tick skipped: lock busy")
            return 0
        init_db()
        with Session(engine) as session:
            result = run_pgc_news_tick(session)
        line = json.dumps(result, ensure_ascii=False)
        print(line)
        log(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
