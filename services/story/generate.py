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
import os
import re
import sys
from datetime import date
from pathlib import Path

from services.common import paths
from services.common.rules import check_storyboard, words
from services.common.schema import Storyboard
from services.common.env import load_dotenv
from services.llm.client import LLM, claude_executable
from services.analytics.learn import learning_notes
from services.story import polish, retention
from services.publishing.seo import build_caption, kicker_date, lint_caption, pick_hashtags
from services.research.score import slugify

log = logging.getLogger("reelforge.story")

EXAMPLE = paths.STORYBOARDS / "dsh-superagent-host.json"
DM_KEYWORD = "LETS GO"
CTA = "Comment LETS GO, the link lands in your DMs"
PAYOFF_LINE = "Want the link? Comment LETS GO and I'll send it to your DMs. And follow, so you're not the last to know."

SYSTEM = """You write 45-55 second Instagram Reel scripts for an Indian tech creator. An animated anime host says every line on camera. Output ONE JSON object and nothing else: no prose, no code fences.

HARD RULES
- English only, spoken and casual. Sentences under 14 words. Total narration 100 to 140 words across 5 to 7 scenes: that is about 20 words per scene, so each non-hook scene needs 2 or 3 full sentences. Count the words before you answer; 80 words is too short.
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


SYSTEM_LITE = """You write short Instagram Reel scripts. Output ONE JSON object only, no prose, no code fences.

Write exactly 5 scenes for the topic brief, in this order, with these fields:
1. {"id":"hook","kind":"hook","title":"6 to 9 words","highlight":["one word from the title"],"narration":"two sentences, 14 to 18 words total, a bit scary and funny, aimed at coders or developers","data":{"kicker":"AI NEWS / {kicker_date}"}}
2. {"id":"what","kind":"concept","title":"6 to 9 words","highlight":["one word from the title"],"narration":"three sentences, 22 to 28 words total, what it is","lines":["4 to 6 words","4 to 6 words","4 to 6 words"],"data":{"kicker":"WHAT IT IS"}}
3. {"id":"how","kind":"concept","title":"6 to 9 words","highlight":["one word from the title"],"narration":"three sentences, 22 to 28 words total, how it works or why it matters","lines":["4 to 6 words","4 to 6 words","4 to 6 words"],"data":{"kicker":"HOW IT WORKS"}}
4. {"id":"flow","kind":"workflow","title":"6 to 9 words","highlight":["one word from the title"],"narration":"three sentences, 22 to 28 words total, the steps","data":{"stages":["step","step","step","step"],"goal":"2 or 3 words"}}
5. {"id":"payoff","kind":"payoff","title":"Want the link? Comment: LETS GO","highlight":["LETS","GO"],"narration":"one sentence of 8 to 12 words with the main takeaway","data":{"kicker":"THE TAKEAWAY"}}

Also return "title" (under 60 chars), "keyword" (2 to 4 words people search), "caption_summary" (two sentences), "niche_hashtags" (3 tags).
Rules: plain spoken English; facts only from the brief, never invent numbers; no links, URLs or domains anywhere; every highlight word must appear in that scene's title.
Shape: {"title":"...","keyword":"...","caption_summary":"...","niche_hashtags":["#a","#b","#c"],"scenes":[ ...5 scenes... ]}
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
    msg = f"POSTING DATE (IST): {post_date.isoformat()} -> kicker date text '{kicker_date(post_date)}'.\n\nTOPIC BRIEF:\n{json.dumps(brief, ensure_ascii=False, indent=1)}\n\nEXAMPLE OF THE JSON SHAPE (different topic, copy the structure not the words):\n{load_example()}"
    notes = learning_notes()
    if notes:
        msg += "\n\n" + notes
    msg += "\n\nWrite the JSON for this topic now."
    if problems:
        msg += "\n\nYour previous answer had these problems; fix every one and return the full JSON again:\n- " + "\n- ".join(problems)
    return msg


def _coerce(answer):
    """Small models wrap or flatten the shape: unwrap {"storyboard": {...}}, parse a JSON string, drop non-dict scenes."""
    if isinstance(answer, str):
        answer = json.loads(answer)
    if isinstance(answer, dict):
        for key in ("storyboard", "output", "result"):
            if isinstance(answer.get(key), dict) and "scenes" in answer[key]:
                answer = answer[key]
    if not isinstance(answer, dict):
        raise ValueError("answer is not a JSON object")
    scenes = answer.get("scenes")
    if isinstance(scenes, dict):
        scenes = list(scenes.values())
    fixed = []
    for sc in scenes or []:
        if isinstance(sc, str):
            try:
                sc = json.loads(sc)
            except Exception:
                continue
        if isinstance(sc, dict):
            fixed.append(sc)
    answer["scenes"] = fixed
    return answer


def assemble(answer: dict, topic: dict, post_date: date, reel_id: str, lite: bool = False) -> Storyboard:
    answer = _coerce(answer)
    if lite and answer.get("scenes"):
        last = answer["scenes"][-1]
        take = (last.get("narration") or "").strip() if last.get("kind") == "payoff" else ""
        if last.get("kind") != "payoff":
            answer["scenes"].append({"id": "payoff", "kind": "payoff", "title": "Want the link? Comment: LETS GO", "highlight": ["LETS", "GO"], "data": {"kicker": "THE TAKEAWAY"}})
            last = answer["scenes"][-1]
        take = take.split("Want the link")[0].strip()
        last["narration"] = (take + " " if take else "") + PAYOFF_LINE
        last["title"] = "Want the link? Comment: LETS GO"
        last["highlight"] = ["LETS", "GO"]
        hook = answer["scenes"][0]
        hook.setdefault("data", {})["kicker"] = f"AI NEWS / {kicker_date(post_date)}"
    scenes = []
    for i, sc in enumerate(answer.get("scenes", [])):
        kind = sc.get("kind", "concept")
        title = (sc.get("title") or "").strip()
        title_words = [w.strip(".,!?:;\"'") for w in title.split()]
        highlight = [h for h in (sc.get("highlight") or []) if h.lower().strip(".,!?") in [t.lower() for t in title_words]]
        if not highlight and title_words:
            highlight = [max(title_words, key=len)]  # longest title word stands in
        lines = [str(x).strip() for x in (sc.get("lines") or []) if str(x).strip()]
        if kind == "concept":
            if len(lines) < 3:  # pad from the narration's sentences
                extra = [t.strip() for t in re.split(r"(?<=[.!?])\s+", sc.get("narration", "")) if t.strip()]
                lines = (lines + [e[:60] for e in extra if e not in lines])[:4]
            lines = lines[:4]
        scene = {
            "id": sc.get("id") or f"s{i + 1}",
            "kind": kind,
            "narration": polish.humanize_text(sc.get("narration", "").strip())[0],
            "title": title or None,
            "highlight": highlight,
            "lines": lines,
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
    caption = polish.humanize_text(build_caption(hook_line, answer.get("caption_summary", ""), keyword, post_date, hashtags, keyword_phrase=answer.get("title")))[0]
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


def validate(sb: Storyboard, answer: dict, post_date: date, lite: bool = False, strict_hook: bool = False) -> list[str]:
    """strict_hook: also treat a WEAK hook score as a problem (the retry loop uses it on every
    attempt but the last, so a weak-but-legal hook never blocks production)."""
    problems = check_storyboard(sb)
    n = len(sb.scenes)
    if not 4 <= n <= 7:
        problems.append(f"{n} scenes; write 5 to 7")
    total = sum(len(words(s.narration)) for s in sb.scenes)
    floor = 75 if lite else 90
    if not floor <= total <= 150:
        per = ", ".join(f"{s.id}={len(words(s.narration))}" for s in sb.scenes)
        problems.append(f"narration is {total} words in total ({per}); write 100 to 140, about 20 per scene")
    if sb.scenes and strict_hook and not lite:
        advice = polish.hook_advice(sb.scenes[0].narration)
        if advice:
            problems.append(advice)
        # Reelsmith's rules-only retention estimate: a pre-flight on the whole spoken script,
        # fed back with its fixes on every attempt but the last; silent when Reelsmith is absent.
        ret = retention.advice(retention.estimate(sb))
        if ret:
            problems.append(ret)
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


AGENT_PROMPT = """You are producing one Instagram Reel storyboard for REELFORGE, unattended. Work only inside this repository and only on the files named here.

Topic brief file: {topic_file}
Posting date (IST): {post_date}  -> the hook scene's kicker must end with " / {kicker_date}"
Write the storyboard to: {out_path}
Reel id: {reel_id}

Do this, in order:
1. Read .claude/skills/generate-script/SKILL.md and .claude/skills/generate-script/hooks.md (house rules, hook formulas), .claude/skills/create-storyboard/SKILL.md, and data/storyboards/dsh-superagent-host.json (the exact JSON shape to follow, including voice, presenter, music, dmKeyword, dmLink, caption, hashtags). Then, if they exist, read data/analytics/brief.md (what performed on this account: follow its "Do more / do less" lines for the hook shape, topic angle and length) and data/analytics/niche.md (what is working in the niche this week: borrow hook shapes, never words).
2. Read the topic brief. Use the brief as the only source of facts. Never invent numbers.
3. Write the storyboard JSON at the path above with id "{reel_id}": 5 to 7 scenes, English, hook under 16 words and anxious-funny, 100 to 140 narration words, no link or URL anywhere on screen or in the caption, last scene a payoff with cta "Comment LETS GO, the link lands in your DMs" and narration ending "Want the link? Comment LETS GO and I'll send it to your DMs. And follow, so you're not the last to know.", presenter look "vrm", voice am_fenrir, dmLink = the first source URL.
   Caption (SEO): first line = the hook with the search keyword, under 125 chars; two-sentence summary with the keyword once; a line "{caption_date} update."; the line "Want the link? Comment LETS GO and I'll DM it to you."; then exactly 5 hashtags: 3 niche + 2 of #ai #technews #artificialintelligence (Instagram caps a post at 5).
   Hook quality: the rules checker prints hook_score and hook_advice; aim for a score of 50 or more (a concrete number, name or product in the first sentence, 5 to 12 words, aimed at coders) and fix any dealbreaker. The checker also prints retention_score (a rules-only estimate of how much of the reel people will watch) with retention_fixes pinned to seconds; aim for 70 or more and apply the fixes that do not break the house format. No em dashes, curly quotes or slop words anywhere: the checker reports them as machine artefacts.
4. Run the rules checker:  .venv\\Scripts\\python -m services.common.rules {out_path}   and fix the file until it prints "ok": true.
5. Print the single line DONE when the file is valid. Do not render, synthesize, commit, publish, or touch any other file.
"""

# Least privilege for the headless session: read and search the repo, write the
# storyboard, and run only the rules checker. No bypass of permission checks;
# anything outside this list is refused in non-interactive mode.
AGENT_TOOLS = ["Read", "Glob", "Grep", "Write", "Edit", "Bash(.venv\\Scripts\\python -m services.common.rules *)", "Bash(.venv/Scripts/python -m services.common.rules *)"]


def generate_with_claude_code(topic_file: str, post_date: date, reel_id: str, timeout_s: int = 1500) -> Storyboard | None:
    """Let a headless Claude Code session write and validate the storyboard with a
    small tool allowlist. Returns None when the CLI is missing, not logged in,
    times out, or the file never validates; the caller then uses the one-shot path."""
    import subprocess

    exe = claude_executable()
    if not exe:
        return None
    out_path = paths.STORYBOARDS / f"{reel_id}.json"
    prompt = AGENT_PROMPT.format(topic_file=topic_file, post_date=post_date.isoformat(), kicker_date=kicker_date(post_date), out_path=str(out_path), reel_id=reel_id, caption_date=post_date.strftime("%d %b %Y").lstrip("0"))
    # The prompt goes over stdin: as an argument it would be cut at the first newline
    # by the npm claude.cmd shim on Windows.
    cmd = [exe, "-p", "--output-format", "text", "--max-turns", "40", "--allowedTools", *AGENT_TOOLS]
    try:
        r = subprocess.run(cmd, input=prompt, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout_s, cwd=str(paths.ROOT))
    except subprocess.TimeoutExpired:
        log.warning("claude code producer timed out after %ss", timeout_s)
        return None
    out = (r.stdout or "") + (r.stderr or "")
    logs = paths.OUTPUT / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    (logs / f"claude-producer-{reel_id}.log").write_text(out, encoding="utf-8")
    if r.returncode != 0 or "Not logged in" in out:
        log.warning("claude code producer unavailable: %s", out[-300:].strip())
        return None
    if not out_path.exists():
        log.warning("claude code producer wrote no file (see output/logs/claude-producer-%s.log); tail: %s", reel_id, out[-300:].strip())
        return None
    try:
        sb = Storyboard.model_validate_json(out_path.read_text(encoding="utf-8"))
    except Exception as exc:
        log.warning("claude code storyboard invalid: %s", str(exc)[:300])
        return None
    # hashtags may live in the `hashtags` field (packaging appends them to the caption)
    caption_full = sb.caption if any(t in sb.caption for t in sb.hashtags) else sb.caption + chr(10) + chr(10) + " ".join(sb.hashtags)
    problems = [p for p in check_storyboard(sb) + lint_caption(caption_full, "") if "primary keyword" not in p]
    if problems:
        log.warning("claude code storyboard breaks rules: %s", problems)
        return None
    sb.id = reel_id
    return sb


def generate_storyboard(topic: dict, post_date: date, reel_id: str, llm: LLM | None = None, attempts: int = 4) -> Storyboard:
    load_dotenv()
    provider = (llm.provider if llm else (os.environ.get("LLM_PROVIDER") or "")).strip().lower()
    if provider == "claude-cli" and topic.get("_file"):
        sb = generate_with_claude_code(topic["_file"], post_date, reel_id)
        if sb is not None:
            return sb
        log.warning("falling back to the one-shot LLM path")
        fb = (llm.fallbacks if llm else [p.strip() for p in (os.environ.get("LLM_FALLBACKS") or "sarvam,ollama").split(",") if p.strip()]) or ["sarvam"]
        llm = LLM(provider=fb[0], fallbacks=fb[1:])
    llm = llm or LLM()
    lite = llm.provider == "ollama"  # small local models get the simpler contract
    system = (SYSTEM_LITE if lite else SYSTEM).replace("{payoff}", PAYOFF_LINE).replace("{cta}", CTA).replace("{kicker_date}", kicker_date(post_date))
    problems: list[str] = []
    last_err = ""
    for attempt in range(attempts):
        if attempt == attempts - 1 and llm.provider == "sarvam" and not llm.model:
            llm.model = "sarvam-105b"  # last try: the reasoning model
        answer = llm.complete_json(system, user_prompt(topic, post_date, problems), max_tokens=8000, temperature=0.6 if attempt == 0 else 0.4)
        try:
            sb = assemble(answer, topic, post_date, reel_id, lite=lite)
        except Exception as exc:  # schema errors come back as problems
            log.warning("raw answer head: %s", json.dumps(answer)[:400] if not isinstance(answer, str) else answer[:400])
            problems = [f"storyboard schema error: {str(exc)[:300]}"]
            last_err = problems[0]
            log.warning("attempt %d: %s", attempt + 1, last_err)
            continue
        problems = validate(sb, answer, post_date, lite=lite, strict_hook=attempt < attempts - 1)
        if not problems:
            log.info("hook: %s", polish.score_hook(sb.scenes[0].narration) if sb.scenes else None)
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
    topic = json.loads(Path(args.topic).read_text(encoding="utf-8")) | {"_file": args.topic}
    post_date = date.fromisoformat(args.date) if args.date else date.today()
    reel_id = args.id or reel_id_for(topic, post_date, args.slot)
    sb = generate_storyboard(topic, post_date, reel_id)
    out = paths.STORYBOARDS / f"{reel_id}.json"
    out.write_text(sb.model_dump_json(indent=2, exclude_none=True), encoding="utf-8")
    print(json.dumps({"storyboard": str(out), "title": sb.title, "scenes": len(sb.scenes), "words": sum(len(re.findall(r"\S+", s.narration)) for s in sb.scenes)}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
