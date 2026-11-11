"""Collect Instagram insights for every published reel into data/analytics/insights.json.

One snapshot per run and per reel (views, reach, likes, comments, saves, shares, total
interactions, average watch time, total watch time), stamped with the reel's age in hours,
so later runs can read a settled value (a reel keeps collecting views for days).

    python -m services.analytics.insights            # collect once
    python -m services.analytics.insights --show     # print the latest snapshot per reel

Official Graph API only: GET /{media_id}/insights and GET /{media_id}. Metrics the API
rejects for a media item are dropped and the call retried, so an API rename never
stops the collector. A blocked or failing call is reported in the summary, never fatal.
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from services.common import paths
from services.publishing.instagram import _env, graph_base

log = logging.getLogger("reelforge.analytics")

ANALYTICS_DIR = paths.DATA / "analytics"
INSIGHTS_FILE = ANALYTICS_DIR / "insights.json"

# Reel metrics on Graph API v21+ ("plays" was retired in favour of "views").
METRICS = ["views", "reach", "likes", "comments", "saved", "shares", "total_interactions",
           "ig_reels_avg_watch_time", "ig_reels_video_view_total_time"]
MEDIA_FIELDS = "like_count,comments_count,timestamp,permalink"
MAX_AGE_DAYS = 45          # stop polling reels older than this
MAX_SNAPSHOTS = 120        # per reel

FetchFn = Callable[[str], dict]


def _scrub(text: str, token: str | None) -> str:
    return text.replace(token, "***") if token else text


def fetch_insights(media_id: str, token: str, base: str | None = None) -> dict:
    """Metric -> value for one reel; unsupported metrics are dropped and the call retried."""
    import httpx

    base = base or graph_base()
    metrics = list(METRICS)
    out: dict = {}
    for _ in range(len(METRICS)):
        r = httpx.get(f"{base}/{media_id}/insights", params={"metric": ",".join(metrics), "access_token": token}, timeout=30)
        body = r.json()
        if r.status_code == 200:
            for item in body.get("data", []):
                vals = item.get("values") or []
                value = vals[0].get("value") if vals else (item.get("total_value") or {}).get("value")
                out[item.get("name")] = value
            break
        msg = str((body.get("error") or {}).get("message", body))
        bad = [m for m in metrics if re.search(rf"\b{re.escape(m)}\b", msg)]
        if not bad or len(metrics) <= 1:
            raise RuntimeError(_scrub(msg, token))
        metrics = [m for m in metrics if m not in bad]
    media = httpx.get(f"{base}/{media_id}", params={"fields": MEDIA_FIELDS, "access_token": token}, timeout=30).json()
    if "error" not in media:
        out.setdefault("likes", media.get("like_count"))
        out.setdefault("comments", media.get("comments_count"))
        out["permalink"] = media.get("permalink")
        out["timestamp"] = media.get("timestamp")
    return out


def published_reels(approved_root: Path | None = None) -> list[dict]:
    """Every approved package with status published and a media id, oldest first."""
    root = approved_root or paths.APPROVED
    reels = []
    for meta_file in sorted(root.glob("*/*/package.json")):
        try:
            meta = json.loads(meta_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if meta.get("status") != "published" or not meta.get("media_id"):
            continue
        reels.append({"media_id": str(meta["media_id"]), "reel_id": meta.get("id") or meta_file.parent.name, "package": str(meta_file.parent),
                      "published_at": meta.get("published_at") or meta.get("scheduled_for"), "slot": meta.get("slot"),
                      "duration_s": meta.get("duration_s"), "title": meta.get("title"), "topic": meta.get("topic"), "post_url": meta.get("post_url")})
    return reels


def load_state(path: Path = INSIGHTS_FILE) -> dict:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            log.warning("insights state unreadable, starting fresh: %s", path)
    return {"reels": {}}


def save_state(state: dict, path: Path = INSIGHTS_FILE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=1, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def _age_hours(published_at: str | None, now: float) -> float | None:
    if not published_at:
        return None
    try:
        dt = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return round((now - dt.timestamp()) / 3600, 2)


def collect(*, fetch: FetchFn | None = None, approved_root: Path | None = None, state_path: Path = INSIGHTS_FILE,
            token: str | None = None, now: float | None = None) -> dict:
    """One snapshot per published reel younger than MAX_AGE_DAYS. Returns a summary."""
    now = now or time.time()
    token = token if token is not None else (_env("IG_ACCESS_TOKEN") or "")
    if fetch is None:
        if not token:
            raise RuntimeError("IG_ACCESS_TOKEN is empty in .env")
        fetch = lambda mid: fetch_insights(mid, token)  # noqa: E731
    state = load_state(state_path)
    summary = {"reels": 0, "collected": 0, "skipped_old": 0, "errors": []}
    for reel in published_reels(approved_root):
        summary["reels"] += 1
        rec = state["reels"].setdefault(reel["media_id"], {k: reel[k] for k in ("reel_id", "published_at", "slot", "duration_s", "title", "topic", "post_url")} | {"snapshots": []})
        rec.update({k: reel[k] for k in ("published_at", "slot", "duration_s", "title", "topic", "post_url") if reel.get(k)})
        age = _age_hours(reel.get("published_at"), now)
        if age is not None and age > MAX_AGE_DAYS * 24:
            summary["skipped_old"] += 1
            continue
        try:
            metrics = fetch(reel["media_id"])
        except Exception as exc:  # blocked API, network, token: keep going
            summary["errors"].append({"media_id": reel["media_id"], "reel_id": reel["reel_id"], "error": _scrub(str(exc), token)[:200]})
            continue
        snap = {"at": datetime.fromtimestamp(now, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "age_h": age} | {k: v for k, v in metrics.items() if k in METRICS}
        snaps = rec["snapshots"]
        if snaps and age is not None and snaps[-1].get("age_h") is not None and age - snaps[-1]["age_h"] < 1.0:
            snaps[-1] = snap  # same hour: replace rather than pile up
        else:
            snaps.append(snap)
        del snaps[:-MAX_SNAPSHOTS]
        if metrics.get("permalink"):
            rec["post_url"] = metrics["permalink"]
        summary["collected"] += 1
    save_state(state, state_path)
    return summary


def latest(state: dict) -> list[dict]:
    rows = []
    for mid, rec in state.get("reels", {}).items():
        snap = rec["snapshots"][-1] if rec.get("snapshots") else {}
        rows.append({"media_id": mid, "reel_id": rec.get("reel_id"), "title": rec.get("title"), "age_h": snap.get("age_h"),
                     "views": snap.get("views"), "reach": snap.get("reach"), "avg_watch_s": snap.get("ig_reels_avg_watch_time"),
                     "likes": snap.get("likes"), "comments": snap.get("comments"), "saved": snap.get("saved"), "shares": snap.get("shares")})
    return rows


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--show", action="store_true", help="print the latest snapshot per reel instead of collecting")
    ap.add_argument("--state", default=str(INSIGHTS_FILE))
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    if args.show:
        for row in latest(load_state(Path(args.state))):
            print(json.dumps(row, ensure_ascii=False))
        return 0
    print(json.dumps(collect(state_path=Path(args.state)), indent=1, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
