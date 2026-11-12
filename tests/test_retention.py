"""Reelsmith pre-flight retention estimate: script mapping, soft failures, the soft gate (no node needed)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from services.story import retention


class _R:
    def __init__(self, stdout="", returncode=0):
        self.stdout, self.returncode, self.stderr = stdout, returncode, ""


def _ok(score, fixes=()):
    return _R(json.dumps({"ok": True, "score": score, "avg_watch_pct": 58, "skip_rate_3s": 32, "duration_s": 45,
                          "fixes": [{"t": t, "text": x} for t, x in fixes], "by": "rules"}))


@pytest.fixture
def cli(tmp_path, monkeypatch):
    f = tmp_path / "cli.mjs"
    f.write_text("// fake", encoding="utf-8")
    monkeypatch.setenv("REELSMITH_CLI", str(f))
    monkeypatch.setattr(retention.shutil, "which", lambda name: "node" if name == "node" else None)
    return f


def test_script_shape_hook_body_cta():
    s = retention.script_from_scenes(["Coders, GPT-6 burned 90M tokens.", "Beat one.", "Beat two.", "Comment LETS GO."], "T")
    assert s == {"title": "T", "hook": "Coders, GPT-6 burned 90M tokens.", "body": "Beat one.\nBeat two.", "cta": "Comment LETS GO."}
    assert retention.script_from_scenes(["only"])["hook"] == "only"
    assert retention.script_from_scenes([])["hook"] == ""
    sb = {"title": "x", "scenes": [{"narration": "a"}, {"narration": "b"}]}
    assert retention.script_from_storyboard(sb) == {"title": "x", "hook": "a", "body": "", "cta": "b"}


def test_estimate_calls_the_cli_rules_only_and_parses(cli):
    calls = {}

    def run(cmd, **kw):
        calls["cmd"] = cmd
        calls["script"] = json.loads(Path(cmd[cmd.index("--script") + 1]).read_text(encoding="utf-8"))
        return _ok(80, [(0, "The hook is long."), (43, "Make the call to action one short ask.")])

    est = retention.estimate({"scenes": [{"narration": "Hook here."}, {"narration": "Body."}, {"narration": "Ask."}]}, duration_s=45, run=run)
    assert est["score"] == 80 and est["by"] == "rules" and [f["t"] for f in est["fixes"]] == [0, 43]
    assert calls["cmd"][0] == "node" and calls["cmd"][1] == str(cli) and "--no-claude" in calls["cmd"]
    assert calls["cmd"][calls["cmd"].index("--duration") + 1] == "45.0"
    assert calls["script"]["hook"] == "Hook here." and calls["script"]["cta"] == "Ask."
    assert not any(a.startswith("--elevenlabs") for a in calls["cmd"])
    # the temp script file is removed
    assert not Path(calls["cmd"][calls["cmd"].index("--script") + 1]).exists()


def test_estimate_duration_from_words_when_untimed(cli):
    seen = {}

    def run(cmd, **kw):
        seen["d"] = float(cmd[cmd.index("--duration") + 1])
        return _ok(75)

    words = " ".join(["w"] * 52)
    retention.estimate({"scenes": [{"narration": words}, {"narration": "x"}]}, run=run)
    assert seen["d"] == pytest.approx(53 / retention.WORDS_PER_SECOND, abs=0.1)


def test_estimate_is_soft_on_every_failure(cli, monkeypatch):
    sb = {"scenes": [{"narration": "a"}, {"narration": "b"}]}
    assert retention.estimate(sb, run=lambda *a, **k: _R("", 1)) is None
    assert retention.estimate(sb, run=lambda *a, **k: _R("not json")) is None
    assert retention.estimate(sb, run=lambda *a, **k: _R(json.dumps({"ok": False, "error": "x"}))) is None

    def boom(*a, **k):
        raise OSError("no node")

    assert retention.estimate(sb, run=boom) is None
    assert retention.estimate({"scenes": []}, run=lambda *a, **k: _ok(90)) is None
    monkeypatch.setenv("REELSMITH_CLI", str(cli.parent / "missing.mjs"))
    assert retention.cli_path() is None and retention.available() is False
    assert retention.estimate(sb, run=lambda *a, **k: _ok(90)) is None
    assert retention.report(sb)["retention_score"] is None


def test_advice_only_under_the_floor(monkeypatch):
    low = {"score": 62.0, "fixes": [{"t": 0, "text": "Shorter hook."}, {"t": 43, "text": "One ask."}, {"t": 10, "text": "c"}, {"t": 20, "text": "d"}]}
    line = retention.advice(low)
    assert line.startswith("retention estimate 62/100") and "at 0 s: Shorter hook." in line and "at 43 s: One ask." in line and " d" not in line
    assert retention.advice({"score": 70.0, "fixes": []}) is None
    assert retention.advice(None) is None
    monkeypatch.setenv("RETENTION_MIN", "85")
    assert retention.minimum() == 85 and retention.advice({"score": 80.0, "fixes": []}) is not None
    monkeypatch.setenv("RETENTION_MIN", "junk")
    assert retention.minimum() == 70


def test_validate_pushes_a_low_estimate_back_but_never_on_the_last_attempt(monkeypatch):
    from datetime import date

    from services.common.schema import Storyboard
    from services.story import generate

    sb = Storyboard.model_validate(json.loads(generate.EXAMPLE.read_text(encoding="utf-8")))
    answer = {"scenes": [s.model_dump() for s in sb.scenes]}
    monkeypatch.setattr(generate.retention, "estimate", lambda s, duration_s=None, **k: {"score": 55.0, "fixes": [{"t": 0, "text": "Shorter hook."}]})
    monkeypatch.setattr(generate.polish, "hook_advice", lambda t: None)
    post = date(2026, 11, 12)
    strict = generate.validate(sb, answer, post, strict_hook=True)
    final = generate.validate(sb, answer, post, strict_hook=False)
    assert any(p.startswith("retention estimate 55/100") for p in strict)
    assert not any(p.startswith("retention estimate") for p in final)
    monkeypatch.setattr(generate.retention, "estimate", lambda s, duration_s=None, **k: None)
    assert not any(p.startswith("retention estimate") for p in generate.validate(sb, answer, post, strict_hook=True))


def test_rules_cli_and_feedback_loop_carry_the_estimate(monkeypatch, tmp_path):
    from services.common import rules
    from services.common.schema import Storyboard
    from services.story import generate
    from services.analytics import learn

    monkeypatch.setattr(retention, "available", lambda: True)
    monkeypatch.setattr(retention, "estimate", lambda s, duration_s=None, **k: {"score": 81.0, "fixes": [{"t": 0, "text": "Shorter hook."}]})
    sb = Storyboard.model_validate(json.loads(generate.EXAMPLE.read_text(encoding="utf-8")))
    rep = rules.retention_report(sb)
    assert rep["retention_score"] == 81.0 and rep["retention_fixes"] == ["Shorter hook."] and rep["retention_advice"] is None

    sbd = tmp_path / "sb"
    sbd.mkdir()
    (sbd / "r1.json").write_text(json.dumps({"title": "t", "scenes": [{"kind": "hook", "narration": "Coders, this costs $200."}, {"kind": "payoff", "narration": "Comment LETS GO."}]}), encoding="utf-8")
    a = learn.reel_attributes("r1", storyboards=sbd, topics=tmp_path)
    assert a["retention_estimate"] == 81.0
    rows = learn.score_rows(learn.performance_rows({"reels": {"r1": {"reel_id": "r1", "title": "t", "published_at": "2026-11-10T10:00:00+00:00", "duration_s": 40,
                                                                      "snapshots": [{"age_h": 30, "views": 100, "reach": 80, "likes": 3, "ig_reels_avg_watch_time": 12000}]}}},
                                                   lambda rid, **k: a, now=None))
    assert rows and rows[0]["retention_estimate"] == 81.0 and rows[0]["retention_est_bucket"] == "70-85"
    brief, _ = learn.build_brief(rows, None)
    assert "By Reelsmith retention estimate" in brief
