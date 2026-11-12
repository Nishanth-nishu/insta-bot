"""Weekly niche watch, in two steps.

1. Evidence (no login, public data): yt-dlp lists the Shorts tab of each channel in
   data/analytics/niche_channels.txt (one @handle per line; a default list of AI-news
   creators is written on first run). The vendored collect.py normalises the counts and
   swipe.py ranks every video as a multiple of its OWN channel's median and names the hook
   formula: a 400k video on a 2M channel is a quiet Tuesday, a 400k video on a 13k-median
   channel is the thing to study. The outliers, with our hook score, go to
   data/analytics/niche_evidence.md.
2. Swipe file: a headless Claude Code session reads that evidence first, uses web search only
   to fill in this week's topics, and writes data/analytics/niche.md (hook shapes, trending
   topics, format notes, things to avoid) that the script writer reads.

    python -m services.analytics.niche              # both steps
    python -m services.analytics.niche --evidence   # step 1 only

Runs on the Claude subscription (same CLI as the producer). If yt-dlp or the CLI is missing
the matching step is skipped with a warning; the daily pipeline never depends on it.
"""
from __future__ import annotations

import json
import logging
import statistics
import subprocess
import sys
import time
from pathlib import Path

from services.common import paths
from services.llm.client import claude_executable
from services.analytics.learn import ANALYTICS_DIR, NICHE_FILE

log = logging.getLogger("reelforge.analytics")

CHANNELS_FILE = ANALYTICS_DIR / "niche_channels.txt"
EVIDENCE_FILE = ANALYTICS_DIR / "niche_evidence.md"
RAW_DIR = ANALYTICS_DIR / "niche_raw"
POSTS_FILE = ANALYTICS_DIR / "niche_posts.json"
TOOLS = paths.ROOT / "third_party" / "instagram-agent-skill"

# AI-news Shorts creators whose public tabs listed cleanly on 2026-11-12 (medians 2k to 36k
# views, within reach of a new account). Edit data/analytics/niche_channels.txt to change.
DEFAULT_CHANNELS = ["@mreflow", "@WesRoth", "@MattVidPro", "@TwoMinutePapers", "@DavidOndrej", "@bycloudAI",
                    "@matthew_berman", "@1littlecoder", "@SkillLeapAI", "@TechLinked", "@AIRevolutionX", "@futurepedia_io"]
PER_CHANNEL = 40        # recent Shorts per channel (the ranker needs at least 4)
MIN_MULTIPLE = 2.0      # an outlier beats its channel's median by this much
TOP_N = 25

PROMPT = """You are the research step of REELFORGE, an automated Instagram Reels channel: 35-55 second English explainers about AI news, tools and research, presented by an animated host, two posts a day, Indian tech audience, LETS GO comment gate for links.

Find what is working RIGHT NOW in this niche on Instagram Reels, YouTube Shorts and TikTok.
{evidence_step}Use WebSearch (4 to 8 searches: e.g. "AI news reels hooks this week", "viral AI explainer shorts {month}", "tech creator reel hook formulas", trending AI topics this week) and WebFetch on the most useful results. Read .claude/skills/generate-script/hooks.md so you know the house hook library and can say what is new.

Then write {out_path} (overwrite it) with, in this order and under 450 words total:
1. A first line "Niche watch, {date}".
2. "## Hook shapes working now": 8 to 12 bullets. Each: the shape in a few words, then one example line REWRITTEN for an AI-news reel (never copy a creator's words), then the fear or promise it uses. Mark the ones backed by the evidence file with the multiple, e.g. "(8x, @channel)".
3. "## Trending this week": 5 to 8 AI/tech topics people are watching, one line each with why it is hot. No links.
4. "## Format notes": length, pacing, captions, on-screen text, what the first 3 seconds look like on the winners.
5. "## Avoid": 3 to 5 things that are tired or flagged.
Facts only from what you read; if you could not verify something, leave it out. No URLs in the file. Do not edit any other file. Print DONE when the file is written.
"""
EVIDENCE_STEP = "FIRST read {evidence} : real public view counts for AI creators' Shorts, ranked by how far each video beat its own channel's median, with the hook formula and our hook score. Base the hook shapes on those outliers (the titles are the on-screen hooks). "

# An Edit(path) rule covers every file-editing tool (Write included); a Write(path) rule is never matched.
AGENT_TOOLS = ["WebSearch", "WebFetch", "Read", "Edit(data/analytics/niche.md)", "Edit(data\\analytics\\niche.md)"]


# ---------- step 1: evidence ----------

def channels(path: Path = CHANNELS_FILE) -> list[str]:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text("# One YouTube handle per line: AI-news creators whose Shorts to watch (public data, no login).\n"
                        "# Keep them within about 10x of your own audience size; a formula that works at 2M followers often works because it is at 2M.\n"
                        + "\n".join(DEFAULT_CHANNELS) + "\n", encoding="utf-8")
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            out.append(line if line.startswith("@") else "@" + line)
    return out


def ytdlp_available() -> bool:
    try:
        import yt_dlp  # noqa: F401

        return True
    except ImportError:
        return False


def fetch_shorts(handle: str, per_channel: int = PER_CHANNEL, timeout_s: int = 180) -> dict | None:
    """Flat listing of a channel's public Shorts tab (titles, view counts, urls). None on failure."""
    url = f"https://www.youtube.com/{handle}/shorts"
    cmd = [sys.executable, "-m", "yt_dlp", "--flat-playlist", "--playlist-end", str(per_channel), "-J", url]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout_s)
    except subprocess.TimeoutExpired:
        log.warning("yt-dlp timed out for %s", handle)
        return None
    if r.returncode != 0 or not r.stdout.strip():
        log.warning("yt-dlp failed for %s: %s", handle, (r.stderr or "")[-200:].strip())
        return None
    try:
        data = json.loads(r.stdout)
    except json.JSONDecodeError:
        return None
    data["entries"] = [e for e in data.get("entries") or [] if e]
    for e in data["entries"]:
        e.setdefault("channel", handle)
        e.setdefault("uploader_id", handle)
    return data


def _tool(name: str, args: list[str], timeout_s: int = 120) -> subprocess.CompletedProcess:
    # -I: the vendored scripts run isolated from the raw data folder (imports never resolve there).
    # -I also drops PYTHONUTF8, and the scripts open files with the platform default, so force UTF-8 on the command line.
    return subprocess.run([sys.executable, "-I", "-X", "utf8", str(TOOLS / name), *args], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout_s, cwd=str(paths.ROOT))


def rank(raw_files: list[Path], posts_file: Path | None = None, min_multiple: float = MIN_MULTIPLE) -> dict:
    """collect.py normalises the raw listings, swipe.py ranks by multiple of each channel's median."""
    posts_file = posts_file or POSTS_FILE  # resolved at call time so tests can point it elsewhere
    posts_file.parent.mkdir(parents=True, exist_ok=True)
    r = _tool("collect.py", [*map(str, raw_files), "--platform", "shorts", "--quiet", "-o", str(posts_file)])
    if r.returncode != 0:
        raise RuntimeError(f"collect.py failed: {(r.stderr or r.stdout)[-300:]}")
    r = _tool("swipe.py", [str(posts_file), "--min", str(min_multiple), "--json"])
    if r.returncode != 0:
        raise RuntimeError(f"swipe.py failed: {(r.stderr or r.stdout)[-300:]}")
    out = json.loads(r.stdout)
    posts = json.loads(posts_file.read_text(encoding="utf-8"))
    out["posts"] = len(posts)
    out["channels"] = len({p.get("channel") for p in posts})
    return out


def evidence_markdown(ranked: dict, when: str | None = None) -> str:
    from services.story import polish

    when = when or time.strftime("%Y-%m-%d")
    outliers = ranked.get("outliers", [])[:TOP_N]
    lines = [f"# Niche evidence, {when}", "",
             f"{ranked.get('posts', 0)} recent Shorts across {ranked.get('channels', 0)} AI creators (public counts via yt-dlp). "
             f"Each video is scored as a multiple of its own channel's median; {len(ranked.get('outliers', []))} cleared {MIN_MULTIPLE}x. "
             "The title is the on-screen hook; the formula names its shape; the hook score is our five-check scorer.", ""]
    if not outliers:
        lines.append("No outliers this week: collect more per channel or lower the threshold.")
        return "\n".join(lines) + "\n"
    lines += ["| multiple | views | channel median | channel | hook (title) | formula | hook score |", "|---|---|---|---|---|---|---|"]
    for o in outliers:
        title = (o.get("title") or "").replace("|", "/").strip()
        try:
            score = polish.score_hook(title)["score"]
        except Exception:
            score = ""
        lines.append(f"| {o.get('multiple')}x | {o.get('views'):,} | {o.get('median'):,} | {o.get('channel')} | {title[:90]} | {o.get('formula')} | {score} |")
    counts: dict[str, int] = {}
    for o in ranked.get("outliers", []):
        counts[o.get("formula") or "Unclassified"] = counts.get(o.get("formula") or "Unclassified", 0) + 1
    lines += ["", "## Formulas among the outliers", ""]
    lines += [f"- {name}: {n}" for name, n in sorted(counts.items(), key=lambda kv: -kv[1])]
    scores = [polish.score_hook((o.get("title") or ""))["score"] for o in outliers]
    if scores:
        lines += ["", f"Median hook score of the outliers: {statistics.median(scores):.0f}/100 (our own reels are mostly under 40: the winners put a name, number or product in the first words)."]
    thin = ranked.get("skipped_thin_channels") or []
    if thin:
        lines += ["", "Skipped (under 4 videos listed): " + ", ".join(f"{c} ({n})" for c, n in thin)]
    return "\n".join(lines) + "\n"


def evidence(handles: list[str] | None = None, raw_dir: Path = RAW_DIR, out_file: Path = EVIDENCE_FILE, fetch=fetch_shorts) -> dict:
    """Step 1 end to end. Returns a summary; writes niche_evidence.md. Never raises for one bad channel."""
    if not ytdlp_available():
        log.warning("niche evidence skipped: yt-dlp is not installed (pip install yt-dlp)")
        return {"ok": False, "error": "yt-dlp not installed"}
    handles = handles or channels()
    raw_dir.mkdir(parents=True, exist_ok=True)
    files, failed = [], []
    for h in handles:
        data = fetch(h)
        if not data or not data.get("entries"):
            failed.append(h)
            continue
        f = raw_dir / f"{h.lstrip('@')}.json"
        f.write_text(json.dumps(data), encoding="utf-8")
        files.append(f)
    if not files:
        return {"ok": False, "error": "no channel listed", "failed": failed}
    ranked = rank(files)
    out_file.write_text(evidence_markdown(ranked), encoding="utf-8")
    return {"ok": True, "channels": ranked["channels"], "posts": ranked["posts"], "outliers": len(ranked.get("outliers", [])),
            "top": [(o["multiple"], o["channel"], o["title"][:60]) for o in ranked.get("outliers", [])[:3]], "failed": failed, "file": str(out_file)}


# ---------- step 2: the swipe file ----------

def swipe(out_path: Path = NICHE_FILE, evidence_path: Path = EVIDENCE_FILE, timeout_s: int = 1200) -> dict:
    exe = claude_executable()
    if not exe:
        log.warning("niche watch skipped: claude CLI not found")
        return {"ok": False, "error": "claude CLI not found"}
    out_path.parent.mkdir(parents=True, exist_ok=True)
    today = time.strftime("%Y-%m-%d")
    step = EVIDENCE_STEP.format(evidence=evidence_path.relative_to(paths.ROOT).as_posix()) if evidence_path.exists() else ""
    prompt = PROMPT.format(out_path=out_path.relative_to(paths.ROOT).as_posix(), date=today, month=time.strftime("%B %Y"), evidence_step=step)
    cmd = [exe, "-p", "--output-format", "text", "--max-turns", "40", "--allowedTools", *AGENT_TOOLS]
    t0 = time.time()
    try:
        r = subprocess.run(cmd, input=prompt, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout_s, cwd=str(paths.ROOT))
    except subprocess.TimeoutExpired:
        log.warning("niche watch timed out after %ss", timeout_s)
        return {"ok": False, "error": "timeout"}
    out = (r.stdout or "") + (r.stderr or "")
    logs = paths.OUTPUT / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    (logs / f"niche-watch-{today}.log").write_text(out, encoding="utf-8")
    ok = r.returncode == 0 and out_path.exists() and today in out_path.read_text(encoding="utf-8")[:200]
    if not ok:
        log.warning("niche watch did not produce a dated file: %s", out[-300:].strip())
    return {"ok": ok, "seconds": round(time.time() - t0), "file": str(out_path), "evidence_used": bool(step), "tail": out[-200:].strip()}


def run(out_path: Path = NICHE_FILE, timeout_s: int = 1200) -> dict:
    """Both steps; the evidence step's failure never blocks the swipe file."""
    try:
        ev = evidence()
    except Exception as exc:
        log.warning("niche evidence failed: %s", exc)
        ev = {"ok": False, "error": str(exc)[-200:]}
    sw = swipe(out_path=out_path, timeout_s=timeout_s)
    return {"evidence": ev, "swipe": sw, "ok": sw.get("ok", False)}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    if "--evidence" in sys.argv:
        print(json.dumps(evidence(), indent=1, ensure_ascii=False, default=str))
    else:
        print(json.dumps(run(), indent=1, ensure_ascii=False, default=str))
