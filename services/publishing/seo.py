"""SEO for Instagram Reels: keyword-led caption, dated kicker, hashtag mix, and a lint.

Instagram ranks Reels by the words in the caption (the first ~125 characters
carry the most weight), on-screen text and audio, and by hashtags that match
search terms. The builder keeps the hook first, repeats the primary keyword
once naturally, states the posting date so time-sensitive searches match,
ends with the comment ask, and closes with 3 broad + 3 niche hashtags.
"""
from __future__ import annotations

import re
from datetime import date, datetime
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
MAX_CAPTION = 2200
MAX_HASHTAGS = 6
BROAD = ["#ai", "#artificialintelligence", "#technews", "#tech", "#coding", "#programming", "#aitools", "#machinelearning"]
BANNED = {"#followforfollow", "#like4like", "#f4f", "#l4l", "#viral", "#fyp"}


def kicker_date(d: date) -> str:
    """'11 NOV 2026' for the on-screen kicker."""
    return d.strftime("%d %b %Y").upper().lstrip("0")


def caption_date(d: date) -> str:
    return d.strftime("%d %b %Y").lstrip("0")


def normalise_tag(tag: str) -> str:
    t = tag.strip().lower()
    t = "#" + re.sub(r"[^a-z0-9_]", "", t.lstrip("#"))
    return t


def pick_hashtags(niche: list[str], category: str = "") -> list[str]:
    """3 niche tags from the topic plus broad tags to make 6, deduped, nothing banned."""
    out: list[str] = []
    for t in [normalise_tag(x) for x in niche] + BROAD:
        if len(t) > 2 and t not in out and t not in BANNED:
            out.append(t)
        if len(out) >= MAX_HASHTAGS:
            break
    return out


def build_caption(hook: str, summary: str, keyword: str, post_date: date, hashtags: list[str], keyword_phrase: str | None = None, dm_keyword: str = "LETS GO") -> str:
    """Hook (with the keyword) first, two-line summary, date line, the ask, tags."""
    hook = hook.strip().rstrip(".") + "."
    if keyword.lower() not in hook.lower():
        hook = f"{hook} {keyword_phrase or keyword}."
    lines = [hook, "", summary.strip(), "", f"{caption_date(post_date)} update. Save this for later.", "", f"Want the link? Comment {dm_keyword} and I'll DM it to you.", "", " ".join(hashtags)]
    cap = "\n".join(lines)
    return cap[:MAX_CAPTION]


def lint_caption(caption: str, keyword: str, dm_keyword: str = "LETS GO") -> list[str]:
    problems: list[str] = []
    first = caption.split("\n", 1)[0]
    if len(first) > 125:
        problems.append(f"first line is {len(first)} chars; keep it under 125 so it survives the '... more' cut")
    if keyword.lower() not in caption[:300].lower():
        problems.append(f"primary keyword {keyword!r} missing from the first 300 chars")
    tags = re.findall(r"#\w+", caption)
    if not 3 <= len(tags) <= MAX_HASHTAGS:
        problems.append(f"{len(tags)} hashtags; use 3 to {MAX_HASHTAGS}")
    if any(t.lower() in BANNED for t in tags):
        problems.append("banned engagement-bait hashtag present")
    if dm_keyword.lower() not in caption.lower():
        problems.append(f"caption never asks for the keyword {dm_keyword!r}")
    if re.search(r"https?://|www\.", caption):
        problems.append("caption contains a link; the DM bot sends it")
    if len(caption) > MAX_CAPTION:
        problems.append("caption over 2200 chars")
    return problems


def now_ist() -> datetime:
    return datetime.now(IST)
