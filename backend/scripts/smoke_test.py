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
        ("/skills", "我的龙虾"),
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

    status, body = fetch("/agent/skills")
    if status != 200:
        print("FAIL /agent/skills status", status)
        return 1
    try:
        json.loads(body)
    except Exception as e:
        print("FAIL /agent/skills json", e)
        return 1

    status, body = fetch("/agent/capabilities")
    if status != 200:
        print("FAIL /agent/capabilities status", status)
        return 1
    try:
        data = json.loads(body)
        if not data.get("auth_header"):
            print("FAIL /agent/capabilities missing auth_header")
            return 1
    except Exception as e:
        print("FAIL /agent/capabilities json", e)
        return 1

    print("OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
