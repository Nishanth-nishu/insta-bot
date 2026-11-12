"""The reel queue: FIFO publishing, retries that keep the reel, re-dating of late reels, backlog cap."""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from services.common import paths
from services.scheduler import daily

SLOTS = {"afternoon": {"publish": "15:30", "produce": "13:00", "prefer": []}, "night": {"publish": "20:40", "produce": "18:10", "prefer": []}}


@pytest.fixture
def queue(tmp_path, monkeypatch):
    q = tmp_path / "schedule.json"
    monkeypatch.setattr(daily, "QUEUE", q)
    monkeypatch.setattr(daily, "slots", lambda: SLOTS)
    monkeypatch.setattr(daily, "load_dotenv", lambda: None)
    return q


def _entry(d: str, slot: str, status: str = "ready", **extra) -> dict:
    return {"date": d, "slot": slot, "reel_id": f"{d.replace('-', '')}-{slot}-x", "package": f"C:/pkg/{d}-{slot}", "scheduled_for": f"{d}T00:00", "status": status, **extra}


def test_next_ready_prefers_own_slot_then_oldest_ready(queue):
    q = [_entry("2026-11-11", "night"), _entry("2026-11-11", "afternoon", "published"), _entry("2026-11-12", "afternoon"), _entry("2026-11-12", "night")]
    # strict FIFO: the 11th night reel missed its slot, so it goes out first even though the 12th has its own reel
    assert daily.next_ready("afternoon", date(2026, 11, 12), q)["reel_id"].endswith("11-night-x")
    assert daily.next_ready("afternoon", date(2026, 11, 13), q)["date"] == "2026-11-11"
    # an already published own slot is reported as such, never re-posted
    assert daily.next_ready("afternoon", date(2026, 11, 11), q)["status"] == "published"
    assert daily.next_ready("night", date(2026, 11, 14), [_entry("2026-11-11", "afternoon", "published")]) is None
    assert [e["slot"] for e in daily.ready_entries(q)] == ["night", "afternoon", "night"]
    assert not daily.backlog_full(q)
    monkey = q + [_entry("2026-11-13", "afternoon")]
    assert daily.backlog_full(monkey)  # 4 ready reels


def test_failed_publish_keeps_the_reel_queued_and_counts_attempts(queue, monkeypatch):
    daily.save_queue([_entry("2026-11-12", "night")])
    monkeypatch.setenv("AUTOPUBLISH", "true")
    calls = []

    def boom(package_dir, confirm, publish_at=None):
        calls.append(str(package_dir))
        raise RuntimeError("container creation failed: API access blocked.")

    import services.publishing.instagram as ig
    monkeypatch.setattr(ig, "publish_package", boom)
    with pytest.raises(RuntimeError):
        daily.publish("night", date(2026, 11, 12), now=True, dry_run=False)
    e = daily.load_queue()[0]
    assert e["status"] == "ready" and e["attempts"] == 1 and "API access blocked" in e["last_error"]
    with pytest.raises(RuntimeError):
        daily.publish("night", date(2026, 11, 12), now=True, dry_run=False)
    assert daily.load_queue()[0]["attempts"] == 2 and len(calls) == 2


def test_late_reel_is_redated_then_posted_and_marked(queue, monkeypatch):
    daily.save_queue([_entry("2026-11-11", "night")])
    monkeypatch.setenv("AUTOPUBLISH", "true")
    redated = []
    monkeypatch.setattr(daily, "redate_entry", lambda entry, post_date, **kw: redated.append(post_date) or entry)
    import services.publishing.instagram as ig
    monkeypatch.setattr(ig, "publish_package", lambda package_dir, confirm, publish_at=None: {"permalink": "https://instagram.com/reel/abc", "media_id": "m1"})
    import services.publishing.dm_bot as dm
    monkeypatch.setattr(dm, "run_once", lambda **kw: {"sent": 0})
    out = daily.publish("afternoon", date(2026, 11, 12), now=True, dry_run=False)
    assert redated == [date(2026, 11, 12)]
    assert out["status"] == "published" and out["posted_as"] == "2026-11-12" and out["posted_slot"] == "afternoon" and out["media_id"] == "m1"
    assert daily.load_queue()[0]["status"] == "published" and not daily.ready_entries()


def test_redate_moves_every_date_and_survives_a_failed_render(queue, tmp_path, monkeypatch):
    sb_dir = tmp_path / "storyboards"
    sb_dir.mkdir()
    monkeypatch.setattr(paths, "STORYBOARDS", sb_dir)
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    rid = "20261111-night-x"
    spec = {"id": rid, "title": "T", "style": "cinematic", "scenes": [{"kind": "hook", "title": "T", "data": {"kicker": "PYRITE / 11 NOV 2026"}, "highlight": []}], "caption": "Hook.\n\n11 Nov 2026 update. Save this for later."}
    (sb_dir / f"{rid}.timed.json").write_text(json.dumps(spec), encoding="utf-8")
    (sb_dir / f"{rid}.json").write_text(json.dumps(spec), encoding="utf-8")
    (pkg / "caption.txt").write_text("Hook.\n\n11 Nov 2026 update. Save this for later.\n\n#ai", encoding="utf-8")
    (pkg / "package.json").write_text(json.dumps({"id": rid, "video": f"{rid}.mp4", "scheduled_for": "2026-11-11T20:40:00+05:30"}), encoding="utf-8")
    entry = _entry("2026-11-11", "night")
    entry["reel_id"], entry["package"] = rid, str(pkg)
    daily.save_queue([entry])

    def failing_render(spec_path, reel_id, profile):
        raise RuntimeError("render failed: out of memory")

    monkeypatch.setattr(daily, "render_spec", failing_render)
    out = daily.redate_entry(entry, date(2026, 11, 12))
    timed = json.loads((sb_dir / f"{rid}.timed.json").read_text(encoding="utf-8"))
    assert timed["scenes"][0]["data"]["kicker"] == "PYRITE / 12 NOV 2026" and "12 Nov 2026 update" in timed["caption"]
    assert "12 Nov 2026 update" in (pkg / "caption.txt").read_text(encoding="utf-8")
    meta = json.loads((pkg / "package.json").read_text(encoding="utf-8"))
    assert meta["produced_for"] == "2026-11-11" and meta["scheduled_for"].startswith("2026-11-12T20:40")
    assert out["redated_to"] == "2026-11-12" and out["rerendered"] is False and "out of memory" in out["redate_error"]
    assert daily.load_queue()[0]["redated_to"] == "2026-11-12"
    # same day: nothing to do
    assert daily.redate_entry(_entry("2026-11-12", "night"), date(2026, 11, 12)) == _entry("2026-11-12", "night")


def test_redate_copies_the_new_render_into_the_package(queue, tmp_path, monkeypatch):
    sb_dir = tmp_path / "storyboards"
    sb_dir.mkdir()
    monkeypatch.setattr(paths, "STORYBOARDS", sb_dir)
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    rid = "20261111-night-y"
    (sb_dir / f"{rid}.timed.json").write_text(json.dumps({"id": rid, "scenes": [{"data": {"kicker": "X / 11 NOV 2026"}}], "caption": "11 Nov 2026 update."}), encoding="utf-8")
    (pkg / "package.json").write_text(json.dumps({"id": rid, "video": f"{rid}.mp4"}), encoding="utf-8")
    (pkg / f"{rid}.mp4").write_bytes(b"old")
    new_video, new_cover = tmp_path / "new.mp4", tmp_path / "new.png"
    new_video.write_bytes(b"new")
    new_cover.write_bytes(b"png")
    monkeypatch.setattr(daily, "render_spec", lambda spec_path, reel_id, profile: (new_video, new_cover, tmp_path / "qa.json"))
    entry = _entry("2026-11-11", "night")
    entry["reel_id"], entry["package"] = rid, str(pkg)
    out = daily.redate_entry(entry, date(2026, 11, 12))
    assert out["rerendered"] is True
    assert (pkg / f"{rid}.mp4").read_bytes() == b"new" and (pkg / "cover.png").read_bytes() == b"png"
