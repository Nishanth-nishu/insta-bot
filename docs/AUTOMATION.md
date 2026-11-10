# Daily automation: two reels a day, posted on the minute

Since 2026-11-11 REELFORGE runs unattended. Every day it produces and posts two reels in the house format (anime VRM host, 5-second hook, no link on screen, LETS GO comment gate) with SEO captions that carry the posting date.

| Slot | Produce starts (IST) | Posts (IST) | Topic preference |
|---|---|---|---|
| afternoon | 13:00 | 15:30 | news, tools, breakthroughs |
| night | 18:10 | 20:40 | research, explainers |

Change the times with `PUBLISH_SLOTS=afternoon=15:30,night=20:40` in `.env` (production starts 150 minutes before each post) and re-run `install-tasks`.

## What one slot does

`python -m services.scheduler.daily produce --slot afternoon`

1. Research: `services.research.discover --save` refreshes the topic candidates (arXiv, Hugging Face, GitHub trending, official blogs, RSS). Offline it falls back to the existing queue.
2. Topic: the highest-scoring unused candidate, preferring the slot's categories, never a name used in the last 30 days. The topic file is marked `used`.
3. Script and storyboard: `services/story/generate.py` asks the LLM for scenes, title, search keyword, caption summary and three niche hashtags in one JSON answer, assembles the storyboard with the house defaults, validates it (schema, `services.common.rules`, caption lint) and retries with the problems fed back. The hook scene's kicker ends with the posting date (`AI NEWS / 11 NOV 2026`).
4. Voiceover and timing: `services.tts.synthesize` (Sarvam or Kokoro per `VOICE_PROVIDER`), plus the mouth envelope and the expression track.
5. Render: `services.rendering.local_render` at 1080p, one Chrome tab, plus the cover still.
6. QA: `services.quality.technical` must pass; a failure stops the slot and nothing is posted.
7. Package: `output/approved/<date>/<id>/` with `scheduled_for`, and a queue entry in `data/database/schedule.json`.

`python -m services.scheduler.daily publish --slot afternoon` (scheduled four minutes before the slot)

Hosts the MP4, creates the Instagram container, waits for the exact slot minute, publishes, records the permalink, then runs the DM bot once for that post. `AUTOPUBLISH=true` in `.env` is required; otherwise it dry-runs. `--now` skips the wait, `--dry-run` never posts.

`python -m services.scheduler.daily dm` runs every 30 minutes and answers new LETS GO comments (needs the messaging permission; see PUBLISHING.md).

## SEO

`services/publishing/seo.py` builds the caption: the hook with the search keyword in the first line (under 125 characters, the part Instagram shows before "more"), a two-sentence summary that repeats the keyword once, the posting date ("11 Nov 2026 update"), the LETS GO ask, then six hashtags: three niche ones from the LLM plus broad ones (`#ai`, `#technews`, ...). Engagement-bait tags are banned. `lint_caption` enforces all of it before packaging. The on-screen kicker carries the same date so time-sensitive searches match the spoken and written text.

## LLM

`services/llm/client.py` picks `LLM_PROVIDER`: `sarvam` (default here; `sarvam-105b-conversations` answers in about a second, `sarvam-105b` reasons first and is the last-attempt fallback), `anthropic` (set `ANTHROPIC_API_KEY`; uses `claude-opus-5-5`), or `claude-cli` (Claude Code installed and logged in; runs `claude -p` on the subscription, no API cost).

## Free fallbacks when Sarvam runs out

Both paid calls fall back to open-source tools on a quota, credit, auth, rate-limit or outage error (HTTP 401/402/403/429/5xx or a network failure); a plain validation error does not trigger a fallback.

- Voice: `TTS_FALLBACK=kokoro`. `synthesize_storyboard` restarts the whole reel on Kokoro (`am_fenrir`) so one voice carries the reel; cache keys include the provider, so nothing paid is re-spent.
- Script: `LLM_FALLBACKS=ollama`. `services/llm/client.py` tries the primary, then each fallback in order. The Ollama provider talks to a local server (`OLLAMA_URL`), uses `OLLAMA_MODEL` or any pulled model, and forces JSON output. Install once with `winget install Ollama.Ollama`, then `ollama pull qwen2.5:7b-instruct` (about 4.7 GB) and optionally `qwen2.5:3b-instruct` for a faster, weaker option. On this laptop's CPU a storyboard takes a few minutes with the 7B model, which fits inside the 150-minute production window.
- The log line `llm: ollama used instead of sarvam` marks every fallback, so a drained Sarvam balance is visible in `output/logs/`.

## Scheduling on this machine

```
.venv\Scripts\python -m services.scheduler.daily install-tasks   # registers REELFORGE\produce-*, publish-*, dm-bot
.venv\Scripts\python -m services.scheduler.daily status          # the queue
.venv\Scripts\python -m services.scheduler.daily remove-tasks
```

The tasks run as the logged-in user with the venv Python, logging to `output/logs/<task>.log`. The laptop must be on and awake at the slot times (the render needs about 15 minutes at 1080p and the Instagram upload a few more).

## Safety

Nothing is posted without a passed QA report and a passed rules check. A failed slot leaves a log and no post. Set `AUTOPUBLISH=false` to pause posting without touching the schedule.
