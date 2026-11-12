"""Daily automation: SEO caption, storyboard assembly and validation, topic choice, slots, tasks."""
import json
from datetime import date

from services.publishing.seo import build_caption, kicker_date, lint_caption, pick_hashtags
from services.scheduler import daily
from services.story.generate import assemble, reel_id_for, validate

TOPIC = {
    "name": "OpenAI buys Pierre to supercharge Codex",
    "category": "news",
    "description": "OpenAI acquired Pierre Computer Company to fold its code tools into Codex.",
    "sources": [{"url": "https://openai.com/index/example", "source": "rss:openai"}],
    "verified_claims": ["OpenAI acquired Pierre"],
}

ANSWER = {
    "title": "AI coding agent upgrade by OpenAI",
    "keyword": "AI coding agent",
    "caption_summary": "OpenAI is buying a company to boost Codex. Expect stronger AI coding agent tools.",
    "niche_hashtags": ["#codex", "#openai", "#aicodingagent"],
    "scenes": [
        {"id": "hook", "kind": "hook", "narration": "Coders, your AI coding agent just got huge. OpenAI bought a company to supercharge Codex.", "title": "OpenAI bought a company to supercharge Codex", "highlight": ["Codex"], "data": {"kicker": "OPENAI / 11 NOV 2026"}},
        {"id": "what", "kind": "concept", "narration": "Pierre makes Codex build, understand and review code. Cleaner output, faster loops, fewer missed bugs for everyone.", "title": "What Pierre does for Codex", "highlight": ["Pierre"], "lines": ["Builds code", "Reads code deeply", "Reviews pull requests"], "data": {"kicker": "PIERRE"}},
        {"id": "flow", "kind": "workflow", "narration": "Write a task. Codex builds it. Codex reviews it. You merge. That is the whole loop now, start to finish.", "title": "Task in, merge out", "highlight": ["merge"], "data": {"stages": ["Task", "Build", "Review", "Merge"], "goal": "You decide"}},
        {"id": "why", "kind": "concept", "narration": "Developers get a smarter code brain. It builds, reads and reviews your pull requests and actually speeds you up.", "title": "What this means for developers", "highlight": ["developers"], "lines": ["Smarter reviews", "Faster iteration", "Fewer bugs"], "data": {"kicker": "DEV LIFE"}},
        {"id": "payoff", "kind": "payoff", "narration": "Want the link? Comment LETS GO and I'll send it to your DMs. And follow, so you're not the last to know.", "title": "Want the link? Comment LETS GO", "highlight": ["LETS", "GO"], "data": {"kicker": "THE TAKEAWAY"}},
    ],
}


def test_kicker_and_caption_dates():
    d = date(2026, 11, 11)
    assert kicker_date(d) == "11 NOV 2026"
    assert kicker_date(date(2026, 3, 5)) == "5 MAR 2026"
    cap = build_caption("Coders, your AI coding agent just got huge", "Summary here.", "AI coding agent", d, ["#a", "#b", "#c"])
    assert cap.splitlines()[0].startswith("Coders, your AI coding agent")
    assert "11 Nov 2026" in cap and "Comment LETS GO" in cap and cap.endswith("#a #b #c")


def test_hashtags_mix_and_lint():
    tags = pick_hashtags(["#Codex", "openai", "#ai coding agent", "#viral"], "news")
    assert tags[:3] == ["#codex", "#openai", "#aicodingagent"] and len(tags) == 5 and "#viral" not in tags
    good = build_caption("AI coding agent news", "Two sentences.", "AI coding agent", date(2026, 11, 11), tags)
    assert lint_caption(good, "AI coding agent") == []
    bad = "x" * 130 + "\nsee https://a.b\n#one"
    probs = lint_caption(bad, "AI coding agent")
    assert any("first line" in p for p in probs) and any("link" in p for p in probs) and any("hashtags" in p for p in probs)


def test_assemble_and_validate_clean_answer():
    sb = assemble(ANSWER, TOPIC, date(2026, 11, 11), "20261111-afternoon-test")
    assert sb.presenter.look == "vrm" and sb.voice.voice == "am_fenrir"
    assert sb.dmLink == "https://openai.com/index/example" and sb.scenes[-1].cta
    assert sb.scenes[0].data["kicker"].endswith("/ 11 NOV 2026")
    assert validate(sb, ANSWER, date(2026, 11, 11)) == []


def test_validate_catches_wrong_date_and_long_hook():
    answer = json.loads(json.dumps(ANSWER))
    answer["scenes"][0]["data"]["kicker"] = "OPENAI / 8 NOV 2026"
    answer["scenes"][0]["narration"] = " ".join(["word"] * 17) + "."
    sb = assemble(answer, TOPIC, date(2026, 11, 11), "x")
    probs = validate(sb, answer, date(2026, 11, 11))
    assert any("kicker" in p for p in probs) and any("hook over 16 words" in p for p in probs)


def test_reel_id_and_slots(monkeypatch):
    assert reel_id_for(TOPIC, date(2026, 11, 11), "night").startswith("20261111-night-openai-buys-pierre")
    monkeypatch.delenv("PUBLISH_SLOTS", raising=False)
    s = daily.slots()
    assert s["afternoon"]["publish"] == "15:30" and s["night"]["publish"] == "20:40"
    monkeypatch.setenv("PUBLISH_SLOTS", "afternoon=16:00,night=21:00")
    s = daily.slots()
    assert s["afternoon"]["publish"] == "16:00" and s["afternoon"]["produce"] == "13:30" and s["night"]["produce"] == "18:30"
    when = daily.slot_datetime("night", date(2026, 11, 11))
    assert (when.hour, when.minute, str(when.tzinfo)) == (21, 0, "Asia/Kolkata")


def test_pick_topic_prefers_slot_category_and_skips_used(tmp_path, monkeypatch):
    monkeypatch.setattr(daily.paths, "TOPICS", tmp_path)
    for name, cat, score, used in [("a", "research", 90, None), ("b", "news", 80, None), ("c", "news", 95, "2026-11-10T10:00:00+05:30")]:
        (tmp_path / f"{name}.json").write_text(json.dumps({"name": name, "category": cat, "score_total": score, "status": "used" if used else "candidate", "used_at": used, "sources": []}), encoding="utf-8")
    monkeypatch.delenv("PUBLISH_SLOTS", raising=False)
    assert daily.pick_topic("afternoon")["name"] == "b"  # news preferred over the higher-scoring research topic
    assert daily.pick_topic("night")["name"] == "a"


def test_task_command_points_at_the_venv_and_logs():
    cmd = daily.task_cmd("produce --slot afternoon", "produce-afternoon")
    assert cmd.endswith('reelforge-task.cmd" produce afternoon') and len(cmd) < 261
