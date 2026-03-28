from __future__ import annotations

import json

from config import STATE_FILE, REPORT_DIR, ensure_dirs


def main() -> None:
    ensure_dirs()
    if not STATE_FILE.exists():
        print("state not found")
        return
    state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    print(json.dumps(state, ensure_ascii=False, indent=2))
    print(f"reports_dir={REPORT_DIR}")


if __name__ == "__main__":
    main()
