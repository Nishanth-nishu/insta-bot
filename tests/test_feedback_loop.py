"""Feedback loop: insights collector, learning step, and how it feeds the pipeline."""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from services.analytics import insights, learn


def _approved(tmp_path: Path, reels: list[tuple[str, float, str]], now: float) -> Path:
    root = tmp_path / "approved"
    for i, (rid, age_h, slot) in enumerate(reels):
        d = root / "2026-11-10" / rid
        d.mkdir(parents=True)
        meta = {"id": rid, "status": "published", "media_id": f"m{i}", "slot": slot, "duration_s": 34.0, "title": f"Reel {rid}", "topic": "t",
                "published_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now - age_h * 3600))}
        (d / "package.json").write_text(json.dumps(meta), encoding="utf-8")
    (root / "2026-11-09" / "draft").mkdir(parents=True)
    (root / "2026-11-09" / "draft" / "package.json").write_text(json.dumps({"id": "draft", "status": "ready"}), encoding="utf-8")
    return root


def test_collect_snapshots_and_survives_blocked_calls(tmp_path):
    now = time.time()
    root = _approved(tmp_path, [("r1", 30, "afternoon"), ("r2", 26, "night"), ("r3", 50 * 24, "night")], now)
    calls = []

    def fake(mid):
        calls.append(mid)
        if mid == "m1":
            raise RuntimeError("API access blocked. access_token=SECRET")
        return {"views": 1200, "reach": 900, "likes": 40, "comments": 3, "saved": 10, "shares": 5, "ig_reels_avg_watch_time": 15, "permalink": "https://instagram.com/reel/x"}

    state_path = tmp_path / "insights.json"
    s = insights.collect(fetch=fake, approved_root=root, state_path=state_path, token="SECRET", now=now)
    assert s["reels"] == 3 and s["collected"] == 1 and s["skipped_old"] == 1
    assert s["errors"][0]["reel_id"] == "r2" and "SECRET" not in s["errors"][0]["error"]
    assert calls == ["m0", "m1"]  # the 50-day-old reel is not polled
    state = json.loads(state_path.read_text(encoding="utf-8"))
    snap = state["reels"]["m0"]["snapshots"][0]
    assert snap["views"] == 1200 and snap["age_h"] == 30.0 and "permalink" not in snap
    assert state["reels"]["m0"]["post_url"] == "https://instagram.com/reel/x"
    # a second run within the hour replaces the snapshot instead of piling up
    insights.collect(fetch=fake, approved_root=root, state_path=state_path, token="SECRET", now=now + 600)
    assert len(json.loads(state_path.read_text(encoding="utf-8"))["reels"]["m0"]["snapshots"]) == 1
    insights.collect(fetch=fake, approved_root=root, state_path=state_path, token="SECRET", now=now + 7200)
    assert len(json.loads(state_path.read_text(encoding="utf-8"))["reels"]["m0"]["snapshots"]) == 2


def test_hook_formula_families():
    cases = {"If you want a job, watch this.": "if-you", "Stop using ChatGPT for this.": "warning",
             "Why did OpenAI buy a computer company?": "question", "3 tools just died.": "number",
             "Everyone is wrong about agents.": "callout", "DeepSeek shipped a one bit model.": "statement", "": "unknown"}
    for text, family in cases.items():
        assert learn.hook_formula(text) == family, text


def _state(rows):
    """rows: (reel_id, age_h, views, reach, avg_watch, duration, actions)"""
    reels = {}
    for i, (rid, age, views, reach, watch, dur, actions) in enumerate(rows):
        reels[f"m{i}"] = {"reel_id": rid, "title": rid, "slot": "afternoon" if i % 2 == 0 else "night", "duration_s": dur,
                          "published_at": "2026-11-10T10:00:00Z",
                          "snapshots": [{"at": "x", "age_h": age, "views": views, "reach": reach, "likes": actions, "comments": 0, "saved": 0, "shares": 0, "ig_reels_avg_watch_time": watch}]}
    return {"reels": reels}


def _attrs_for(table):
    return lambda rid: {"hook": table[rid][1], "hook_formula": learn.hook_formula(table[rid][1]), "scene_kinds": ["hook", "payoff"], "words": 120, "category": table[rid][0], "duration_s": None}


def test_unsettled_reels_are_ignored_and_brief_says_collecting(tmp_path):
    state = _state([("a", 30, 1000, 800, 15, 34, 20), ("b", 3, 90000, 1, 34, 34, 500)])
    rows = learn.score_rows(learn.performance_rows(state, _attrs_for({"a": ("news", "If you code, read this."), "b": ("news", "x")})))
    assert [r["reel_id"] for r in rows] == ["a"]
    brief, weights = learn.build_brief(rows)
    assert "Collecting: 1 of 4" in brief and weights["enough"] is False and weights["category"] == {"news": 1.0}
    assert "Do more / do less" in brief and "Not enough reels yet" in brief


def test_enough_reels_produce_weights_and_do_more_lines(tmp_path):
    # news reels retain and spread; research reels die. Same length, same slot pattern.
    table = {"n1": ("news", "If you want a job, watch this."), "n2": ("news", "If you use Cursor, stop."),
             "r1": ("research", "A new paper on pose ranking."), "r2": ("research", "Protein models got better."), "r3": ("research", "Benchmarks moved again.")}
    state = _state([("n1", 40, 5000, 4000, 20, 34, 300), ("n2", 40, 4000, 3500, 18, 34, 250),
                    ("r1", 40, 400, 350, 6, 34, 5), ("r2", 40, 300, 300, 5, 34, 4), ("r3", 40, 350, 300, 7, 34, 6)])
    rows = learn.score_rows(learn.performance_rows(state, _attrs_for(table)))
    assert rows[0]["reel_id"] == "n1" and rows[-1]["category"] == "research"
    brief, weights = learn.build_brief(rows)
    assert weights["enough"] is True
    assert weights["category"]["news"] == learn.CLAMP[1] and weights["category"]["research"] < 0.9
    assert weights["hook_formula"]["if-you"] > weights["hook_formula"]["statement"]
    assert "Do more: open with a if-you hook" in brief and "Topics: news reels perform best" in brief
    assert "Retention is low" in brief  # median retention is 7/34 ~ 21%
    # refresh writes the three files and the pipeline readers pick them up
    out = tmp_path / "analytics"
    state_path = tmp_path / "insights.json"
    state_path.write_text(json.dumps(state), encoding="utf-8")
    summary = learn.refresh(state_path=state_path, out_dir=out, attrs_fn=_attrs_for(table))
    assert summary["enough"] and summary["top"][0] == "n1"
    w = learn.load_weights(out / "weights.json")
    assert learn.category_weight("news", w) == learn.CLAMP[1] and learn.category_weight("unknown-cat", w) == 1.0 and learn.category_weight("research", w) < 0.9
    notes = learn.learning_notes(out / "brief.md", out / "niche.md")
    assert notes.startswith("WHAT IS WORKING ON THIS ACCOUNT") and "Do more" in notes
    (out / "niche.md").write_text("Niche watch, 2026-11-11\n## Hook shapes working now\n- fear of missing the update", encoding="utf-8")
    assert "WHAT IS WORKING IN THE NICHE" in learn.learning_notes(out / "brief.md", out / "niche.md")


def test_weights_default_to_one_without_data(tmp_path):
    w = learn.load_weights(tmp_path / "missing.json")
    assert learn.category_weight("news", w) == 1.0
    assert learn.learning_notes(tmp_path / "no.md", tmp_path / "no2.md") == ""


def test_pick_topic_applies_category_weight(tmp_path, monkeypatch):
    from services.common import paths
    from services.scheduler import daily

    topics = tmp_path / "topics"
    topics.mkdir()
    (topics / "a.json").write_text(json.dumps({"name": "A research", "category": "research", "score_total": 90, "status": "candidate"}), encoding="utf-8")
    (topics / "b.json").write_text(json.dumps({"name": "B news", "category": "news", "score_total": 80, "status": "candidate"}), encoding="utf-8")
    monkeypatch.setattr(paths, "TOPICS", topics)
    monkeypatch.setenv("PUBLISH_SLOTS", "afternoon=15:30,night=20:40")
    monkeypatch.setattr(daily, "slots", lambda: {"night": {"prefer": [], "publish": "20:40", "produce": "18:10"}})
    monkeypatch.setattr(learn, "load_weights", lambda path=None: {"enough": False, "category": {}})
    assert daily.pick_topic("night")["name"] == "A research"
    monkeypatch.setattr(learn, "load_weights", lambda path=None: {"enough": True, "category": {"news": 1.3, "research": 0.75}})
    assert daily.pick_topic("night")["name"] == "B news"


def test_user_prompt_carries_the_notes(tmp_path, monkeypatch):
    from datetime import date

    from services.story import generate

    monkeypatch.setattr(generate, "learning_notes", lambda: "WHAT IS WORKING ON THIS ACCOUNT\n- Do more: question hooks")
    msg = generate.user_prompt({"name": "T", "category": "news"}, date(2026, 11, 12))
    assert "Do more: question hooks" in msg and msg.endswith("Write the JSON for this topic now.")
    monkeypatch.setattr(generate, "learning_notes", lambda: "")
    assert "WHAT IS WORKING" not in generate.user_prompt({"name": "T"}, date(2026, 11, 12))
    assert "data/analytics/brief.md" in generate.AGENT_PROMPT
