"""Two reels a day, unattended: produce ahead of time, publish on the minute, answer comments.

    python -m services.scheduler.daily produce  --slot afternoon|night [--date YYYY-MM-DD] [--topic data/topics/x.json] [--profile standard] [--dry-run]
    python -m services.scheduler.daily publish  --slot afternoon|night [--date YYYY-MM-DD] [--now] [--dry-run]
    python -m services.scheduler.daily dm
    python -m services.scheduler.daily status
    python -m services.scheduler.daily install-tasks   (Windows Task Scheduler, user level)
    python -m services.scheduler.daily remove-tasks

Slots (IST): afternoon publishes 15:30, produced from 13:00; night publishes
20:40, produced from 18:10. Override with PUBLISH_SLOTS="afternoon=15:30,night=20:40"
in .env. AUTOPUBLISH=true in .env is required for a live publish; without it
the publish step is a dry run. Every step logs to output/logs/ and the queue
lives in data/database/schedule.json. The host character and voice are the
house defaults (VRM avatar, am_fenrir or Sarvam per VOICE_PROVIDER).
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from services.common import paths
from services.common.env import load_dotenv
from services.common.schema import Storyboard

IST = ZoneInfo("Asia/Kolkata")
QUEUE = paths.DATA / "database" / "schedule.json"
LOGS = paths.OUTPUT / "logs"
DEFAULT_SLOTS = {"afternoon": {"publish": "15:30", "produce": "13:00", "prefer": ["news", "tool", "breakthrough"]}, "night": {"publish": "20:40", "produce": "18:10", "prefer": ["research", "explainer", "educational"]}}
log = logging.getLogger("reelforge.daily")


# ---------------------------------------------------------------- helpers
ANALYTICS_AT = os.environ.get("ANALYTICS_AT", "12:30")   # IST, before the first produce slot
NICHE_AT = os.environ.get("NICHE_AT", "12:00")           # IST, Mondays


def slots() -> dict:
    load_dotenv()
    out = json.loads(json.dumps(DEFAULT_SLOTS))
    for part in (os.environ.get("PUBLISH_SLOTS") or "").split(","):
        if "=" in part:
            name, hhmm = part.split("=", 1)
            out.setdefault(name.strip(), {"produce": "00:00", "prefer": []})["publish"] = hhmm.strip()
            h, m = (int(x) for x in hhmm.strip().split(":"))
            prod = (datetime(2000, 1, 1, h, m) - timedelta(minutes=150)).strftime("%H:%M")
            out[name.strip()]["produce"] = prod
    return out


def slot_datetime(slot: str, d: date) -> datetime:
    h, m = (int(x) for x in slots()[slot]["publish"].split(":"))
    return datetime(d.year, d.month, d.day, h, m, tzinfo=IST)


def load_queue() -> list[dict]:
    if QUEUE.exists():
        return json.loads(QUEUE.read_text(encoding="utf-8"))
    return []


def save_queue(entries: list[dict]) -> None:
    QUEUE.parent.mkdir(parents=True, exist_ok=True)
    QUEUE.write_text(json.dumps(entries, indent=1), encoding="utf-8")


def upsert(entry: dict) -> None:
    q = [e for e in load_queue() if not (e["date"] == entry["date"] and e["slot"] == entry["slot"])]
    q.append(entry)
    save_queue(sorted(q, key=lambda e: (e["date"], e["slot"])))


# The reel queue: every produced reel is an entry with status ready; a slot posts the reel
# made for it, otherwise the OLDEST ready reel (one missed day never means a lost reel).
# A failed publish keeps the entry ready with attempts/last_error, so the next slot retries.
BACKLOG_MAX = int(os.environ.get("PRODUCE_BACKLOG_MAX") or 4)   # ready reels kept at most; beyond it produce skips


def ready_entries(queue: list[dict] | None = None) -> list[dict]:
    q = queue if queue is not None else load_queue()
    order = {name: i for i, name in enumerate(slots())}
    return sorted((e for e in q if e.get("status") == "ready"), key=lambda e: (e["date"], order.get(e["slot"], 9)))


def next_ready(slot: str, post_date: date, queue: list[dict] | None = None) -> dict | None:
    """Strict FIFO: the oldest ready reel in the queue, whatever day it was made for. A reel
    that missed its day goes out at the very next slot (re-dated), and today's reel waits
    one slot; the backlog cap in produce keeps the drift to a day at most."""
    q = queue if queue is not None else load_queue()
    own = next((e for e in q if e["date"] == post_date.isoformat() and e["slot"] == slot), None)
    if own and own.get("status") == "published":
        return own
    ready = ready_entries(q)
    return ready[0] if ready else own


def backlog_full(queue: list[dict] | None = None) -> bool:
    return len(ready_entries(queue)) >= BACKLOG_MAX


def run(cmd: list[str], cwd: Path | None = None, timeout: int = 3600) -> subprocess.CompletedProcess:
    log.info("$ %s", " ".join(str(c) for c in cmd))
    env = dict(os.environ, PYTHONUTF8="1")
    return subprocess.run(cmd, cwd=str(cwd or paths.ROOT), capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout, env=env)


def python() -> str:
    return sys.executable


# ---------------------------------------------------------------- topic choice
def pick_topic(slot: str, days_window: int = 30) -> dict:
    """Highest-scoring unused candidate, preferring the slot's categories and
    skipping names used in the last `days_window` days."""
    prefer = slots()[slot].get("prefer", [])
    cutoff = (datetime.now(IST) - timedelta(days=days_window)).isoformat()
    used_names: set[str] = set()
    candidates: list[dict] = []
    for f in sorted(paths.TOPICS.glob("*.json")):
        t = json.loads(f.read_text(encoding="utf-8"))
        t["_file"] = str(f)
        if t.get("used_at") and t["used_at"] >= cutoff:
            used_names.add(t.get("name", "").lower())
        if t.get("status", "candidate") == "candidate" and not t.get("used_at"):
            candidates.append(t)
    candidates = [t for t in candidates if t.get("name", "").lower() not in used_names]
    if not candidates:
        raise RuntimeError("no unused topic candidates; run research first")
    # feedback loop: categories that performed on this account get a weight (1.0 until enough reels are in)
    from services.analytics.learn import category_weight, load_weights

    weights = load_weights()
    candidates.sort(key=lambda t: (t.get("category") in prefer, t.get("score_total", 0) * category_weight(t.get("category"), weights)), reverse=True)
    if weights.get("enough"):
        log.info("topic weights in use: %s", weights.get("category"))
    return candidates[0]


def mark_used(topic: dict, reel_id: str) -> None:
    f = Path(topic["_file"])
    t = json.loads(f.read_text(encoding="utf-8"))
    t["status"] = "used"
    t["used_at"] = datetime.now(IST).isoformat()
    t["reel_id"] = reel_id
    f.write_text(json.dumps(t, indent=1, ensure_ascii=False), encoding="utf-8")


# ---------------------------------------------------------------- produce
def render_spec(spec_path: Path, reel_id: str, profile: str) -> tuple[Path, Path | None, Path]:
    """Render the timed spec at 1080p plus the cover still, run technical QA. Returns (video, cover, qa report).
    Raises on a render failure or a failed QA."""
    from services.quality.technical import run_technical

    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    video = paths.DRAFTS / f"{reel_id}.{profile}.mp4"
    r = run([python(), "-m", "services.rendering.local_render", "--spec", str(spec_path), "--profile", profile, "--concurrency", "1", "--out", str(video)], timeout=5400)
    if r.returncode != 0 or not video.exists():
        raise RuntimeError("render failed: " + (r.stdout[-600:] + r.stderr[-600:]))
    cover: Path | None = paths.COVERS / f"{reel_id}.png"
    hook = (spec.get("scenes") or [{}])[0]
    cover_props = json.dumps({"title": hook.get("title") or spec.get("title"), "kicker": (hook.get("data") or {}).get("kicker", ""), "styleId": spec.get("style"), "highlight": hook.get("highlight") or []})
    npx = "npx.cmd" if os.name == "nt" else "npx"
    rc = run([npx, "remotion", "still", "src/index.ts", "Cover", str(cover), f"--props={cover_props}", "--log=error"], cwd=paths.VIDEO_ENGINE, timeout=900)
    if rc.returncode != 0:
        log.warning("cover render failed, continuing without a cover: %s", rc.stdout[-300:])
        cover = None
    prof = {"draft": (720, 1280)}.get(profile, (1080, 1920))
    rep = run_technical(video, spec, prof[0], prof[1])
    qa_path = paths.REPORTS / f"{reel_id}.{profile}.technical.json"
    qa_path.write_text(json.dumps({"passed": rep.passed, "checks": [c.__dict__ for c in rep.checks]}, indent=1, default=str), encoding="utf-8")
    if not rep.passed:
        raise RuntimeError(f"QA failed: {[c.name for c in rep.checks if not c.passed]}")
    return video, cover, qa_path


def redate_entry(entry: dict, post_date: date, profile: str = "standard", render: bool = True) -> dict:
    """A reel produced for an earlier day is posted later: move the on-screen kicker date, the
    caption date line and the package metadata to the real posting date, re-render when asked.
    If the re-render fails the text is still re-dated and the old video is posted (a day-old
    kicker beats a lost reel); the entry records what happened."""
    import shutil

    from services.publishing.seo import caption_date, kicker_date

    old = date.fromisoformat(entry["date"])
    if old == post_date:
        return entry
    reel_id = entry["reel_id"]
    pkg = Path(entry["package"])
    swaps = [(kicker_date(old), kicker_date(post_date)), (caption_date(old), caption_date(post_date))]

    def swap(text: str) -> str:
        for a, b in swaps:
            text = text.replace(a, b)
        return text

    for name in (f"{reel_id}.timed.json", f"{reel_id}.json"):
        f = paths.STORYBOARDS / name
        if f.exists():
            f.write_text(swap(f.read_text(encoding="utf-8")), encoding="utf-8")
    for name in ("caption.txt", "checklist.md"):
        f = pkg / name
        if f.exists():
            f.write_text(swap(f.read_text(encoding="utf-8")), encoding="utf-8")
    meta_file = pkg / "package.json"
    meta = json.loads(meta_file.read_text(encoding="utf-8")) if meta_file.exists() else {}
    meta["produced_for"] = entry["date"]
    meta["scheduled_for"] = slot_datetime(entry["slot"], post_date).isoformat() if entry.get("slot") in slots() else post_date.isoformat()
    entry["redated_to"] = post_date.isoformat()
    spec_path = paths.STORYBOARDS / f"{reel_id}.timed.json"
    if render and spec_path.exists():
        try:
            video, cover, qa = render_spec(spec_path, reel_id, profile)
            shutil.copy(video, pkg / meta.get("video", f"{reel_id}.mp4"))
            if cover:
                shutil.copy(cover, pkg / "cover.png")
            entry["rerendered"] = True
            log.info("re-dated and re-rendered %s for %s", reel_id, post_date)
        except Exception as exc:
            entry["rerendered"] = False
            entry["redate_error"] = str(exc)[-300:]
            log.warning("re-render failed, posting with the old kicker: %s", exc)
    meta_file.write_text(json.dumps(meta, indent=1), encoding="utf-8")
    upsert(entry)
    return entry


def produce(slot: str, post_date: date, topic_file: str | None, profile: str, dry_run: bool) -> dict:
    from services.publishing.package import build_package
    from services.story.generate import generate_storyboard, reel_id_for
    from services.tts.synthesize import synthesize_storyboard

    paths.ensure_dirs()
    LOGS.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    if backlog_full() and not topic_file:
        ready = ready_entries()
        log.warning("backlog full (%d ready reels, max %d): not producing; next to post is %s", len(ready), BACKLOG_MAX, ready[0]["reel_id"])
        return ready[0]
    # 0. learn from what is posted so far (local, cheap; the analytics task fetched the insights)
    try:
        from services.analytics.learn import refresh

        log.info("learning: %s", json.dumps(refresh()))
    except Exception as exc:  # never block production on analytics
        log.warning("learning step skipped: %s", exc)
    # 1. research (best effort: an old queue of candidates still works offline)
    r = run([python(), "-m", "services.research.discover", "--top", "25", "--save"], timeout=900)
    if r.returncode != 0:
        log.warning("research step failed, using existing candidates: %s", r.stdout[-300:] + r.stderr[-300:])
    # 2. topic
    topic = json.loads(Path(topic_file).read_text(encoding="utf-8")) | {"_file": topic_file} if topic_file else pick_topic(slot)
    reel_id = reel_id_for(topic, post_date, slot)
    log.info("topic %r -> reel %s", topic.get("name"), reel_id)
    # 3. script + storyboard
    sb = generate_storyboard(topic, post_date, reel_id)
    sb_path = paths.STORYBOARDS / f"{reel_id}.json"
    sb_path.write_text(sb.model_dump_json(indent=2, exclude_none=True), encoding="utf-8")
    # 4. voiceover + timing
    spec = synthesize_storyboard(sb)
    spec_path = paths.STORYBOARDS / f"{reel_id}.timed.json"
    # 5+6. render + cover + QA (a failed QA raises: nothing is queued)
    video, cover, qa_path = render_spec(spec_path, reel_id, profile)
    # 7. package + queue
    pkg = build_package(spec_path, video, cover, qa_path)
    meta = json.loads((pkg / "package.json").read_text(encoding="utf-8"))
    meta.update({"slot": slot, "scheduled_for": slot_datetime(slot, post_date).isoformat(), "topic": topic.get("name"), "status": "ready"})
    (pkg / "package.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
    mark_used(topic, reel_id)
    entry = {"date": post_date.isoformat(), "slot": slot, "reel_id": reel_id, "package": str(pkg), "scheduled_for": meta["scheduled_for"], "status": "ready", "produced_in_s": round(time.time() - t0), "dry_run": dry_run}
    upsert(entry)
    log.info("ready: %s (%.0f s)", pkg, time.time() - t0)
    return entry


# ---------------------------------------------------------------- publish
def publish(slot: str, post_date: date, now: bool, dry_run: bool) -> dict:
    from services.publishing import dm_bot
    from services.publishing.instagram import publish_package

    load_dotenv()
    entry = next_ready(slot, post_date)
    if not entry:
        raise RuntimeError(f"nothing queued for {post_date} {slot} and no ready reel in the queue; run produce first")
    if entry["status"] == "published":
        log.info("already published: %s", entry.get("post_url"))
        return entry
    if entry["date"] != post_date.isoformat():
        log.info("no reel for %s %s: posting the oldest ready reel %s (made for %s %s)", post_date, slot, entry["reel_id"], entry["date"], entry["slot"])
    live = (os.environ.get("AUTOPUBLISH") or "").strip().lower() == "true" and not dry_run
    if live and entry["date"] != post_date.isoformat():
        entry = redate_entry(entry, post_date)
    when = None if now else slot_datetime(slot, post_date)
    try:
        result = publish_package(Path(entry["package"]), confirm=live, publish_at=when if live else None)
    except Exception as exc:
        # keep the reel in the queue: the next slot retries it (and posts it even a day late)
        entry.update({"status": "ready", "attempts": int(entry.get("attempts") or 0) + 1, "last_error": str(exc)[-300:], "last_attempt_at": datetime.now(IST).isoformat()})
        upsert(entry)
        log.error("publish failed (attempt %d), reel stays queued: %s", entry["attempts"], exc)
        raise
    if live:
        entry.update({"status": "published", "post_url": result.get("permalink"), "media_id": result.get("media_id"), "published_at": datetime.now(IST).isoformat(), "posted_as": post_date.isoformat(), "posted_slot": slot})
        entry.pop("last_error", None)
        upsert(entry)
        try:
            summary = dm_bot.run_once(send=True, media_ids=[result["media_id"]])
            log.info("dm bot: %s", json.dumps(summary)[:300])
        except Exception as exc:  # the post is live; DM problems must not fail the task
            log.warning("dm bot failed: %s", exc)
    else:
        log.info("dry run (AUTOPUBLISH is not true or --dry-run): %s", json.dumps(result)[:400])
    return entry


# ---------------------------------------------------------------- tasks
TASK_PREFIX = "REELFORGE"


def task_cmd(args: str, name: str) -> str:
    """schtasks caps /TR at 261 chars, so the task runs scripts/reelforge-task.cmd <command> [slot]."""
    launcher = str(paths.ROOT / "scripts" / "reelforge-task.cmd")
    parts = args.replace("--slot", "").split()
    return f'"{launcher}" ' + " ".join(parts)


HARDEN_PS = ("$s = New-ScheduledTaskSettingsSet -StartWhenAvailable -WakeToRun -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries "
             "-ExecutionTimeLimit (New-TimeSpan -Hours 3) -MultipleInstances IgnoreNew; "
             "Get-ScheduledTask -TaskPath '\\REELFORGE\\' | ForEach-Object { Set-ScheduledTask -TaskPath '\\REELFORGE\\' -TaskName $_.TaskName -Settings $s | Out-Null }; "
             "Get-ScheduledTask -TaskPath '\\REELFORGE\\' | ForEach-Object { $t = $_.Settings; "
             "'{0,-18} missed={1} wake={2} onBattery={3} stopOnBattery={4} limit={5}' -f $_.TaskName, $t.StartWhenAvailable, $t.WakeToRun, (-not $t.DisallowStartIfOnBatteries), $t.StopIfGoingOnBatteries, $t.ExecutionTimeLimit }")


def harden_tasks() -> None:
    """schtasks creates tasks that skip a missed start, never wake the machine, start only on AC
    power and stop on battery: on a laptop that silently skips whole days (2026-11-12: asleep
    04:25-15:25 IST, no afternoon reel). Set the laptop-safe settings on every REELFORGE task."""
    r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", HARDEN_PS], capture_output=True, text=True)
    print((r.stdout or r.stderr).strip())
    if r.returncode != 0:
        log.warning("task hardening failed: %s", (r.stderr or r.stdout)[-300:])


def install_tasks() -> None:
    LOGS.mkdir(parents=True, exist_ok=True)
    jobs = []
    for slot, cfg in slots().items():
        jobs.append((f"produce-{slot}", cfg["produce"], f"produce --slot {slot}"))
        # start 4 minutes early; publish_package waits for the exact minute
        h, m = (int(x) for x in cfg["publish"].split(":"))
        start = (datetime(2000, 1, 1, h, m) - timedelta(minutes=4)).strftime("%H:%M")
        jobs.append((f"publish-{slot}", start, f"publish --slot {slot}"))
    for name, at, args in jobs:
        tn = f"{TASK_PREFIX}\\{name}"
        r = subprocess.run(["schtasks", "/Create", "/F", "/SC", "DAILY", "/ST", at, "/TN", tn, "/TR", task_cmd(args, name)], capture_output=True, text=True)
        print(tn, at, "->", (r.stdout or r.stderr).strip())
    tn = f"{TASK_PREFIX}\\dm-bot"
    r = subprocess.run(["schtasks", "/Create", "/F", "/SC", "MINUTE", "/MO", "5", "/TN", tn, "/TR", task_cmd("dm", "dm-bot")], capture_output=True, text=True)
    print(tn, "every 5 min ->", (r.stdout or r.stderr).strip())
    # feedback loop: insights + learning every day before the first produce; niche watch weekly
    tn = f"{TASK_PREFIX}\\analytics"
    r = subprocess.run(["schtasks", "/Create", "/F", "/SC", "DAILY", "/ST", ANALYTICS_AT, "/TN", tn, "/TR", task_cmd("analytics", "analytics")], capture_output=True, text=True)
    print(tn, ANALYTICS_AT, "->", (r.stdout or r.stderr).strip())
    tn = f"{TASK_PREFIX}\\niche-watch"
    r = subprocess.run(["schtasks", "/Create", "/F", "/SC", "WEEKLY", "/D", "MON", "/ST", NICHE_AT, "/TN", tn, "/TR", task_cmd("niche", "niche-watch")], capture_output=True, text=True)
    print(tn, "Mondays", NICHE_AT, "->", (r.stdout or r.stderr).strip())
    harden_tasks()


def remove_tasks() -> None:
    for name in [f"produce-{s}" for s in slots()] + [f"publish-{s}" for s in slots()] + ["dm-bot", "analytics", "niche-watch"]:
        r = subprocess.run(["schtasks", "/Delete", "/F", "/TN", f"{TASK_PREFIX}\\{name}"], capture_output=True, text=True)
        print(name, "->", (r.stdout or r.stderr).strip())


# ---------------------------------------------------------------- cli
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("command", choices=["produce", "publish", "dm", "analytics", "niche", "status", "install-tasks", "harden-tasks", "remove-tasks"])
    ap.add_argument("--slot", default="afternoon")
    ap.add_argument("--date", default=None)
    ap.add_argument("--topic", default=None)
    ap.add_argument("--profile", default="standard")
    ap.add_argument("--now", action="store_true", help="publish immediately instead of waiting for the slot minute")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    LOGS.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    load_dotenv()
    post_date = date.fromisoformat(args.date) if args.date else datetime.now(IST).date()
    if args.command == "produce":
        print(json.dumps(produce(args.slot, post_date, args.topic, args.profile, args.dry_run), indent=1))
    elif args.command == "publish":
        print(json.dumps(publish(args.slot, post_date, args.now, args.dry_run), indent=1, default=str))
    elif args.command == "dm":
        from services.publishing import dm_bot

        print(json.dumps(dm_bot.run_once(send=(os.environ.get("AUTOPUBLISH") or "").lower() == "true"), indent=1)[:2000])
    elif args.command == "analytics":
        from services.analytics.insights import collect
        from services.analytics.learn import refresh

        print(json.dumps({"collect": collect(), "learn": refresh()}, indent=1, ensure_ascii=False))
    elif args.command == "niche":
        from services.analytics import niche

        print(json.dumps(niche.run(), indent=1, ensure_ascii=False))
    elif args.command == "status":
        for e in load_queue()[-14:]:
            extra = f"  attempts={e['attempts']} last_error={e.get('last_error', '')[:80]}" if e.get("attempts") else ""
            print(f"{e['date']} {e['slot']:<10} {e['status']:<10} {e.get('post_url') or e['package']}{extra}")
        ready = ready_entries()
        print(f"reel queue: {len(ready)} ready (max {BACKLOG_MAX}); next to post: {ready[0]['reel_id'] if ready else 'nothing'}")
        from services.publishing import dm_bot

        st = dm_bot.load_state(dm_bot.STATE_FILE)
        q = st.get("queue", {})
        ok = sum(1 for h in st.get("handled", {}).values() if h.get("ok"))
        print(f"dm: {ok} delivered, {len(q)} waiting in the queue")
        for v in q.values():
            print(f"  @{v.get('username')} attempts={v.get('attempts')} last={v.get('last_error')}")
    elif args.command == "install-tasks":
        install_tasks()
    elif args.command == "harden-tasks":
        harden_tasks()
    elif args.command == "remove-tasks":
        remove_tasks()
    return 0


if __name__ == "__main__":
    sys.exit(main())
