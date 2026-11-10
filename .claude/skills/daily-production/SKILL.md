---
name: daily-production
description: Run the REELFORGE daily batch of three reels (news/discovery, educational explainer, tool/repo/breakthrough): select distinct topics, script, storyboard, voiceover, render, QA and export packages, one resource-heavy stage at a time, with manual approval before publishing. Use when the user says "run today's batch", "make today's reels", "daily production".
---

# daily-production

## Purpose
Produce three publication-ready packages per day within the hardware limits, without sacrificing accuracy.

## Trigger
A user request, or the Windows Task Scheduler entry (Phase 8) firing `services/scheduler/daily.py`.

## Inputs
- Topic queue (`data/topics/`), style rotation (reel 1 cinematic, reel 2 explainer, reel 3 either), render target preference (Ada if reachable, else local draft then standard).

## Outputs
- `output/approved/<date>/<id>/` with MP4, cover, caption.txt, hashtags.txt, checklist.md, plus `output/reports/<date>.production.md`.

## Unattended mode (since 2026-11-11)
`services/scheduler/daily.py` runs the whole chain without an agent in the loop: `produce --slot afternoon|night` (research, pick topic, LLM script+storyboard via `services/story/generate.py`, voiceover, 1080p render, cover, QA, package, queue) and `publish --slot ...` (host, container, wait for the slot minute, publish, DM bot). Windows tasks: `install-tasks`. Two slots a day: 15:30 and 20:40 IST. See docs/AUTOMATION.md. The steps below describe the same chain when an agent drives it by hand.

## Workflow
1. `research-topic` -> pick three with different categories and no overlap with the last 30 days.
2. For each: `generate-script` -> `create-storyboard` -> `generate-voiceover` -> `render-reel` (draft) -> `review-reel` -> fix -> `render-reel` (standard or cinematic) -> `review-reel` -> export package.
3. Resource gating: one TTS job, one render, one post-process at a time locally; Ada jobs one per reel; check free RAM before every local Chrome launch.
4. Retries: a failed stage retries once with the same inputs, then once with the fallback (Ada -> local draft, Kokoro CUDA -> CPU). After that the reel is marked failed in the report and production continues with the others.
5. End with the report: what was produced, render times, QA scores, what failed and why.

## Validation
Three distinct topics, each with a passed QA report, each package complete. Fewer than three is acceptable when quality would otherwise drop; say so in the report.

## Error handling
- By hand: never publish without approval. In unattended mode the scheduled `publish` step posts on its own when `AUTOPUBLISH=true` and QA passed; `AUTOPUBLISH=false` pauses it. After a post, run the DM bot so `LETS GO` comments get the link: `python -m services.publishing.dm_bot --send --watch` (dry run first; see docs/PUBLISHING.md "Comment-to-DM link gate").
- Every reel uses the house format: animated male host (long hair, black hoodie), male English voice `am_fenrir`, anxious-funny 5 s hook, no link on screen or in the caption, keyword CTA. `services.common.rules` must pass before voiceover.
- Never reuse a topic slug used in the last 30 days.

## Dependencies
All other project skills; SQLite job table (Phase 6+).
