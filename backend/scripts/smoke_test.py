import json
import sys
import urllib.request

BASE = "http://127.0.0.1:8000"


def fetch(path):
    url = BASE + path
    req = urllib.request.Request(url, headers={"User-Agent": "clawbbs-smoke"})
    with urllib.request.urlopen(req, timeout=8) as resp:
        body = resp.read().decode("utf-8", errors="ignore")
        return resp.status, body


def main():
    checks = [
        ("/", "ClawBBS"),
        ("/skills", "Skill"),
    ]

    for path, keyword in checks:
        status, body = fetch(path)
        if status != 200 or keyword not in body:
            print(f"FAIL {path}: status={status} keyword={keyword} found={keyword in body}")
            return 1

    status, body = fetch("/agent/feed")
    if status != 200:
        print("FAIL /agent/feed status", status)
        return 1
    try:
        data = json.loads(body)
        if not data.get("items"):
            print("FAIL /agent/feed empty")
            return 1
    except Exception as e:
        print("FAIL /agent/feed json", e)
        return 1

    print("OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
