"""The vendored instagram-agent-skill tools inside the pipeline: hook score, humanizer, caption lint."""
from __future__ import annotations

from services.story import polish


def test_hook_scorer_flags_greetings_and_rewards_specific_hooks():
    weak = polish.score_hook("Hey guys, welcome back to the channel, today we talk about AI.")
    assert weak["verdict"] == "WEAK" and weak["flags"]
    assert polish.hook_advice("Hey guys, welcome back to the channel.").startswith("hook has a dealbreaker")
    strong = polish.score_hook("Coders, DeepSeek just cut your $200 Cursor bill to zero.")
    assert strong["score"] > weak["score"] and strong["verdict"] in ("OK", "STRONG")
    advice = polish.hook_advice("Most people think an AI agent is just a chatbot with extra steps. It isn't.")
    assert advice is not None and "weakest" in advice


def test_weak_hook_is_pushed_back_but_never_blocks_the_last_attempt():
    import json
    from datetime import date

    from services.common.schema import Storyboard
    from services.story.generate import EXAMPLE, validate

    sb = Storyboard.model_validate(json.loads(EXAMPLE.read_text(encoding="utf-8")))
    sb.scenes[0].narration = "Most people think an AI agent is just a chatbot with extra steps. It isn't."
    answer = {"keyword": "AI agent", "hashtags": ["#a", "#b", "#c"]}
    strict = validate(sb, answer, date(2026, 11, 12), strict_hook=True)
    lax = validate(sb, answer, date(2026, 11, 12), strict_hook=False)
    assert any(p.startswith("hook scores") for p in strict) and not any(p.startswith("hook scores") for p in lax)


def test_hook_classifier_names_a_formula_or_none():
    assert polish.classify_hook("$18,000 is what one missing clause cost me.") == "The Cost"
    assert polish.classify_hook("") is None


def test_humanizer_strips_em_dashes_curly_quotes_and_slop():
    text = "Coders — this is a game-changer. It’s “insane”. Let’s delve into it."
    clean, info = polish.humanize_text(text)
    assert "—" not in clean and "“" not in clean and "’" not in clean
    assert info["changes"] >= 3
    assert polish.machine_artefacts("Plain text, no tricks.") == []
    assert polish.machine_artefacts(text)
    assert polish.humanize_text("")[0] == ""


def test_human_score_panel_runs():
    r = polish.human_score("Coders, be scared. DeepSeek just open-sourced your replacement. It runs your repo on its own, and 21 thousand people starred it in a week.")
    assert 0 <= r["score"] <= 100 and r["verdict"] in ("PASS", "REVIEW", "FLAGGED") and "SLOP DENSITY" in r["checks"]


def test_caption_lint_catches_hashtag_walls_and_links():
    bad = "Hey everyone! Check it out.\n\nhttps://example.com\n\n#ai #tech #news #viral #fyp #explore #reels"
    r = polish.lint_caption_feed(bad, ["ai coding agent"])
    assert r["verdict"] == "FIX" and any(c["check"] == "HASHTAGS" for c in r["fails"]) and len(r["hashtags"]) == 7
    good = "Coders, the AI coding agent just got huge. Codex is about to change your job.\n\nOpenAI bought the company behind it. Here is what it means for the AI coding agent race.\n\n12 Nov 2026 update.\n\nWant the link? Comment LETS GO and I'll DM it to you.\n\n#codex #openai #aicodingagent #ai #technews"
    r2 = polish.lint_caption_feed(good, ["ai coding agent"])
    assert r2["verdict"] in ("READY", "REVIEW") and not r2["fails"]


def test_rules_checker_reports_artefacts_and_hook_dealbreakers():
    import json
    from datetime import date

    from services.common.rules import check_storyboard, hook_report
    from services.common.schema import Storyboard
    from services.story.generate import EXAMPLE

    sb = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    base = Storyboard.model_validate(sb)
    assert not [p for p in check_storyboard(base) if "artefact" in p or "dealbreaker" in p]
    rep = hook_report(base)
    assert rep["hook_score"] is not None and "hook_advice" in rep
    broken = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    broken["scenes"][0]["narration"] = "Hey guys, welcome back — today we talk about agents."
    problems = check_storyboard(Storyboard.model_validate(broken))
    assert any("dealbreaker" in p for p in problems) and any("machine artefacts" in p for p in problems)


def test_five_hashtags_now():
    from services.publishing.seo import MAX_HASHTAGS, lint_caption, pick_hashtags

    assert MAX_HASHTAGS == 5
    tags = pick_hashtags(["#codex", "#openai", "#aicodingagent"])
    assert len(tags) == 5 and tags[:3] == ["#codex", "#openai", "#aicodingagent"]
    assert any("hashtags" in p for p in lint_caption("AI coding agent news.\n\nComment LETS GO.\n\n#a #b #c #d #e #f", "AI coding agent"))
