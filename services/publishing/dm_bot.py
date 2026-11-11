"""Comment-to-DM link gate for published Reels (official Instagram Graph API only).

Reels end with "comment LETS GO and I'll DM you the link". This bot reads the
comments on every published reel, finds the ones that say "let's go" (tolerant
matching), sends that commenter a private reply with the reel's link, and can
optionally post a public reply so others see the gate works.

Credentials come from .env (IG_ACCESS_TOKEN, IG_USER_ID); the token is never printed.
State lives in data/database/dm_bot.json so no comment is ever answered twice.

Usage:
    python -m services.publishing.dm_bot                 # dry run (default): lists what would get a DM
    python -m services.publishing.dm_bot --send          # send private replies
    python -m services.publishing.dm_bot --send --public-reply
    python -m services.publishing.dm_bot --watch --interval 60
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path
from typing import Callable, Iterable

import httpx

from services.common import paths
from services.publishing.instagram import API_VERSION, _env, graph_base

STATE_FILE = paths.DATA / "database" / "dm_bot.json"
IG_MESSAGES_HOST = f"https://graph.instagram.com/{API_VERSION}"
DEFAULT_MESSAGE = "Here is the link: {link}\n\nIf the reel helped, a follow keeps them coming. \U0001F525"
DEFAULT_PUBLIC_TEXT = "Sent, check your DMs \U0001F4E9"
PERMISSION_HINT = ("app needs App Review for instagram_manage_messages / instagram_business_manage_messages "
                   "(Development mode only reaches users with a role on the app; also allow message access under "
                   "Instagram Settings > Messages and story replies > Connected tools)")

# "let" + up to two non-alphanumerics (apostrophe, curly quote, space) + "s" + optional spaces + "go" with any
# number of trailing o's, not glued to another letter on either side. "let go" (no s) does not match.
_LETS_GO = re.compile(r"(?<![a-z0-9])let[^a-z0-9]{0,2}s\s*go+(?![a-z])", re.IGNORECASE)

FetchFn = Callable[[str], list[dict]]
SendFn = Callable[[str, str], dict]


class GraphError(RuntimeError):
    def __init__(self, message: str, code: int | None = None, subcode: int | None = None, host: str | None = None):
        super().__init__(message)
        self.code, self.subcode, self.host = code, subcode, host


# ---------- pure helpers (covered by tests, no network) ----------

def is_lets_go(text: str | None) -> bool:
    """True when the comment contains a 'let's go' trigger in any common spelling."""
    if not text:
        return False
    return _LETS_GO.search(text) is not None


def link_for_package(pkg_dir: Path) -> str | None:
    """`dm_link` in package.json wins; else the first non-empty line of sources.txt; else None."""
    pkg_dir = Path(pkg_dir)
    meta_file = pkg_dir / "package.json"
    if meta_file.exists():
        try:
            meta = json.loads(meta_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            meta = {}
        link = meta.get("dm_link")
        if isinstance(link, str) and link.strip():
            return link.strip()
    sources = pkg_dir / "sources.txt"
    if sources.exists():
        for line in sources.read_text(encoding="utf-8").splitlines():
            if line.strip():
                return line.strip()
    return None


def format_message(template: str, link: str) -> str:
    """Substitute {link}; a literal backslash-n typed on the command line becomes a newline."""
    return template.replace("\\n", "\n").replace("{link}", link)


def published_packages(approved_root: Path | None = None) -> dict[str, dict]:
    """media_id -> {dir, id, link, post_url} for every approved package with status published."""
    root = Path(approved_root) if approved_root else paths.APPROVED
    out: dict[str, dict] = {}
    for meta_file in sorted(root.glob("*/*/package.json")):
        try:
            meta = json.loads(meta_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if meta.get("status") != "published" or not meta.get("media_id"):
            continue
        out[str(meta["media_id"])] = {"dir": meta_file.parent, "id": meta.get("id"), "link": link_for_package(meta_file.parent),
                                      "post_url": meta.get("post_url")}
    return out


def load_state(path: Path = STATE_FILE) -> dict:
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                data.setdefault("handled", {})
                return data
        except json.JSONDecodeError:
            pass
    return {"handled": {}}


def save_state(state: dict, path: Path = STATE_FILE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=1, ensure_ascii=False), encoding="utf-8")


def _scrub(text: str, token: str | None) -> str:
    return text.replace(token, "***") if token else text


def _error_of(body: dict, host: str) -> GraphError:
    err = body.get("error") or {}
    return GraphError(str(err.get("message") or body), err.get("code"), err.get("error_subcode"), host)


def looks_like_permission_error(err: GraphError | None) -> bool:
    if err is None:
        return False
    if err.code in (10, 200, 230, 190) or err.subcode in (2534022, 2534001):
        return True
    msg = str(err).lower()
    return "permission" in msg or "advanced access" in msg or "app review" in msg


# ---------- Graph API calls ----------

def fetch_comments(media_id: str, token: str, client: httpx.Client | None = None) -> list[dict]:
    """All top-level comments on a media object, following paging. GET only."""
    own = client is None
    client = client or httpx.Client(timeout=30)
    try:
        out: list[dict] = []
        url = f"{graph_base()}/{media_id}/comments"
        params: dict = {"fields": "id,text,username,timestamp,from", "limit": 50, "access_token": token}
        while url:
            body = client.get(url, params=params).json()
            if "error" in body:
                err = _error_of(body, graph_base())
                if err.code == 100 and "from" in params.get("fields", ""):
                    params["fields"] = "id,text,username,timestamp"  # some token flavours cannot read `from`
                    continue
                raise err
            out.extend(body.get("data", []))
            url = (body.get("paging") or {}).get("next")
            params = {"access_token": token} if url and "access_token" not in url else {}
        return out
    finally:
        if own:
            client.close()


def send_private_reply(ig_user: str, token: str, comment_id: str, text: str, client: httpx.Client | None = None) -> dict:
    """Private reply to a comment via the Messaging API. Tries graph_base() first, then graph.instagram.com."""
    own = client is None
    client = client or httpx.Client(timeout=30)
    payload = {"recipient": {"comment_id": comment_id}, "message": {"text": text}}
    hosts = [graph_base()]
    if IG_MESSAGES_HOST not in hosts:
        hosts.append(IG_MESSAGES_HOST)
    last: GraphError | None = None
    try:
        for host in hosts:
            try:
                r = client.post(f"{host}/{ig_user}/messages", json=payload, params={"access_token": token},
                                headers={"Authorization": f"Bearer {token}"})
                body = r.json()
            except (httpx.HTTPError, ValueError) as exc:
                last = GraphError(_scrub(f"{type(exc).__name__}: {exc}", token), host=host)
                continue
            if r.status_code == 200 and "error" not in body:
                return {"ok": True, "host": host, "message_id": body.get("message_id") or body.get("id"), "error": None}
            last = _error_of(body, host)
        return {"ok": False, "host": last.host if last else None, "error": str(last), "code": last.code if last else None,
                "subcode": last.subcode if last else None, "permission": looks_like_permission_error(last)}
    finally:
        if own:
            client.close()


def send_public_reply(comment_id: str, token: str, text: str, client: httpx.Client | None = None) -> dict:
    own = client is None
    client = client or httpx.Client(timeout=30)
    try:
        r = client.post(f"{graph_base()}/{comment_id}/replies", data={"message": text, "access_token": token})
        body = r.json()
        if r.status_code == 200 and "error" not in body:
            return {"ok": True, "id": body.get("id"), "error": None}
        err = _error_of(body, graph_base())
        return {"ok": False, "id": None, "error": str(err), "code": err.code, "subcode": err.subcode}
    except (httpx.HTTPError, ValueError) as exc:
        return {"ok": False, "id": None, "error": _scrub(f"{type(exc).__name__}: {exc}", token)}
    finally:
        if own:
            client.close()


def own_identity(ig_user: str | None, token: str | None) -> set[str]:
    """Ids/usernames that identify the account itself (its own replies are never DMed)."""
    ids = {s for s in (ig_user, _env("IG_USERNAME")) if s}
    if ig_user and token:
        try:
            me = httpx.get(f"{graph_base()}/{ig_user}", params={"fields": "username", "access_token": token}, timeout=30).json()
            if me.get("username"):
                ids.add(str(me["username"]))
        except (httpx.HTTPError, ValueError):
            pass
    return {s.lower() for s in ids}


# ---------- delivery queue ----------

RETRY_MINUTES = [2, 5, 15, 30, 60]       # then every 60 minutes
MAX_AGE_S = 7 * 24 * 3600                # Instagram allows private replies for 7 days after the comment
MAX_ATTEMPTS = 100


def _backoff_s(attempts: int) -> int:
    return 60 * RETRY_MINUTES[min(attempts, len(RETRY_MINUTES)) - 1]


def enqueue(state: dict, cid: str, entry: dict, text_out: str) -> bool:
    """Put a matching comment on the delivery queue once. Returns True when newly queued."""
    queue = state.setdefault("queue", {})
    if cid in queue:
        return False
    queue[cid] = {**entry, "message": text_out, "attempts": 0, "next_at": 0, "enqueued_at": time.time(), "last_error": None}
    return True


def process_queue(state: dict, send_private, send_public, public_text: str, public_reply: bool, summary: dict, now: float | None = None) -> None:
    """Deliver every due queue entry, oldest first. Failures stay queued with a longer wait."""
    now = now or time.time()
    queue: dict = state.setdefault("queue", {})
    handled: dict = state["handled"]
    for cid in sorted(queue, key=lambda k: queue[k].get("enqueued_at", 0)):
        item = queue[cid]
        if item.get("next_at", 0) > now:
            summary["pending"] += 1
            continue
        if now - item.get("enqueued_at", now) > MAX_AGE_S or item.get("attempts", 0) >= MAX_ATTEMPTS:
            handled[cid] = {"media_id": item.get("media_id"), "username": item.get("username"), "ok": False, "dropped": True,
                            "error": f"gave up after {item.get('attempts', 0)} attempts: {item.get('last_error')}"}
            summary["dropped"] += 1
            del queue[cid]
            continue
        result = send_private(cid, item["message"])
        item["attempts"] = item.get("attempts", 0) + 1
        if item["attempts"] > 1:
            summary["retried"] += 1
        record = {"media_id": item.get("media_id"), "username": item.get("username"), "sent_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
                  "host": result.get("host"), "ok": bool(result.get("ok")), "error": result.get("error"), "attempts": item["attempts"]}
        if record["ok"]:
            summary["sent"] += 1
            summary["sent_to"].append({k: item.get(k) for k in ("media_id", "username", "text", "comment_id", "reel")} | {"host": result.get("host"), "attempts": item["attempts"]})
            if public_reply and send_public is not None:
                pub = send_public(cid, public_text)
                record["public_reply"] = pub.get("id") if pub.get("ok") else None
                if not pub.get("ok"):
                    record["public_error"] = pub.get("error")
                    summary["failures"].append({"comment_id": cid, "username": item.get("username"), "stage": "public_reply", "error": pub.get("error")})
            handled[cid] = record
            del queue[cid]
        else:
            item["last_error"] = result.get("error")
            item["next_at"] = now + _backoff_s(item["attempts"])
            handled[cid] = record
            summary["failed"] += 1
            summary["pending"] += 1
            summary["failures"].append({"comment_id": cid, "username": item.get("username"), "stage": "private_reply", "host": result.get("host"),
                                        "error": result.get("error"), "attempt": item["attempts"], "retry_in_s": int(item["next_at"] - now)})
            if result.get("permission"):
                summary["permission_hint"] = PERMISSION_HINT


# ---------- one pass ----------

def run_once(*, send: bool = False, public_reply: bool = False, media_ids: Iterable[str] | None = None,
             template: str = DEFAULT_MESSAGE, public_text: str = DEFAULT_PUBLIC_TEXT,
             approved_root: Path | None = None, state_path: Path = STATE_FILE,
             fetch: FetchFn | None = None, send_private: SendFn | None = None, send_public: SendFn | None = None,
             own: set[str] | None = None, token: str | None = None, ig_user: str | None = None,
             now: float | None = None) -> dict:
    """Scan comments, queue the matching ones, deliver the queue (when send=True), persist state, return a summary.

    Every LETS GO comment goes on a persistent queue first; a failed send stays queued and is
    retried with a growing wait (2, 5, 15, 30, 60 min, then hourly) for up to seven days.

    `fetch`, `send_private`, `send_public` and `own` can be injected for tests (no network)."""
    token = token if token is not None else _env("IG_ACCESS_TOKEN")
    ig_user = ig_user if ig_user is not None else _env("IG_USER_ID")
    if fetch is None:
        if not token:
            raise RuntimeError("IG_ACCESS_TOKEN is empty in .env")
        fetch = lambda mid: fetch_comments(mid, token)  # noqa: E731
    if send and send_private is None:
        if not token or not ig_user:
            raise RuntimeError("IG_ACCESS_TOKEN and IG_USER_ID must be set in .env before --send")
        send_private = lambda cid, text: send_private_reply(ig_user, token, cid, text)  # noqa: E731
    if send and public_reply and send_public is None:
        send_public = lambda cid, text: send_public_reply(cid, token, text)  # noqa: E731
    if own is None:
        own = own_identity(ig_user, token) if send or token else {s.lower() for s in (ig_user,) if s}

    packages = published_packages(approved_root)
    wanted = [str(m) for m in media_ids] if media_ids else list(packages)
    state = load_state(state_path)
    handled: dict = state["handled"]
    summary = {"mode": "SEND" if send else "DRY RUN", "public_reply": bool(send and public_reply), "media": len(wanted),
               "scanned": 0, "matched": 0, "queued": 0, "sent": 0, "retried": 0, "pending": 0, "dropped": 0,
               "skipped_already": 0, "skipped_own": 0, "failed": 0,
               "would_dm": [], "sent_to": [], "failures": [], "media_errors": [], "permission_hint": None}

    for media_id in wanted:
        pkg = packages.get(media_id)
        link = pkg["link"] if pkg else None
        if not link:
            summary["media_errors"].append({"media_id": media_id, "error": "no link: add dm_link to package.json or a first line in sources.txt"})
            continue
        try:
            comments = fetch(media_id)
        except Exception as exc:  # one bad media must not stop the others
            summary["media_errors"].append({"media_id": media_id, "error": _scrub(str(exc), token)})
            continue
        text_out = format_message(template, link)
        for c in comments:
            cid = str(c.get("id") or "")
            if not cid:
                continue
            summary["scanned"] += 1
            if not is_lets_go(c.get("text")):
                continue
            summary["matched"] += 1
            username = str(c.get("username") or (c.get("from") or {}).get("username") or "")
            from_id = str((c.get("from") or {}).get("id") or "")
            if (username and username.lower() in own) or (from_id and from_id.lower() in own):
                summary["skipped_own"] += 1
                continue
            prior = handled.get(cid)
            if prior and prior.get("ok"):
                summary["skipped_already"] += 1
                continue
            entry = {"media_id": media_id, "username": username, "text": c.get("text"), "comment_id": cid, "reel": pkg.get("id") if pkg else None}
            if not send:
                summary["would_dm"].append(entry)
                continue
            if enqueue(state, cid, entry, text_out):
                summary["queued"] += 1
    if send:
        process_queue(state, send_private, send_public, public_text, public_reply, summary, now=now)
        save_state(state, state_path)
    return summary


# ---------- CLI ----------

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Comment-to-DM link gate for published Reels (official Graph API only).")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="list matching comments that would get a DM, send nothing (default)")
    mode.add_argument("--send", action="store_true", help="actually send private replies")
    ap.add_argument("--public-reply", action="store_true", help="with --send: also post a public reply under the comment")
    ap.add_argument("--media", action="append", help="restrict to one media id (repeatable); default: all published packages")
    ap.add_argument("--message", default=DEFAULT_MESSAGE, help="DM template, {link} is substituted")
    ap.add_argument("--public-text", default=DEFAULT_PUBLIC_TEXT, help="text of the public reply")
    loop = ap.add_mutually_exclusive_group()
    loop.add_argument("--once", action="store_true", help="single pass (default)")
    loop.add_argument("--watch", action="store_true", help="poll forever (Ctrl+C to stop)")
    ap.add_argument("--interval", type=int, default=60, help="seconds between polls with --watch")
    ap.add_argument("--state", default=str(STATE_FILE), help="state file (default data/database/dm_bot.json)")
    args = ap.parse_args(argv)

    kwargs = dict(send=args.send, public_reply=args.public_reply, media_ids=args.media, template=args.message,
                  public_text=args.public_text, state_path=Path(args.state))
    try:
        while True:
            summary = run_once(**kwargs)
            print(json.dumps(summary, indent=1, ensure_ascii=False))
            if not args.watch:
                break
            sys.stdout.flush()
            time.sleep(max(5, args.interval))
    except KeyboardInterrupt:
        print("stopped", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
