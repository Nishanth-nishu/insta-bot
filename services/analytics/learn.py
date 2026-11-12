"""Learn from the insights: a performance table per reel, a plain-language brief of what
is working, and weights that tilt the next topic choice.

    python -m services.analytics.learn           # refresh performance.json, brief.md, weights.json
    python -m services.analytics.learn --print   # also print the brief

How a reel is scored (relative to this account's own median, so a small account
learns from day one):
    retention  = average watch time / reel length           (the hook and pacing)
    engagement = (likes + comments + saves + shares) / reach (did it earn an action)
    views                                                     (did Instagram push it)
    index = 0.45 * retention_n + 0.30 * views_n + 0.25 * engagement_n, each _n = value / median, capped at 3

Only settled snapshots count (reel at least SETTLE_HOURS old). Below MIN_SAMPLES reels
the brief says "collecting" and every weight is 1.0, so a lucky first reel cannot
steer the channel. Weights are clamped to 0.75 .. 1.3: the loop nudges, it never
takes over the house format.
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path

from services.common import paths
from services.analytics.insights import INSIGHTS_FILE, ANALYTICS_DIR, load_state

log = logging.getLogger("reelforge.analytics")

PERF_FILE = ANALYTICS_DIR / "performance.json"
BRIEF_FILE = ANALYTICS_DIR / "brief.md"
WEIGHTS_FILE = ANALYTICS_DIR / "weights.json"
NICHE_FILE = ANALYTICS_DIR / "niche.md"

MIN_SAMPLES = 4
SETTLE_HOURS = 20
MIN_GROUP = 2
W_RET, W_VIEWS, W_ENG = 0.45, 0.30, 0.25
CLAMP = (0.75, 1.3)
LENGTH_BUCKETS = [(0, 30, "under 30 s"), (30, 40, "30-40 s"), (40, 55, "40-55 s"), (55, 10_000, "over 55 s")]


# ---------- attributes of a reel (what we chose) ----------

def hook_formula(text: str) -> str:
    """Rough hook family, for grouping: question, if-you, number, warning, callout, statement."""
    t = (text or "").strip().lower()
    if not t:
        return "unknown"
    first = t.split(".")[0].split("?")[0]
    if t.startswith(("if you", "if your")):
        return "if-you"
    if re.match(r"^(stop|don't|dont|never|wait|before you|warning)\b", t):
        return "warning"
    if "?" in text[: max(len(text) // 2, 40)]:
        return "question"
    if re.search(r"\d", first):
        return "number"
    if re.match(r"^(everyone|nobody|people|engineers|developers|your|you)\b", t):
        return "callout"
    return "statement"


def length_bucket(duration_s: float | None) -> str:
    if not duration_s:
        return "unknown"
    for lo, hi, name in LENGTH_BUCKETS:
        if lo <= duration_s < hi:
            return name
    return "unknown"


def reel_attributes(reel_id: str, storyboards: Path | None = None, topics: Path | None = None) -> dict:
    """Hook text, hook formula, scene kinds, narration words, category: read from the storyboard and the topic file."""
    sb_dir = storyboards or paths.STORYBOARDS
    tp_dir = topics or paths.TOPICS
    attrs = {"hook": None, "hook_formula": "unknown", "hook_pattern": "none", "hook_score": None, "retention_estimate": None, "scene_kinds": [], "words": None, "category": None, "duration_s": None}
    for name in (f"{reel_id}.timed.json", f"{reel_id}.json"):
        f = sb_dir / name
        if f.exists():
            try:
                sb = json.loads(f.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                continue
            scenes = sb.get("scenes") or []
            attrs["hook"] = (scenes[0].get("narration") or scenes[0].get("title")) if scenes else None
            attrs["hook_formula"] = hook_formula(attrs["hook"] or "")
            try:
                from services.story import polish

                attrs["hook_pattern"] = polish.classify_hook(attrs["hook"] or "") or "none"
                attrs["hook_score"] = polish.score_hook(attrs["hook"] or "")["score"]
            except Exception:
                pass
            attrs["scene_kinds"] = [s.get("kind") for s in scenes]
            attrs["words"] = sum(len((s.get("narration") or "").split()) for s in scenes)
            if sb.get("durationMs"):
                attrs["duration_s"] = round(sb["durationMs"] / 1000, 1)
            try:  # Reelsmith's pre-flight estimate, kept so real watch time can judge it
                from services.story import retention

                if retention.available():
                    est = retention.estimate(sb, attrs["duration_s"])
                    attrs["retention_estimate"] = est["score"] if est else None
            except Exception:
                pass
            break
    for f in tp_dir.glob("*.json"):
        try:
            t = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if t.get("reel_id") == reel_id:
            attrs["category"] = t.get("category")
            break
    return attrs


# ---------- performance table ----------

def settled_snapshot(rec: dict, now: float | None = None) -> dict | None:
    """The newest snapshot taken once the reel was at least SETTLE_HOURS old."""
    snaps = [s for s in rec.get("snapshots", []) if (s.get("age_h") or 0) >= SETTLE_HOURS]
    return snaps[-1] if snaps else None


def performance_rows(state: dict, attrs_fn=reel_attributes, now: float | None = None) -> list[dict]:
    rows = []
    for mid, rec in state.get("reels", {}).items():
        snap = settled_snapshot(rec, now)
        if not snap:
            continue
        a = attrs_fn(rec.get("reel_id") or "")
        duration = rec.get("duration_s") or a.get("duration_s")
        views = snap.get("views") or 0
        reach = snap.get("reach") or 0
        avg_watch = snap.get("ig_reels_avg_watch_time")
        if avg_watch is not None and avg_watch > 1000:   # the API reports milliseconds for some accounts
            avg_watch = avg_watch / 1000
        actions = sum((snap.get(k) or 0) for k in ("likes", "comments", "saved", "shares"))
        rows.append({
            "media_id": mid, "reel_id": rec.get("reel_id"), "title": rec.get("title"), "slot": rec.get("slot"),
            "published_at": rec.get("published_at"), "age_h": snap.get("age_h"), "post_url": rec.get("post_url"),
            "views": views, "reach": reach, "likes": snap.get("likes") or 0, "comments": snap.get("comments") or 0,
            "saved": snap.get("saved") or 0, "shares": snap.get("shares") or 0, "avg_watch_s": avg_watch,
            "duration_s": duration, "retention": (avg_watch / duration) if (avg_watch and duration) else None,
            "engagement": (actions / reach) if reach else None,
            "category": a.get("category"), "hook": a.get("hook"), "hook_formula": a.get("hook_formula"),
            "hook_pattern": a.get("hook_pattern") or "none", "hook_score": a.get("hook_score"),
            "hook_score_bucket": ("under 40" if (a.get("hook_score") or 0) < 40 else "40-55" if (a.get("hook_score") or 0) < 55 else "55+") if a.get("hook_score") is not None else "unknown",
            "retention_estimate": a.get("retention_estimate"),
            "retention_est_bucket": ("under 70" if a["retention_estimate"] < 70 else "70-85" if a["retention_estimate"] < 85 else "85+") if a.get("retention_estimate") is not None else "unknown",
            "length_bucket": length_bucket(duration), "words": a.get("words"), "scene_kinds": a.get("scene_kinds"),
        })
    return rows


def _norm(value: float | None, median: float | None) -> float:
    if value is None or not median:
        return 1.0
    return max(0.0, min(3.0, value / median))


def score_rows(rows: list[dict]) -> list[dict]:
    """Add a composite index to each row, relative to this account's medians."""
    med = {}
    for key in ("retention", "views", "engagement"):
        vals = [r[key] for r in rows if r.get(key)]
        med[key] = statistics.median(vals) if vals else None
    for r in rows:
        r["index"] = round(W_RET * _norm(r.get("retention"), med["retention"]) + W_VIEWS * _norm(r.get("views"), med["views"]) + W_ENG * _norm(r.get("engagement"), med["engagement"]), 3)
    rows.sort(key=lambda r: -r["index"])
    return rows


def aggregate(rows: list[dict], key: str) -> dict[str, dict]:
    groups: dict[str, list[dict]] = {}
    for r in rows:
        g = r.get(key) or "unknown"
        groups.setdefault(str(g), []).append(r)
    out = {}
    for g, rs in groups.items():
        ret = [r["retention"] for r in rs if r.get("retention")]
        out[g] = {"n": len(rs), "index": round(sum(r["index"] for r in rs) / len(rs), 3),
                  "retention": round(sum(ret) / len(ret), 3) if ret else None,
                  "views": round(sum(r["views"] for r in rs) / len(rs))}
    return dict(sorted(out.items(), key=lambda kv: -kv[1]["index"]))


def weights_from(groups: dict[str, dict], enough: bool) -> dict[str, float]:
    if not enough:
        return {g: 1.0 for g in groups}
    return {g: (round(max(CLAMP[0], min(CLAMP[1], v["index"])), 3) if v["n"] >= MIN_GROUP else 1.0) for g, v in groups.items()}


# ---------- the brief ----------

def _pct(x: float | None) -> str:
    return f"{x * 100:.0f}%" if x is not None else "n/a"


def build_brief(rows: list[dict], now: float | None = None) -> tuple[str, dict]:
    now = now or time.time()
    stamp = datetime.fromtimestamp(now, timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    enough = len(rows) >= MIN_SAMPLES
    by_cat, by_hook, by_len, by_slot = (aggregate(rows, k) for k in ("category", "hook_formula", "length_bucket", "slot"))
    weights = {"generated_at": stamp, "samples": len(rows), "enough": enough,
               "category": weights_from(by_cat, enough), "hook_formula": weights_from(by_hook, enough),
               "length_bucket": weights_from(by_len, enough), "slot": weights_from(by_slot, enough)}
    lines = [f"# What is working on this account", f"Generated {stamp} from {len(rows)} settled reel(s) (a reel counts once it is {SETTLE_HOURS} h old).", ""]
    if not enough:
        lines += [f"Collecting: {len(rows)} of {MIN_SAMPLES} reels needed before the loop changes anything. House defaults apply.", ""]
    if rows:
        ret = [r["retention"] for r in rows if r.get("retention")]
        med_ret = statistics.median(ret) if ret else None
        lines += ["## Top reels", ""]
        for r in rows[:3]:
            lines.append(f"- {r.get('title') or r.get('reel_id')}: {r['views']} views, retention {_pct(r.get('retention'))}, index {r['index']}. Hook: \"{(r.get('hook') or '')[:110]}\"")
        lines.append("")
        if rows[-1] is not rows[0]:
            w = rows[-1]
            lines += ["## Weakest reel", "", f"- {w.get('title') or w.get('reel_id')}: {w['views']} views, retention {_pct(w.get('retention'))}, index {w['index']}. Hook: \"{(w.get('hook') or '')[:110]}\"", ""]
        by_pattern, by_hs, by_re = aggregate(rows, "hook_pattern"), aggregate(rows, "hook_score_bucket"), aggregate(rows, "retention_est_bucket")
        for title, groups in (("By topic category", by_cat), ("By hook formula", by_hook), ("By hook pattern (instagram-agent-skill formulas)", by_pattern), ("By hook score (hookscore.py)", by_hs), ("By Reelsmith retention estimate (pre-flight, rules only)", by_re), ("By length", by_len), ("By slot", by_slot)):
            lines += [f"## {title}", "", "| group | reels | index | retention | avg views |", "|---|---|---|---|---|"]
            lines += [f"| {g} | {v['n']} | {v['index']} | {_pct(v['retention'])} | {v['views']} |" for g, v in groups.items()]
            lines.append("")
        lines += ["## Do more / do less", ""]
        if enough:
            best = next(((g, v) for g, v in by_hook.items() if v["n"] >= MIN_GROUP), None)
            if best:
                lines.append(f"- Do more: open with a {best[0]} hook (index {best[1]['index']} over {best[1]['n']} reels).")
            worst = next(((g, v) for g, v in reversed(list(by_hook.items())) if v["n"] >= MIN_GROUP and v["index"] < 0.9), None)
            if worst:
                lines.append(f"- Do less: {worst[0]} hooks (index {worst[1]['index']}).")
            bc = next(((g, v) for g, v in by_cat.items() if v["n"] >= MIN_GROUP), None)
            if bc:
                lines.append(f"- Topics: {bc[0]} reels perform best here (index {bc[1]['index']}); the topic picker now weights categories by this.")
            bl = next(((g, v) for g, v in by_len.items() if v["n"] >= MIN_GROUP), None)
            if bl:
                lines.append(f"- Length: aim for {bl[0]} (index {bl[1]['index']}).")
            if med_ret is not None and med_ret < 0.35:
                lines.append(f"- Retention is low (median {_pct(med_ret)}): people leave in the first seconds. Make the hook a sharper, more specific fear or promise and get to the point by second 5.")
            elif med_ret is not None and med_ret > 0.6:
                lines.append(f"- Retention is strong (median {_pct(med_ret)}): keep the hook style; push harder on the payoff and the LETS GO ask for more comments.")
            best_row = rows[0]
            lines.append(f"- Model the top reel's hook shape: \"{(best_row.get('hook') or '')[:140]}\"")
        else:
            lines.append("- Not enough reels yet. Follow the house rules; the first findings appear after the fourth settled reel.")
        lines.append("")
    return "\n".join(lines), weights


def refresh(state_path: Path = INSIGHTS_FILE, out_dir: Path = ANALYTICS_DIR, attrs_fn=reel_attributes, now: float | None = None) -> dict:
    state = load_state(state_path)
    rows = score_rows(performance_rows(state, attrs_fn, now))
    brief, weights = build_brief(rows, now)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / PERF_FILE.name).write_text(json.dumps(rows, indent=1, ensure_ascii=False), encoding="utf-8")
    (out_dir / BRIEF_FILE.name).write_text(brief, encoding="utf-8")
    (out_dir / WEIGHTS_FILE.name).write_text(json.dumps(weights, indent=1), encoding="utf-8")
    return {"samples": len(rows), "enough": weights["enough"], "top": [r.get("reel_id") for r in rows[:3]], "brief": str(out_dir / BRIEF_FILE.name)}


# ---------- readers used by the pipeline ----------

def load_weights(path: Path = WEIGHTS_FILE) -> dict:
    """Weights for the topic picker; every lookup defaults to 1.0."""
    if not path.exists():
        return {"enough": False, "category": {}, "hook_formula": {}, "length_bucket": {}, "slot": {}}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"enough": False, "category": {}, "hook_formula": {}, "length_bucket": {}, "slot": {}}


def category_weight(category: str | None, weights: dict | None = None) -> float:
    w = weights if weights is not None else load_weights()
    if not w.get("enough"):
        return 1.0
    return float(w.get("category", {}).get(category or "unknown", 1.0))


def learning_notes(brief_path: Path = BRIEF_FILE, niche_path: Path = NICHE_FILE, max_chars: int = 3500) -> str:
    """The text the script writer gets: the account brief (if it has findings) and the niche swipe file."""
    parts = []
    if brief_path.exists():
        text = brief_path.read_text(encoding="utf-8")
        if "## Do more / do less" in text:
            parts.append("WHAT IS WORKING ON THIS ACCOUNT (from Instagram insights; follow the 'Do more / do less' lines):\n" + text[:max_chars])
    if niche_path.exists():
        parts.append("WHAT IS WORKING IN THE NICHE THIS WEEK (swipe file; borrow the hook shapes, never the words):\n" + niche_path.read_text(encoding="utf-8")[:max_chars])
    return "\n\n".join(parts)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Refresh the performance table, the brief and the weights from the insights.")
    ap.add_argument("--print", action="store_true", help="print the brief after refreshing")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    summary = refresh()
    print(json.dumps(summary, indent=1))
    if args.print:
        print(BRIEF_FILE.read_text(encoding="utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
