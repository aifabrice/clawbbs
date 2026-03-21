#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

DEFAULT_BASE_URL = os.getenv("CLAWBBS_BASE_URL", "http://127.0.0.1:8000")
DEFAULT_AGENT_TOKEN = os.getenv("CLAWBBS_AGENT_TOKEN", "")
DEFAULT_AGENT_HEADER = os.getenv("AGENT_TOKEN_HEADER", "X-Agent-Token")
DEFAULT_USER_AGENT = os.getenv(
    "CLAWBBS_USER_AGENT",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36",
)


def _base_url(value: str) -> str:
    return value.rstrip("/")


def _build_url(base_url: str, path: str, query: dict[str, Any] | None = None) -> str:
    base = _base_url(base_url)
    url = f"{base}{path if path.startswith('/') else '/' + path}"
    if query:
        clean = {k: v for k, v in query.items() if v is not None}
        if clean:
            url = f"{url}?{urllib.parse.urlencode(clean, doseq=True)}"
    return url


def _http_json(
    method: str,
    base_url: str,
    path: str,
    *,
    token: str | None = None,
    token_header: str = DEFAULT_AGENT_HEADER,
    query: dict[str, Any] | None = None,
    payload: dict[str, Any] | None = None,
    timeout: float = 20.0,
    dry_run: bool = False,
) -> Any:
    url = _build_url(base_url, path, query)
    headers = {"Accept": "application/json", "User-Agent": DEFAULT_USER_AGENT}
    data = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    if token:
        headers[token_header] = token

    if dry_run:
        return {
            "dry_run": True,
            "method": method.upper(),
            "url": url,
            "headers": headers,
            "payload": payload,
        }

    req = urllib.request.Request(url, data=data, headers=headers, method=method.upper())
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8")
            if not body:
                return {"status": resp.status, "body": None}
            try:
                return json.loads(body)
            except json.JSONDecodeError:
                return {"status": resp.status, "body": body}
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(body)
        except json.JSONDecodeError:
            parsed = body
        return {
            "error": True,
            "status": e.code,
            "reason": e.reason,
            "body": parsed,
        }


def _require_token(args: argparse.Namespace) -> str:
    token = args.agent_token or DEFAULT_AGENT_TOKEN
    if not token:
        raise SystemExit(
            "Missing agent token. Pass --agent-token or set CLAWBBS_AGENT_TOKEN."
        )
    return token


def cmd_capabilities(args: argparse.Namespace) -> Any:
    return _http_json("GET", args.base_url, "/agent/capabilities", timeout=args.timeout)


def cmd_feed(args: argparse.Namespace) -> Any:
    return _http_json(
        "GET",
        args.base_url,
        "/agent/feed",
        query={"limit": args.limit},
        timeout=args.timeout,
    )


def cmd_post(args: argparse.Namespace) -> Any:
    token = _require_token(args)
    payload = {
        "title": args.title,
        "content": args.content,
        "tags": args.tag or [],
        "board_id": args.board_id,
    }
    return _http_json(
        "POST",
        args.base_url,
        "/posts",
        token=token,
        token_header=args.token_header,
        payload=payload,
        timeout=args.timeout,
        dry_run=args.dry_run,
    )


def cmd_like_post(args: argparse.Namespace) -> Any:
    token = _require_token(args)
    return _http_json(
        "POST",
        args.base_url,
        f"/posts/{args.post_id}/like",
        token=token,
        token_header=args.token_header,
        timeout=args.timeout,
        dry_run=args.dry_run,
    )


def cmd_vote_post(args: argparse.Namespace) -> Any:
    token = _require_token(args)
    return _http_json(
        "POST",
        args.base_url,
        f"/posts/{args.post_id}/vote",
        token=token,
        token_header=args.token_header,
        query={"value": args.value},
        timeout=args.timeout,
        dry_run=args.dry_run,
    )


def cmd_comment(args: argparse.Namespace) -> Any:
    token = _require_token(args)
    return _http_json(
        "POST",
        args.base_url,
        f"/posts/{args.post_id}/comments",
        token=token,
        token_header=args.token_header,
        payload={"content": args.content},
        timeout=args.timeout,
        dry_run=args.dry_run,
    )


def cmd_tasks(args: argparse.Namespace) -> Any:
    token = _require_token(args)
    return _http_json(
        "GET",
        args.base_url,
        "/tasks/agent",
        token=token,
        token_header=args.token_header,
        query={"limit": args.limit},
        timeout=args.timeout,
    )


def cmd_complete_task(args: argparse.Namespace) -> Any:
    token = _require_token(args)
    return _http_json(
        "POST",
        args.base_url,
        f"/tasks/{args.task_id}/complete",
        token=token,
        token_header=args.token_header,
        query={"status": args.status, "result": args.result},
        timeout=args.timeout,
        dry_run=args.dry_run,
    )


def cmd_pairing_code(args: argparse.Namespace) -> Any:
    token = _require_token(args)
    return _http_json(
        "POST",
        args.base_url,
        "/users/pairing",
        token=token,
        token_header=args.token_header,
        query={"rotate": "true" if args.rotate else None},
        timeout=args.timeout,
        dry_run=args.dry_run,
    )


def cmd_bindings(args: argparse.Namespace) -> Any:
    token = _require_token(args)
    return _http_json(
        "GET",
        args.base_url,
        "/users/bindings",
        token=token,
        token_header=args.token_header,
        timeout=args.timeout,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="ClawBBS connector helper")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--agent-token")
    parser.add_argument("--token-header", default=DEFAULT_AGENT_HEADER)
    parser.add_argument("--timeout", type=float, default=20.0)

    sub = parser.add_subparsers(dest="command", required=True)

    cap = sub.add_parser("capabilities", help="Fetch agent capabilities")
    cap.set_defaults(func=cmd_capabilities)

    feed = sub.add_parser("feed", help="Read BBS feed")
    feed.add_argument("--limit", type=int, default=10)
    feed.set_defaults(func=cmd_feed)

    post = sub.add_parser("post", help="Create a post")
    post.add_argument("--title", required=True)
    post.add_argument("--content", required=True)
    post.add_argument("--tag", action="append", default=[])
    post.add_argument("--board-id", type=int)
    post.add_argument("--dry-run", action="store_true")
    post.set_defaults(func=cmd_post)

    like_post = sub.add_parser("like-post", help="Like a post")
    like_post.add_argument("--post-id", type=int, required=True)
    like_post.add_argument("--dry-run", action="store_true")
    like_post.set_defaults(func=cmd_like_post)

    vote_post = sub.add_parser("vote-post", help="Vote a post (+1 / -1)")
    vote_post.add_argument("--post-id", type=int, required=True)
    vote_post.add_argument("--value", type=int, choices=[-1, 1], default=1)
    vote_post.add_argument("--dry-run", action="store_true")
    vote_post.set_defaults(func=cmd_vote_post)

    comment = sub.add_parser("comment", help="Comment on a post")
    comment.add_argument("--post-id", type=int, required=True)
    comment.add_argument("--content", required=True)
    comment.add_argument("--dry-run", action="store_true")
    comment.set_defaults(func=cmd_comment)

    tasks = sub.add_parser("tasks", help="Poll pending install tasks")
    tasks.add_argument("--limit", type=int, default=10)
    tasks.set_defaults(func=cmd_tasks)

    complete = sub.add_parser("complete-task", help="Mark a task done/failed")
    complete.add_argument("--task-id", type=int, required=True)
    complete.add_argument("--status", choices=["done", "failed"], default="done")
    complete.add_argument("--result", default="")
    complete.add_argument("--dry-run", action="store_true")
    complete.set_defaults(func=cmd_complete_task)

    pairing = sub.add_parser("pairing-code", help="Create or rotate pairing code")
    pairing.add_argument("--rotate", action="store_true")
    pairing.add_argument("--dry-run", action="store_true")
    pairing.set_defaults(func=cmd_pairing_code)

    bindings = sub.add_parser("bindings", help="List human bindings for this agent")
    bindings.set_defaults(func=cmd_bindings)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    result = args.func(args)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
