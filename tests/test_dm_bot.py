import json
from pathlib import Path

import pytest

from services.publishing import dm_bot
from services.publishing.dm_bot import format_message, is_lets_go, link_for_package, run_once


@pytest.mark.parametrize("text", [
    "lets go", "let's go", "letsgo", "lets gooo", "LET'S GOOOO", "LET'S GOOO \U0001F525\U0001F525",
    "\U0001F525 lets go \U0001F525", "ok lets go bro", "Lets Go!!!", "let’s go", "yes! LETS GOOOOO!!!", "let s go",
])
def test_matcher_positives(text):
    assert is_lets_go(text)


@pytest.mark.parametrize("text", [
    "let go", "let it go", "nice reel", "", None, "lets gone", "lets goal", "outlets going", "let's", "go go go", "letters go",
])
def test_matcher_negatives(text):
    assert not is_lets_go(text)


def test_link_precedence_dm_link_over_sources(tmp_path):
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "sources.txt").write_text("\nhttps://arxiv.org/abs/1\nhttps://other\n", encoding="utf-8")
    assert link_for_package(pkg) == "https://arxiv.org/abs/1"
    (pkg / "package.json").write_text(json.dumps({"id": "x", "dm_link": "https://my.link/x"}), encoding="utf-8")
    assert link_for_package(pkg) == "https://my.link/x"
    (pkg / "package.json").write_text(json.dumps({"id": "x", "dm_link": "  "}), encoding="utf-8")
    assert link_for_package(pkg) == "https://arxiv.org/abs/1"


def test_link_none_when_neither(tmp_path):
    pkg = tmp_path / "empty"
    pkg.mkdir()
    (pkg / "package.json").write_text(json.dumps({"id": "x"}), encoding="utf-8")
    assert link_for_package(pkg) is None
    assert link_for_package(tmp_path / "missing") is None


def test_format_message_substitutes_link_and_newlines():
    assert format_message(dm_bot.DEFAULT_MESSAGE, "https://a.b") == "Here is the link: https://a.b\n\nIf the reel helped, a follow keeps them coming. \U0001F525"
    assert format_message("Link: {link}\\nbye", "https://a.b") == "Link: https://a.b\nbye"
    assert "{other}" in format_message("{link} {other}", "x")


def _approved(tmp_path: Path) -> Path:
    root = tmp_path / "approved"
    pkg = root / "2026-11-08" / "reel-a"
    pkg.mkdir(parents=True)
    (pkg / "package.json").write_text(json.dumps({"id": "reel-a", "status": "published", "media_id": "111"}), encoding="utf-8")
    (pkg / "sources.txt").write_text("https://link.example/a\n", encoding="utf-8")
    other = root / "2026-11-05" / "draft"
    other.mkdir(parents=True)
    (other / "package.json").write_text(json.dumps({"id": "draft", "status": "ready_for_manual_upload"}), encoding="utf-8")
    return root


COMMENTS = [
    {"id": "c1", "text": "LETS GOOO", "username": "alice", "from": {"id": "1", "username": "alice"}},
    {"id": "c2", "text": "nice", "username": "bob", "from": {"id": "2", "username": "bob"}},
    {"id": "c3", "text": "let's go \U0001F525", "username": "carol", "from": {"id": "3", "username": "carol"}},
    {"id": "c4", "text": "lets go", "username": "me_account", "from": {"id": "999", "username": "me_account"}},
]


def test_dry_run_lists_matches_and_sends_nothing(tmp_path):
    state = tmp_path / "dm_bot.json"
    calls = []
    s = run_once(send=False, approved_root=_approved(tmp_path), state_path=state, fetch=lambda mid: COMMENTS,
                 send_private=lambda cid, text: calls.append(cid) or {"ok": True}, own={"999", "me_account"}, token="", ig_user="999")
    assert s["mode"] == "DRY RUN"
    assert s["scanned"] == 4 and s["matched"] == 3 and s["skipped_own"] == 1 and s["sent"] == 0
    assert [e["username"] for e in s["would_dm"]] == ["alice", "carol"]
    assert calls == [] and not state.exists()


def test_send_then_rerun_dedupes_via_state_file(tmp_path):
    state = tmp_path / "dm_bot.json"
    root = _approved(tmp_path)
    sent: list[tuple[str, str]] = []
    public: list[str] = []

    def fake_private(cid, text):
        sent.append((cid, text))
        if cid == "c3":
            return {"ok": False, "host": "https://graph.instagram.com/v21.0", "error": "(#10) Application does not have permission", "code": 10, "subcode": 2534022, "permission": True}
        return {"ok": True, "host": "https://graph.facebook.com/v21.0", "message_id": "m1", "error": None}

    common = dict(send=True, public_reply=True, approved_root=root, state_path=state, fetch=lambda mid: COMMENTS,
                  send_private=fake_private, send_public=lambda cid, text: public.append(cid) or {"ok": True, "id": "r1"},
                  own={"999", "me_account"}, token="", ig_user="999")
    first = run_once(**common)
    assert first["sent"] == 1 and first["failed"] == 1 and first["skipped_already"] == 0
    assert sent == [("c1", "Here is the link: https://link.example/a\n\nIf the reel helped, a follow keeps them coming. \U0001F525"), ("c3", sent[1][1])]
    assert public == ["c1"]
    assert first["failures"][0]["comment_id"] == "c3"
    assert "App Review" in first["permission_hint"]
    saved = json.loads(state.read_text(encoding="utf-8"))["handled"]
    assert saved["c1"]["ok"] is True and saved["c1"]["host"] == "https://graph.facebook.com/v21.0" and saved["c1"]["username"] == "alice"
    assert saved["c3"]["ok"] is False and "permission" in saved["c3"]["error"]

    queued = json.loads(state.read_text(encoding="utf-8"))["queue"]
    assert list(queued) == ["c3"] and queued["c3"]["attempts"] == 1 and "permission" in queued["c3"]["last_error"]
    assert first["failures"][0]["retry_in_s"] == 120

    # second run right away: c1 is never DMed again; c3 waits for its backoff, so nothing is sent.
    sent.clear()
    public.clear()
    second = run_once(**common)
    assert second["skipped_already"] == 1 and second["pending"] == 1 and sent == []

    # third run after the backoff: c3 is retried, succeeds, and leaves the queue.
    import time as _t

    def ok_private(cid, text):
        sent.append((cid, text))
        return {"ok": True, "host": "https://graph.facebook.com/v21.0", "message_id": "m2", "error": None}

    third = run_once(**{**common, "send_private": ok_private}, now=_t.time() + 200)
    assert [cid for cid, _ in sent] == ["c3"] and third["sent"] == 1 and third["retried"] == 1
    saved = json.loads(state.read_text(encoding="utf-8"))
    assert saved["queue"] == {} and saved["handled"]["c3"]["ok"] is True and saved["handled"]["c3"]["attempts"] == 2
    assert public == ["c3"]


def test_queue_gives_up_after_seven_days(tmp_path):
    from services.publishing import dm_bot as d
    state = {"handled": {}, "queue": {"old": {"media_id": "m", "username": "zed", "message": "x", "attempts": 3,
                                              "next_at": 0, "enqueued_at": 1000.0, "last_error": "boom"}}}
    summary = {"sent": 0, "retried": 0, "pending": 0, "dropped": 0, "failed": 0, "sent_to": [], "failures": [], "permission_hint": None}
    calls = []
    d.process_queue(state, lambda cid, text: calls.append(cid) or {"ok": True}, None, "", False, summary, now=1000.0 + d.MAX_AGE_S + 1)
    assert calls == [] and summary["dropped"] == 1 and state["queue"] == {}
    assert state["handled"]["old"]["dropped"] is True and "gave up" in state["handled"]["old"]["error"]


def test_media_without_link_is_reported_not_fatal(tmp_path):
    root = _approved(tmp_path)
    (root / "2026-11-08" / "reel-a" / "sources.txt").unlink()
    s = run_once(send=False, approved_root=root, state_path=tmp_path / "s.json", fetch=lambda mid: COMMENTS, own=set(), token="", ig_user="999")
    assert s["scanned"] == 0 and s["media_errors"] and "no link" in s["media_errors"][0]["error"]


def test_fetch_error_does_not_crash_loop(tmp_path):
    def boom(mid):
        raise RuntimeError("token SECRET expired")
    s = run_once(send=False, approved_root=_approved(tmp_path), state_path=tmp_path / "s.json", fetch=boom, own=set(), token="SECRET", ig_user="999")
    assert s["media_errors"][0]["error"] == "token *** expired"
