"""House content rules for the presenter format (checked before voiceover and packaging).

    python -m services.common.rules data/storyboards/<id>.json

Rules:
  1. No link anywhere the viewer can see or hear: caption, titles, lines, CTA,
     terminal commands, narration. The link goes out by DM after the viewer
     comments the keyword (`dmKeyword`).
  2. On-screen text is Latin script only (the display fonts have no Devanagari).
     Narration is English; if it ever carries Devanagari it needs a romanised
     `captionText` with the same sentence and word count.
  3. Scene 1 is the hook: at most 18 spoken words so it lands inside 5 seconds.
  4. The last scene's CTA and the Instagram caption both ask for the keyword.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any, Iterable

from services.common.schema import Storyboard

URL_RE = re.compile(r"(https?://\S+|www\.\S+|\b[\w.-]+\.(?:com|io|ai|org|net|dev|co|app|xyz)\b(?:/\S*)?)", re.I)
DEVANAGARI_RE = re.compile(r"[ऀ-ॿ]")
SENTENCE_RE = re.compile(r"(?<=[.!?।])\s+")
HOOK_MAX_WORDS = 18


def split_sentences(text: str) -> list[str]:
    return [s.strip() for s in SENTENCE_RE.split(text.strip()) if s.strip()]


def words(text: str) -> list[str]:
    return re.findall(r"\S+", text)


def _strings(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for v in value.values():
            yield from _strings(v)
    elif isinstance(value, (list, tuple)):
        for v in value:
            yield from _strings(v)


def check_storyboard(sb: Storyboard) -> list[str]:
    problems: list[str] = []
    kw = sb.dmKeyword.lower()
    if URL_RE.search(sb.caption):
        problems.append("caption contains a link; ask for the comment keyword instead, the DM bot sends the link")
    if kw not in sb.caption.lower():
        problems.append(f"caption never asks for the keyword '{sb.dmKeyword}'")
    if not sb.scenes:
        return problems + ["no scenes"]
    if sb.scenes[0].kind != "hook":
        problems.append("scene 1 must be the hook")
    hook_words = len(words(sb.scenes[0].narration))
    if hook_words > HOOK_MAX_WORDS:
        problems.append(f"hook narration is {hook_words} words; keep it under {HOOK_MAX_WORDS} so it lands in 5 s")
    last = sb.scenes[-1]
    if kw not in (last.cta or "").lower() and kw not in last.narration.lower():
        problems.append(f"last scene must ask viewers to comment '{sb.dmKeyword}' (cta or narration)")
    for sc in sb.scenes:
        visible = [sc.title or "", sc.cta or "", sc.captionText or "", *sc.lines, *list(_strings(sc.data))]
        for text in visible:
            if URL_RE.search(text):
                problems.append(f"scene {sc.id}: on-screen text contains a link: {text[:60]!r}")
            if DEVANAGARI_RE.search(text):
                problems.append(f"scene {sc.id}: Devanagari on screen (fonts lack it): {text[:40]!r}")
        if URL_RE.search(sc.narration):
            problems.append(f"scene {sc.id}: narration speaks a link")
        if DEVANAGARI_RE.search(sc.narration):
            if not sc.captionText:
                problems.append(f"scene {sc.id}: narration has Devanagari but no romanised captionText")
            else:
                ns, cs = split_sentences(sc.narration), split_sentences(sc.captionText)
                if len(ns) != len(cs):
                    problems.append(f"scene {sc.id}: narration has {len(ns)} sentences, captionText {len(cs)}")
                else:
                    for i, (a, b) in enumerate(zip(ns, cs)):
                        if len(words(a)) != len(words(b)):
                            problems.append(f"scene {sc.id}: sentence {i + 1} has {len(words(a))} spoken words but {len(words(b))} caption words")
    # instagram-agent-skill checks (third_party): greetings/preambles in the hook and machine
    # artefacts (em dashes, curly quotes, slop words) are hard problems; the hook score is advice.
    try:
        from services.story import polish

        hook_text = sb.scenes[0].narration if sb.scenes else ""
        hs = polish.score_hook(hook_text)
        for f in hs["flags"]:
            problems.append(f"hook dealbreaker: {f}")
        for sc in sb.scenes:
            arts = polish.machine_artefacts(sc.narration)
            if arts:
                problems.append(f"scene {sc.id}: narration has machine artefacts ({', '.join(arts[:3])}); use plain commas, straight quotes, plain words")
        arts = polish.machine_artefacts(sb.caption or "")
        if arts:
            problems.append(f"caption has machine artefacts ({', '.join(arts[:3])})")
        feed = polish.lint_caption_feed(sb.caption or "", [sb.dmKeyword])
        for c in feed["fails"]:
            if c["check"] not in ("link",):  # links are already reported above
                problems.append(f"caption {c['check']}: {c['detail']}")
    except Exception as exc:  # the vendored scripts are optional helpers, never a crash
        problems.append(f"polish checks unavailable: {exc}")
    return problems


def hook_report(sb: Storyboard) -> dict:
    """Hook score and formula for the CLI output (advice, not a gate)."""
    try:
        from services.story import polish

        text = sb.scenes[0].narration if sb.scenes else ""
        r = polish.score_hook(text)
        return {"hook_score": r["score"], "hook_verdict": r["verdict"], "hook_weakest": r["weakest"], "hook_formula": polish.classify_hook(text), "hook_advice": polish.hook_advice(text)}
    except Exception as exc:
        return {"hook_score": None, "hook_error": str(exc)}


def retention_report(sb: Storyboard) -> dict:
    """Reelsmith's rules-only retention estimate for the CLI output (advice, not a gate)."""
    try:
        from services.story import retention

        return retention.report(sb)
    except Exception as exc:
        return {"retention_score": None, "retention_error": str(exc)}


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if not args:
        print(__doc__)
        return 2
    rc = 0
    for arg in args:
        sb = Storyboard.model_validate_json(Path(arg).read_text(encoding="utf-8"))
        problems = check_storyboard(sb)
        print(json.dumps({"storyboard": arg, "ok": not problems, "problems": problems, **hook_report(sb), **retention_report(sb)}, indent=1, ensure_ascii=False))
        rc = rc or (1 if problems else 0)
    return rc


if __name__ == "__main__":
    sys.exit(main())
