"""Weekly niche watch: a headless Claude Code session researches which short-form AI and
tech explainer videos are working right now and writes data/analytics/niche.md, a swipe
file of hook shapes, formats and trending topics that the script writer reads.

    python -m services.analytics.niche

Runs on the Claude subscription (same CLI as the producer). Tools are limited to web
search, web fetch, reading the house hook library and writing the one swipe file. If the
CLI is missing or not logged in the step is skipped with a warning; the daily pipeline
never depends on it.
"""
from __future__ import annotations

import logging
import subprocess
import time
from pathlib import Path

from services.common import paths
from services.llm.client import claude_executable
from services.analytics.learn import NICHE_FILE

log = logging.getLogger("reelforge.analytics")

PROMPT = """You are the research step of REELFORGE, an automated Instagram Reels channel: 35-55 second English explainers about AI news, tools and research, presented by an animated host, two posts a day, Indian tech audience, LETS GO comment gate for links.

Find what is working RIGHT NOW in this niche on Instagram Reels, YouTube Shorts and TikTok. Use WebSearch (6 to 10 searches: e.g. "AI news reels hooks this week", "viral AI explainer shorts {month}", "tech creator reel hook formulas", trending AI topics this week) and WebFetch on the most useful results. Read .claude/skills/generate-script/hooks.md first so you know the house hook library and can say what is new.

Then write {out_path} (overwrite it) with, in this order and under 450 words total:
1. A first line "Niche watch, {date}".
2. "## Hook shapes working now": 8 to 12 bullets. Each: the shape in a few words, then one example line REWRITTEN for an AI-news reel (never copy a creator's words), then the fear or promise it uses.
3. "## Trending this week": 5 to 8 AI/tech topics people are watching, one line each with why it is hot. No links.
4. "## Format notes": length, pacing, captions, on-screen text, what the first 3 seconds look like on the winners.
5. "## Avoid": 3 to 5 things that are tired or flagged.
Facts only from what you read; if you could not verify something, leave it out. No URLs in the file. Do not edit any other file. Print DONE when the file is written.
"""

# An Edit(path) rule covers every file-editing tool (Write included); a Write(path) rule is never matched.
TOOLS = ["WebSearch", "WebFetch", "Read", "Edit(data/analytics/niche.md)", "Edit(data\\analytics\\niche.md)"]


def run(out_path: Path = NICHE_FILE, timeout_s: int = 1200) -> dict:
    exe = claude_executable()
    if not exe:
        log.warning("niche watch skipped: claude CLI not found")
        return {"ok": False, "error": "claude CLI not found"}
    out_path.parent.mkdir(parents=True, exist_ok=True)
    today = time.strftime("%Y-%m-%d")
    prompt = PROMPT.format(out_path=out_path.relative_to(paths.ROOT).as_posix(), date=today, month=time.strftime("%B %Y"))
    cmd = [exe, "-p", "--output-format", "text", "--max-turns", "40", "--allowedTools", *TOOLS]
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
    return {"ok": ok, "seconds": round(time.time() - t0), "file": str(out_path), "tail": out[-200:].strip()}


if __name__ == "__main__":
    import json

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    print(json.dumps(run(), indent=1))
