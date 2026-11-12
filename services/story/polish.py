"""Text quality tools for the unattended pipeline, from the instagram-agent-skill pack
(vendored under third_party/instagram-agent-skill, MIT):

  score_hook       five local heuristics on the hook line (length, specificity, stakes,
                   front-loading, address); WEAK under 50, dealbreakers for greetings and preambles
  classify_hook    which of the pack's 26 hook formulas a hook matches (for the feedback loop)
  humanize_text    strip the machine fingerprint: invisible characters, em dashes, curly quotes,
                   slop words; structural tells are reported, never rewritten
  human_score      the five-check "how machine-written does this look" panel
  lint_caption_feed  what the feed shows before "... more", hashtag cap (5), link, ask, keywords

The scripts are loaded by path so they stay unmodified copies of the upstream files.
"""
from __future__ import annotations

import importlib.util
import json
import re
from functools import lru_cache
from pathlib import Path
from types import ModuleType

from services.common import paths

VENDOR = paths.ROOT / "third_party" / "instagram-agent-skill"
HOOK_WEAK = 50.0


@lru_cache(maxsize=None)
def _mod(name: str) -> ModuleType:
    file = VENDOR / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"igskill_{name}", file)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {file}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@lru_cache(maxsize=None)
def _lexicon() -> dict:
    return _mod("humanize").load_lexicon(str(VENDOR / "slop.json"))


@lru_cache(maxsize=None)
def _hooks() -> dict:
    return json.loads((VENDOR / "hooks.json").read_text(encoding="utf-8"))


def first_sentence(text: str) -> str:
    parts = re.split(r"(?<=[.!?])\s+", (text or "").strip(), maxsplit=1)
    return parts[0] if parts else ""


def score_hook(text: str) -> dict:
    """Score the spoken hook. Returns score, verdict (STRONG/OK/WEAK), dealbreaker flags, the
    weakest property and its detail, and every check's score."""
    hs = _mod("hookscore")
    results, overall, verdict, flags = hs.run(text or "")
    weakest = min(hs.CHECKS, key=lambda c: results[c][0])
    out = {"score": round(overall, 1), "verdict": verdict, "flags": list(flags), "weakest": weakest,
           "detail": results[weakest][1], "checks": {c: round(results[c][0], 1) for c in hs.CHECKS}}
    if hasattr(hs, "payoff"):  # master-skills version: where the first concrete payoff sits
        p = hs.payoff(text or "")
        out["payoff"] = p["status"]          # LEADS / LATE / MISSING
        out["payoff_token"] = p.get("token")
    return out


def hook_advice(text: str) -> str | None:
    """One line telling the writer why the hook is weak, or None when it is fine."""
    r = score_hook(text)
    if r["flags"]:
        return f"hook has a dealbreaker: {'; '.join(r['flags'])}"
    if r["verdict"] == "WEAK":
        tips = {"LENGTH": "say it in 5 to 12 words before the second sentence",
                "SPECIFICITY": "put a concrete number, name or product in the first sentence",
                "STAKES": "say what the viewer loses or gains",
                "FRONTLOAD": "put the payload first, the setup after",
                "ADDRESS": "aim it at a person (you, coders, developers)"}
        line = f"hook scores {r['score']:.0f}/100, weakest {r['weakest']} ({r['detail']}): {tips.get(r['weakest'], 'rewrite it')}"
        if r.get("payoff") == "MISSING":
            line += "; no number or figure anywhere in the hook, lead with one"
        elif r.get("payoff") == "LATE":
            line += f"; the figure '{r.get('payoff_token')}' comes too late, move it into the first 8 words"
        return line
    return None


def classify_hook(text: str) -> str | None:
    """Name of the best-matching hook formula (most patterns hit), else None."""
    if not (text or "").strip():
        return None
    hs = _mod("hookscore")
    if hasattr(hs, "classify"):  # master-skills version: every formula carries several patterns
        formula, hits = hs.classify(text)
        return formula["name"] if formula and hits else None
    data = _hooks()
    by_id = {h["id"]: h for h in data.get("hooks", [])}
    for hid in data.get("classify_order", []):
        h = by_id.get(hid)
        pats = h.get("match") if h else None
        pats = [pats] if isinstance(pats, str) else (pats or [])
        if any(re.search(p, text, re.I) for p in pats):
            return h["name"]
    return None


def humanize_text(text: str) -> tuple[str, dict]:
    """Cleaned text plus {"changes": n, "structures": [...], "report": {...}}."""
    if not text or not text.strip():
        return text, {"changes": 0, "structures": [], "report": {}}
    clean, report = _mod("humanize").humanize(text, _lexicon())
    changes = sum(int(h.get("count", 1)) for key in ("invisible", "typographic", "lexical") for h in report.get(key, []))
    return clean.rstrip("\n"), {"changes": changes, "structures": report.get("structures", []), "report": report}


def machine_artefacts(text: str) -> list[str]:
    """Human-readable list of what humanize_text would change (for the rules checker)."""
    _, info = humanize_text(text)
    out = []
    for h in info["report"].get("invisible", []) + info["report"].get("typographic", []):
        out.append(f"{h.get('count', 1)}x {h.get('name')}")
    for h in info["report"].get("lexical", []):
        out.append(f"{h.get('count', 1)}x '{h.get('find')}' -> '{h.get('replace')}'")
    return out


def human_score(text: str) -> dict:
    results, overall, verdict = _mod("detect").run(text or "", _lexicon())
    return {"score": round(overall, 1), "verdict": verdict, "checks": {k: round(v[0], 1) for k, v in results.items()}}


def lint_caption_feed(caption: str, keywords: list[str] | None = None) -> dict:
    """The pack's caption analysis: verdict READY / REVIEW / FIX plus the failing checks."""
    a = _mod("caption").analyse(caption or "", keywords=list(keywords or []))
    return {"verdict": a["verdict"], "visible": a["visible"], "hashtags": a["hashtags"],
            "fails": [c for c in a["checks"] if c["status"] == "FAIL"], "warns": [c for c in a["checks"] if c["status"] == "WARN"]}
