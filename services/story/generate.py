"""Unattended script + storyboard generation for one reel.

    python -m services.story.generate data/topics/<slug>.json [--date 2026-11-11] [--slot afternoon] [--id <id>]

Asks the LLM (services/llm/client.py) for the scenes, title, keyword and
caption summary in one JSON answer, assembles the Storyboard with the house
settings (VRM host, voice, music, DM gate), builds the SEO caption and
hashtags, validates against the schema, the content rules and the caption
lint, and retries with the problems fed back. Writes data/storyboards/<id>.json.
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from datetime import date
from pathlib import Path

from services.common import paths
from services.common.rules import check_storyboard, words
from services.common.schema import Storyboard
from services.llm.client import LLM
from services.publishing.seo import build_caption, kicker_date, lint_caption, pick_hashtags
from services.research.score import slugify

log = logging.getLogger("reelforge.story")

EXAMPLE = paths.STORYBOARDS / "dsh-superagent-host.json"
DM_KEYWORD = "LETS GO"
CTA = "Comment LETS GO, the link lands in your DMs"
PAYOFF_LINE = "Want the link? Comment LETS GO and I'll send it to your DMs. And follow, so you're not the last to know."

SYSTEM = """You write 45-55 second Instagram Reel scripts for an Indian tech creator. An animated anime host says every line on camera. Output ONE JSON object and nothing else: no prose, no code fences.

HARD RULES
- English only, spoken and casual. Sentences under 14 words. Total narration 100 to 140 words across 5 to 7 scenes.
- Scene 1 is the hook: at most 16 words, two sentences, anxious and a bit funny, aimed at the viewer's job, money, status or "this just happened". Name the viewer ("coders", "developers", "everyone learning AI"). The host says it big on screen.
- Never put a link, URL, domain, "link in bio" or "source:" anywhere: not in narration, titles, lines, commands or the caption. The last scene asks viewers to comment LETS GO and ends with the exact narration: "{payoff}"
- Facts only from the brief. No invented numbers. If a number is not in the brief, do not use one.
- On-screen text is Latin script only. Every scene has `kicker` (uppercase, under 24 chars) and `title` (6 to 10 words). Scene 1's kicker MUST end with " / {kicker_date}" (the posting date).
- `highlight`: 1 or 2 words that appear in the title.

SCENE KINDS (use `data` exactly like this)
- "hook": data {"kicker"}
- "concept": lines: 3 or 4 bullet strings under 7 words; data {"kicker"}
- "workflow": data {"stages": [3 to 5 short labels], "goal": "short label"}
- "chart": data {"kicker", "type": "bar", "unit": "%" or "", "decimals": 0 or 1, "series": [{"label","value"}, {"label","value","hero": true}], "note": "short source note without a URL"} ONLY if the brief has two comparable numbers
- "terminal": data {"kicker", "windowTitle": "zsh", "command": "a short shell command with NO URL", "lines": [{"text","kind": "dim"|"out"|"ok"}]} ONLY for tools you install or run
- "payoff": data {"kicker": "THE TAKEAWAY"}, cta "{cta}", narration ends with the payoff line above

SEO
- `title`: under 60 chars, keyword first, no clickbait punctuation spam.
- `keyword`: the 2 to 4 word phrase people would search (e.g. "AI coding agent"). Use it once in scene 1's title or narration.
- `caption_summary`: two plain sentences for the caption, keyword once, no hashtags, no links.
- `niche_hashtags`: 3 specific tags for this topic (lowercase, no spaces).

OUTPUT SHAPE
{"title": "...", "keyword": "...", "caption_summary": "...", "niche_hashtags": ["#...","#...","#..."],
 "scenes": [{"id": "hook", "kind": "hook", "narration": "...", "title": "...", "highlight": ["..."], "data": {"kicker": "AI NEWS / {kicker_date}"}}, ...]}
"""


def load_example() -> str:
    sb = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    sb = {k: v for k, v in sb.items() if k in ("title", "scenes")}
    for sc in sb["scenes"]:
        sc.pop("transition", None)
        sc.pop("padAfterMs", None)
    return json.dumps(sb, ensure_ascii=False)


def user_prompt(topic: dict, post_date: date, problems: list[str] | None = None) -> str:
    brief = {
        "name": topic.get("name"),
        "category": topic.get("category"),
        "description": topic.get("description"),
        "verified_claims": topic.get("verified_claims") or [],
        "notes": topic.get("notes") or "",
        "source_published": topic.get("published_at"),
    }
    msg = f"POSTING DATE (IST): {post_date.isoformat()} -> kicker date text '{kicker_date(post_date)}'.\n\nTOPIC BRIEF:\n{json.dumps(brief, ensure_ascii=False, indent=1)}\n\nEXAMPLE OF THE JSON SHAPE (different topic, copy the structure not the words):\n{load_example()}\n\nWrite the JSON for this topic now."
    if problems:
        msg += "\n\nYour previous answer had these problems; fix every one and return the full JSON again:\n- " + "\n- ".join(problems)
    return msg


def assemble(answer: dict, topic: dict, post_date: date, reel_id: str) -> Storyboard:
    scenes = []
    for i, sc in enumerate(answer.get("scenes", [])):
        kind = sc.get("kind", "concept")
        scene = {
            "id": sc.get("id") or f"s{i + 1}",
            "kind": kind,
            "narration": sc.get("narration", "").strip(),
            "title": sc.get("title"),
            "highlight": sc.get("highlight") or [],
            "lines": sc.get("lines") or [],
            "data": sc.get("data") or {},
            "transition": "zoomIn" if kind == "workflow" else ("up" if i == len(answer.get("scenes", [])) - 2 else "current"),
            "padAfterMs": 200 if kind == "hook" else (900 if kind == "payoff" else 260),
        }
        if kind == "payoff":
            scene["cta"] = CTA
        scenes.append(scene)
    sources = [s["url"] for s in topic.get("sources", []) if s.get("url")]
    keyword = (answer.get("keyword") or topic.get("name") or "AI").strip()
    hashtags = pick_hashtags(answer.get("niche_hashtags") or [], topic.get("category", ""))
    hook_line = scenes[0]["narration"].split(". ")[0] if scenes else answer.get("title", "")
    caption = build_caption(hook_line, answer.get("caption_summary", ""), keyword, post_date, hashtags, keyword_phrase=answer.get("title"))
    return Storyboard.model_validate(
        {
            "id": reel_id,
            "title": answer.get("title") or topic.get("name"),
            "style": "cinematic",
            "voice": {"provider": "kokoro", "voice": "am_fenrir", "speed": 1.05},
            "presenter": {"enabled": True, "name": "host", "mood": "hype", "look": "vrm", "model": "models/host.vrm"},
            "music": {"file": "music/ambient_bed.wav", "volume": 0.2, "bpm": 84},
            "caption": caption,
            "hashtags": hashtags,
            "sources": sources,
            "dmKeyword": DM_KEYWORD,
            "dmLink": sources[0] if sources else None,
            "scenes": scenes,
        }
    )


def validate(sb: Storyboard, answer: dict, post_date: date) -> list[str]:
    problems = check_storyboard(sb)
    n = len(sb.scenes)
    if not 5 <= n <= 7:
        problems.append(f"{n} scenes; write 5 to 7")
    total = sum(len(words(s.narration)) for s in sb.scenes)
    if not 90 <= total <= 150:
        problems.append(f"narration is {total} words; aim for 100 to 140")
    if sb.scenes and len(words(sb.scenes[0].narration)) > 16:
        problems.append("hook over 16 words")
    if sb.scenes and sb.scenes[-1].kind != "payoff":
        problems.append("last scene must be kind payoff")
    if sb.scenes and kicker_date(post_date) not in str(sb.scenes[0].data.get("kicker", "")):
        problems.append(f"scene 1 kicker must end with ' / {kicker_date(post_date)}'")
    for s in sb.scenes:
        if s.title and s.highlight and not all(h.lower().strip(".,!?") in s.title.lower() for h in s.highlight):
            problems.append(f"scene {s.id}: highlight words must appear in the title")
        if s.kind == "concept" and not 3 <= len(s.lines) <= 4:
            problems.append(f"scene {s.id}: concept needs 3 or 4 lines")
        if s.kind == "workflow" and not 3 <= len(s.data.get("stages", [])) <= 5:
            problems.append(f"scene {s.id}: workflow needs 3 to 5 stages")
        if s.kind == "chart" and len(s.data.get("series", [])) < 2:
            problems.append(f"scene {s.id}: chart needs at least two series entries")
    problems += lint_caption(sb.caption, answer.get("keyword") or "")
    return problems


def generate_storyboard(topic: dict, post_date: date, reel_id: str, llm: LLM | None = None, attempts: int = 3) -> Storyboard:
    llm = llm or LLM()
    system = SYSTEM.replace("{payoff}", PAYOFF_LINE).replace("{cta}", CTA).replace("{kicker_date}", kicker_date(post_date))
    problems: list[str] = []
    last_err = ""
    for attempt in range(attempts):
        if attempt == attempts - 1 and llm.provider == "sarvam" and not llm.model:
            llm.model = "sarvam-105b"  # last try: the reasoning model
        answer = llm.complete_json(system, user_prompt(topic, post_date, problems), max_tokens=8000, temperature=0.6 if attempt == 0 else 0.4)
        try:
            sb = assemble(answer, topic, post_date, reel_id)
        except Exception as exc:  # schema errors come back as problems
            problems = [f"storyboard schema error: {str(exc)[:300]}"]
            last_err = problems[0]
            log.warning("attempt %d: %s", attempt + 1, last_err)
            continue
        problems = validate(sb, answer, post_date)
        if not problems:
            return sb
        last_err = "; ".join(problems)
        log.warning("attempt %d problems: %s", attempt + 1, last_err)
    raise RuntimeError(f"could not produce a valid storyboard after {attempts} attempts: {last_err}")


def reel_id_for(topic: dict, post_date: date, slot: str) -> str:
    return f"{post_date.strftime('%Y%m%d')}-{slot}-{slugify(topic.get('name', 'reel'))[:40]}"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("topic")
    ap.add_argument("--date", default=None)
    ap.add_argument("--slot", default="afternoon")
    ap.add_argument("--id", default=None)
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    topic = json.loads(Path(args.topic).read_text(encoding="utf-8"))
    post_date = date.fromisoformat(args.date) if args.date else date.today()
    reel_id = args.id or reel_id_for(topic, post_date, args.slot)
    sb = generate_storyboard(topic, post_date, reel_id)
    out = paths.STORYBOARDS / f"{reel_id}.json"
    out.write_text(sb.model_dump_json(indent=2, exclude_none=True), encoding="utf-8")
    print(json.dumps({"storyboard": str(out), "title": sb.title, "scenes": len(sb.scenes), "words": sum(len(re.findall(r"\S+", s.narration)) for s in sb.scenes)}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
