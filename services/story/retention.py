"""Pre-flight retention estimate from Reelsmith (rules only, offline).

Reelsmith is a sibling project (a free, local reel engine built on 2026-11-12). Its CLI's
`score --no-claude` runs a fixed set of heuristics on a script's hook, body and call to action
and returns a retention estimate out of 100, a watch curve and fixes pinned to seconds. It is an
estimate, never measured data; here it is a soft gate in the script step (fed back to the writer
on every attempt but the last, like the hook score) and one more attribute the feedback loop
records per reel, so the real watch time can later show whether the estimate predicts anything.

The Claude-backed score is never used from the pipeline: the scheduler's PATH has no `claude`,
and the storyboard step already has its own Claude session when it needs one.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Callable

from services.common import paths

DEFAULT_CLI = paths.ROOT.parent / "New folder" / "reelsmith" / "worker" / "cli.mjs"
TIMEOUT_S = 90
WORDS_PER_SECOND = 2.6  # Reelsmith's own default pace, used when the reel has no timing yet
RETENTION_FIXES_MAX = 3


def cli_path() -> Path | None:
    """The Reelsmith CLI: REELSMITH_CLI in .env, else the sibling project. None when absent."""
    raw = os.environ.get("REELSMITH_CLI", "").strip()
    p = Path(raw) if raw else DEFAULT_CLI
    return p if p.exists() else None


def minimum() -> float:
    """Estimates under this are pushed back to the writer (RETENTION_MIN, default 70)."""
    try:
        return float(os.environ.get("RETENTION_MIN") or 70)
    except ValueError:
        return 70.0


def available() -> bool:
    return cli_path() is not None and shutil.which("node") is not None


def script_from_scenes(narrations: list[str], title: str = "") -> dict:
    """Reelsmith's script shape: hook = first scene, cta = last scene, body = one line per scene between."""
    lines = [n.strip() for n in narrations if n and n.strip()]
    if not lines:
        return {"title": title, "hook": "", "body": "", "cta": ""}
    if len(lines) == 1:
        return {"title": title or lines[0][:60], "hook": lines[0], "body": "", "cta": ""}
    return {"title": title or lines[0][:60], "hook": lines[0], "body": "\n".join(lines[1:-1]), "cta": lines[-1]}


def script_from_storyboard(sb: Any) -> dict:
    """Accepts the Storyboard model or its JSON dict."""
    data = sb if isinstance(sb, dict) else sb.model_dump()
    scenes = data.get("scenes") or []
    return script_from_scenes([s.get("narration") or "" for s in scenes], data.get("title") or "")


def _duration(script: dict, duration_s: float | None) -> float:
    if duration_s:
        return float(duration_s)
    words = len(" ".join([script["hook"], script["body"], script["cta"]]).split())
    return round(words / WORDS_PER_SECOND, 1)


def estimate(sb: Any, duration_s: float | None = None, timeout_s: int = TIMEOUT_S, run: Callable = subprocess.run) -> dict | None:
    """Rules-only Reelsmith score for a storyboard. None when Reelsmith or node is missing, the
    call fails or the answer is not the expected JSON: the pipeline never stops for this."""
    cli = cli_path()
    node = shutil.which("node")
    if cli is None or node is None:
        return None
    script = script_from_storyboard(sb)
    if not script["hook"] and not script["body"]:
        return None
    fd, tmp = tempfile.mkstemp(prefix="reelforge-retention-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(script, fh)
        cmd = [node, str(cli), "score", "--script", tmp, "--duration", str(_duration(script, duration_s)), "--no-claude"]
        try:
            r = run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout_s)
        except (OSError, subprocess.TimeoutExpired):
            return None
        if r.returncode != 0 or not r.stdout:
            return None
        try:
            out = json.loads(r.stdout)
        except json.JSONDecodeError:
            return None
        if not out.get("ok") or "score" not in out:
            return None
        fixes = [{"t": f.get("t"), "text": f.get("text")} for f in out.get("fixes") or [] if f.get("text")]
        return {"score": float(out["score"]), "avg_watch_pct": out.get("avg_watch_pct"), "skip_rate_3s": out.get("skip_rate_3s"),
                "duration_s": out.get("duration_s"), "fixes": fixes, "by": out.get("by") or "rules"}
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def advice(est: dict | None, floor: float | None = None) -> str | None:
    """One line for the writer when the estimate is under the floor, else None."""
    if not est:
        return None
    floor = minimum() if floor is None else floor
    if est["score"] >= floor:
        return None
    fixes = "; ".join(f"at {int(f['t'] or 0)} s: {f['text']}" for f in est["fixes"][:RETENTION_FIXES_MAX]) or "tighten the hook and the ask"
    return f"retention estimate {est['score']:.0f}/100 (Reelsmith rules, aim for {floor:.0f}+): {fixes}"


def report(sb: Any, duration_s: float | None = None) -> dict:
    """Keys for the rules CLI output: retention_score, retention_fixes, retention_advice."""
    if not available():
        return {"retention_score": None, "retention_note": "Reelsmith not available (set REELSMITH_CLI or install the sibling project)"}
    est = estimate(sb, duration_s)
    if est is None:
        return {"retention_score": None, "retention_note": "Reelsmith score failed"}
    return {"retention_score": est["score"], "retention_fixes": [f["text"] for f in est["fixes"]], "retention_advice": advice(est)}
