"""Niche watch evidence step: yt-dlp listings -> collect.py -> swipe.py -> evidence markdown (no network)."""
from __future__ import annotations

import json
from pathlib import Path

from services.analytics import niche


def _listing(handle: str, views: list[int], titles: list[str] | None = None) -> dict:
    entries = []
    for i, v in enumerate(views):
        entries.append({"id": f"{handle}{i}", "title": (titles[i] if titles else f"{handle} short {i}"), "view_count": v,
                        "url": f"https://www.youtube.com/shorts/{handle}{i}", "duration": 30, "channel": handle, "uploader_id": handle})
    return {"entries": entries, "channel": handle}


def test_channels_file_is_seeded_and_parsed(tmp_path):
    f = tmp_path / "niche_channels.txt"
    got = niche.channels(f)
    assert got == niche.DEFAULT_CHANNELS and f.exists()
    f.write_text("# comment\nmreflow\n@WesRoth\n\n", encoding="utf-8")
    assert niche.channels(f) == ["@mreflow", "@WesRoth"]


def test_evidence_ranks_outliers_by_own_channel_median(tmp_path, monkeypatch):
    raw = tmp_path / "raw"
    posts = tmp_path / "posts.json"
    out = tmp_path / "evidence.md"
    monkeypatch.setattr(niche, "POSTS_FILE", posts)
    monkeypatch.setattr(niche, "ytdlp_available", lambda: True)
    listings = {
        # a small channel whose one video did 10x its median beats a big channel's normal day
        "@small": _listing("@small", [1000, 1200, 900, 1100, 11000], ["a", "b", "c", "d", "Coders, DeepSeek just cut your $200 Cursor bill to zero."]),
        "@big": _listing("@big", [400000, 420000, 390000, 410000, 405000]),
        "@dead": None,
    }
    summary = niche.evidence(handles=list(listings), raw_dir=raw, out_file=out, fetch=lambda h: listings[h])
    assert summary["ok"] and summary["failed"] == ["@dead"] and summary["channels"] == 2 and summary["posts"] == 10
    assert summary["outliers"] == 1 and summary["top"][0][1] == "@small" and summary["top"][0][0] >= 9
    text = out.read_text(encoding="utf-8")
    assert "Niche evidence" in text and "@small" in text and "Cursor bill" in text and "| 11,000 |" in text
    assert "Formulas among the outliers" in text and "Median hook score" in text
    assert json.loads(posts.read_text(encoding="utf-8"))  # normalised list written


def test_evidence_without_ytdlp_or_channels_is_a_soft_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(niche, "ytdlp_available", lambda: False)
    assert niche.evidence(handles=["@x"], raw_dir=tmp_path, out_file=tmp_path / "e.md")["ok"] is False
    monkeypatch.setattr(niche, "ytdlp_available", lambda: True)
    s = niche.evidence(handles=["@x"], raw_dir=tmp_path, out_file=tmp_path / "e.md", fetch=lambda h: None)
    assert s["ok"] is False and s["failed"] == ["@x"] and not (tmp_path / "e.md").exists()


def test_evidence_markdown_handles_no_outliers():
    text = niche.evidence_markdown({"outliers": [], "posts": 8, "channels": 2, "skipped_thin_channels": [["@thin", 2]]}, when="2026-11-12")
    assert "No outliers this week" in text and "8 recent Shorts across 2" in text


def test_prompt_mentions_evidence_only_when_the_file_exists(tmp_path, monkeypatch):
    calls = {}

    def fake_run(cmd, input, **kw):
        calls["prompt"] = input

        class R:
            returncode = 1
            stdout = ""
            stderr = "offline"
        return R()

    monkeypatch.setattr(niche.subprocess, "run", fake_run)
    monkeypatch.setattr(niche, "claude_executable", lambda: "claude")
    monkeypatch.setattr(niche.paths, "OUTPUT", tmp_path)
    out = tmp_path / "data" / "analytics" / "niche.md"
    ev = tmp_path / "data" / "analytics" / "niche_evidence.md"
    monkeypatch.setattr(niche.paths, "ROOT", tmp_path)
    assert niche.swipe(out_path=out, evidence_path=ev)["evidence_used"] is False and "FIRST read" not in calls["prompt"]
    ev.parent.mkdir(parents=True, exist_ok=True)
    ev.write_text("# Niche evidence", encoding="utf-8")
    assert niche.swipe(out_path=out, evidence_path=ev)["evidence_used"] is True and "FIRST read data/analytics/niche_evidence.md" in calls["prompt"]
