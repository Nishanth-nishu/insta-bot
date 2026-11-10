"""Instagram Reels publishing through the official Instagram Graph API.

No unofficial bots. Requirements the user must set up once (free):
  1. An Instagram Professional (Creator or Business) account linked to a
     Facebook Page.
  2. A Meta developer app with the Instagram Graph API product and a long-lived
     user access token with instagram_basic, instagram_content_publish,
     pages_show_list, pages_read_engagement.
  3. The numeric Instagram user id (IG_USER_ID).
Both values go in .env (never in the repo): IG_ACCESS_TOKEN, IG_USER_ID.

Instagram fetches the video from a PUBLIC https URL. REELFORGE hosts the
approved MP4 on the project's public GitHub repository, on an orphan `media`
branch, and uses the raw.githubusercontent.com URL (zero cost). Set
PUBLIC_MEDIA_BASE_URL to use any other host instead.

Usage (always dry-run unless --confirm):
    python -m services.publishing.instagram --package output/approved/<date>/<id> [--confirm]

Flow: host media -> create REELS container -> poll status -> publish -> record permalink in package.json.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import httpx

from services.common import paths

API_VERSION = "v21.0"


def graph_base() -> str:
    """Graph host. 'facebook' (default) is the Facebook-login flavour (needs a linked Page);
    'instagram' is the Instagram-login flavour (graph.instagram.com, no Page needed)."""
    flavour = (os.environ.get("IG_API_HOST") or "facebook").strip().lower()
    host = "graph.instagram.com" if flavour.startswith("instagram") else "graph.facebook.com"
    return f"https://{host}/{API_VERSION}"


GRAPH = graph_base()
REPO = os.environ.get("MEDIA_GITHUB_REPO", "Nishanth-nishu/insta-bot")
MEDIA_BRANCH = os.environ.get("MEDIA_BRANCH", "media")


def _env(name: str) -> str | None:
    # Load .env lazily without a dependency.
    env_file = paths.ROOT / ".env"
    if name not in os.environ and env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    return os.environ.get(name)


@dataclass
class PublishResult:
    media_id: str
    permalink: str
    video_url: str


def host_on_github(video: Path, cover: Path | None, reel_id: str, dry_run: bool) -> tuple[str, str | None]:
    """Commit the MP4 (and cover) to the orphan media branch of the public repo
    and return raw URLs. Uses a temporary worktree so the main checkout is untouched."""
    base = os.environ.get("PUBLIC_MEDIA_BASE_URL")
    if base:
        return f"{base.rstrip('/')}/{reel_id}.mp4", (f"{base.rstrip('/')}/{reel_id}.jpg" if cover else None)
    video_url = f"https://raw.githubusercontent.com/{REPO}/{MEDIA_BRANCH}/{reel_id}.mp4"
    cover_url = f"https://raw.githubusercontent.com/{REPO}/{MEDIA_BRANCH}/{reel_id}.jpg" if cover else None
    if dry_run:
        return video_url, cover_url
    wt = paths.ROOT / ".media-worktree"
    run = lambda *cmd, **kw: subprocess.run(cmd, cwd=kw.get("cwd", paths.ROOT), capture_output=True, text=True)
    # Ensure branch exists remotely or create orphan.
    has_remote = run("git", "ls-remote", "--heads", "origin", MEDIA_BRANCH).stdout.strip() != ""
    if wt.exists():
        run("git", "worktree", "remove", "--force", str(wt))
    if has_remote:
        run("git", "fetch", "origin", MEDIA_BRANCH)
        r = run("git", "worktree", "add", str(wt), f"origin/{MEDIA_BRANCH}", "--detach")
    else:
        r = run("git", "worktree", "add", "--detach", str(wt))
        run("git", "checkout", "--orphan", MEDIA_BRANCH, cwd=wt)
        run("git", "rm", "-rf", "-q", ".", cwd=wt)
    if r.returncode != 0:
        raise RuntimeError(r.stderr)
    (wt / f"{reel_id}.mp4").write_bytes(video.read_bytes())
    if cover:
        _write_jpeg(cover, wt / f"{reel_id}.jpg")
    (wt / "README.md").write_text("Media hosting branch for Instagram publishing. Generated videos only.\n", encoding="utf-8")
    run("git", "add", "-A", cwd=wt)
    run("git", "-c", "user.name=Nishanth R", "-c", "user.email=nishanth0962333@gmail.com", "commit", "-q", "-m", f"media: {reel_id}", cwd=wt)
    push = run("git", "push", "origin", f"HEAD:{MEDIA_BRANCH}", cwd=wt)
    if push.returncode != 0:
        raise RuntimeError(push.stderr)
    run("git", "worktree", "remove", "--force", str(wt))
    # Wait until the raw URL answers 200 (CDN propagation).
    with httpx.Client(follow_redirects=True, timeout=30) as c:
        for _ in range(20):
            if c.head(video_url).status_code == 200:
                break
            time.sleep(6)
    return video_url, cover_url


def _write_jpeg(png: Path, out: Path) -> None:
    subprocess.run([paths.FFMPEG, "-y", "-v", "error", "-i", str(png), "-q:v", "3", str(out)], check=True)


def create_container(ig_user: str, token: str, video_url: str, caption: str, cover_url: str | None) -> str:
    data = {"media_type": "REELS", "video_url": video_url, "caption": caption, "share_to_feed": "true", "access_token": token}
    if cover_url:
        data["cover_url"] = cover_url
    r = httpx.post(f"{graph_base()}/{ig_user}/media", data=data, timeout=60)
    body = r.json()
    if r.status_code != 200 or "id" not in body:
        raise RuntimeError(f"container creation failed: {body}")
    return body["id"]


def wait_container(creation_id: str, token: str, timeout_s: int = 600) -> None:
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        r = httpx.get(f"{graph_base()}/{creation_id}", params={"fields": "status_code,status", "access_token": token}, timeout=30).json()
        code = r.get("status_code")
        if code == "FINISHED":
            return
        if code in ("ERROR", "EXPIRED"):
            raise RuntimeError(f"container {code}: {r}")
        time.sleep(8)
    raise TimeoutError("container did not finish processing")


def publish_container(ig_user: str, token: str, creation_id: str) -> PublishResult:
    r = httpx.post(f"{graph_base()}/{ig_user}/media_publish", data={"creation_id": creation_id, "access_token": token}, timeout=60).json()
    if "id" not in r:
        raise RuntimeError(f"publish failed: {r}")
    media_id = r["id"]
    info = httpx.get(f"{graph_base()}/{media_id}", params={"fields": "permalink", "access_token": token}, timeout=30).json()
    return PublishResult(media_id=media_id, permalink=info.get("permalink", ""), video_url="")


def publish_package(package_dir: Path, confirm: bool, publish_at: "datetime | None" = None) -> dict:
    """Host, create the container, then publish. With `publish_at` (an aware
    datetime), the container is prepared early and the final publish call
    waits until that moment, so the post lands on the minute."""
    meta = json.loads((package_dir / "package.json").read_text(encoding="utf-8"))
    video = package_dir / meta["video"]
    cover = package_dir / meta["cover"] if meta.get("cover") else None
    caption = (package_dir / "caption.txt").read_text(encoding="utf-8").strip()
    qa = meta.get("qa", "")
    if not str(qa).startswith("PASS"):
        raise RuntimeError(f"refusing to publish: QA is '{qa}'")
    token = _env("IG_ACCESS_TOKEN")
    ig_user = _env("IG_USER_ID")
    dry = not confirm
    plan = {"video": str(video), "bytes": video.stat().st_size, "caption_chars": len(caption), "cover": str(cover) if cover else None, "credentials_present": bool(token and ig_user), "mode": "DRY RUN" if dry else "LIVE"}
    if dry:
        video_url, cover_url = host_on_github(video, cover, meta["id"], dry_run=True)
        plan.update({"would_host_at": video_url, "would_cover_at": cover_url})
        return plan
    if not token or not ig_user:
        raise RuntimeError("IG_ACCESS_TOKEN and IG_USER_ID must be set in .env before a live publish")
    video_url, cover_url = host_on_github(video, cover, meta["id"], dry_run=False)
    creation = create_container(ig_user, token, video_url, caption, cover_url)
    wait_container(creation, token)
    if publish_at is not None:
        from datetime import datetime, timezone

        delay = (publish_at - datetime.now(timezone.utc)).total_seconds()
        if delay > 0:
            time.sleep(min(delay, 3600))
    result = publish_container(ig_user, token, creation)
    meta.update({"status": "published", "post_url": result.permalink, "media_id": result.media_id, "video_url": video_url, "published_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
    (package_dir / "package.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
    plan.update({"hosted_at": video_url, "media_id": result.media_id, "permalink": result.permalink})
    return plan


def check_credentials() -> dict:
    """Read-only: confirm the token can see the Instagram account. Never prints the token."""
    token = _env("IG_ACCESS_TOKEN")
    ig_user = _env("IG_USER_ID")
    if not token:
        raise RuntimeError("IG_ACCESS_TOKEN is empty in .env")
    base = graph_base()
    if "graph.instagram.com" in base:
        me = httpx.get(f"{base}/me", params={"fields": "user_id,username,account_type", "access_token": token}, timeout=30).json()
        found_id = str(me.get("user_id") or me.get("id") or "")
    else:
        if not ig_user:
            raise RuntimeError("IG_USER_ID is empty in .env (Facebook-login flavour needs it)")
        me = httpx.get(f"{base}/{ig_user}", params={"fields": "id,username", "access_token": token}, timeout=30).json()
        found_id = str(me.get("id") or "")
    if "error" in me:
        raise RuntimeError(f"token check failed: {me['error'].get('message')}")
    perms = httpx.get(f"{base}/me/permissions" if "facebook" in base else f"{base}/me", params={"access_token": token}, timeout=30).json()
    granted = [p["permission"] for p in perms.get("data", []) if p.get("status") == "granted"] if "facebook" in base else []
    return {"host": base, "username": me.get("username"), "account_type": me.get("account_type"), "ig_user_id": found_id,
            "ig_user_id_matches_env": (found_id == str(ig_user)) if ig_user else None, "granted": granted}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--package", help="output/approved/<date>/<id>")
    ap.add_argument("--confirm", action="store_true", help="actually host and publish (default is a dry run)")
    ap.add_argument("--check", action="store_true", help="read-only: verify IG_ACCESS_TOKEN against the API")
    ap.add_argument("--publish-at", default=None, help="HH:MM in IST today: prepare now, publish on that minute")
    args = ap.parse_args(argv)
    if args.check:
        print(json.dumps(check_credentials(), indent=1))
        return 0
    if not args.package:
        ap.error("--package is required unless --check is given")
    publish_at = None
    if args.publish_at:
        from datetime import datetime
        from zoneinfo import ZoneInfo

        now = datetime.now(ZoneInfo("Asia/Kolkata"))
        h, m = (int(x) for x in args.publish_at.split(":"))
        publish_at = now.replace(hour=h, minute=m, second=0, microsecond=0)
    result = publish_package(Path(args.package), args.confirm, publish_at=publish_at)
    print(json.dumps(result, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
