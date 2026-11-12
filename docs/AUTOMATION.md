# Daily automation: two reels a day, posted on the minute

Since 2026-11-11 REELFORGE runs unattended. Every day it produces and posts two reels in the house format (anime VRM host, 5-second hook, no link on screen, LETS GO comment gate) with SEO captions that carry the posting date.

| Slot | Produce starts (IST) | Posts (IST) | Topic preference |
|---|---|---|---|
| afternoon | 13:00 | 15:30 | news, tools, breakthroughs |
| night | 18:10 | 20:40 | research, explainers |

Two more tasks close the loop: `analytics` (daily 12:30 IST) collects Instagram insights for every posted reel and rewrites `data/analytics/brief.md` and `weights.json`; `niche-watch` (Mondays 12:00 IST) writes a swipe file of what is working in the niche. Every produce run re-learns from the latest numbers before picking the topic, and the script writer reads the brief. See ANALYTICS.md.

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

`python -m services.scheduler.daily dm` runs every 5 minutes: it queues every new LETS GO comment, delivers the queue, and keeps retrying a failed DM with a growing wait for up to seven days (see the delivery queue in PUBLISHING.md). `status` also prints how many DMs were delivered and how many are still waiting.

## The reel queue

`data/database/schedule.json` is a real queue, not just a log (since 2026-11-12, after a slept-through afternoon and an API block left reels unposted):

- Every produced reel is an entry with status `ready`. A publish slot posts the **oldest ready reel**, whatever day it was made for. A reel that missed its day goes out at the very next slot; today's reel then waits one slot. Nothing is ever lost.
- A failed publish (API blocked, network, upload error) keeps the entry `ready` and records `attempts`, `last_error` and `last_attempt_at`; the next slot retries it. The task still exits non-zero so the failure shows in the log.
- A reel posted on a later day than it was made for is **re-dated** first: the on-screen kicker (`11 NOV 2026` to `12 NOV 2026`), the caption's date line and the package metadata move to the real posting date, and the video and cover are re-rendered (about 3 to 15 minutes, so a late post lands a few minutes after the slot). If the re-render fails the re-dated caption still goes out with the old video, and the entry records `redate_error`.
- Produce skips when the queue already holds `PRODUCE_BACKLOG_MAX` ready reels (default 4, two days), so a long API outage does not pile up stale reels or burn Sarvam credit.
- `status` prints each entry with its attempts and the queue head: `reel queue: 1 ready (max 4); next to post: ...`.

## SEO

`services/publishing/seo.py` builds the caption: the hook with the search keyword in the first line (under 125 characters, the part Instagram shows before "more"), a two-sentence summary that repeats the keyword once, the posting date ("11 Nov 2026 update"), the LETS GO ask, then five hashtags (Instagram's cap per post since 18 Dec 2025): three niche ones from the LLM plus two broad ones (`#ai`, `#technews`, ...). Engagement-bait tags are banned. `lint_caption` enforces all of it before packaging, and the vendored instagram-agent-skill tools (see below) add a feed-view lint, a hook score and a machine-artefact strip. The on-screen kicker carries the same date so time-sensitive searches match the spoken and written text.

## Text quality tools from instagram-agent-skill

Six scripts from the MIT-licensed instagram-agent-skill pack (Jakeschincariol/instagram-agent-skill) are vendored unmodified under `third_party/instagram-agent-skill/` and wrapped by `services/story/polish.py`, so the unattended pipeline gets what the interactive ig-reel, ig-caption and ig-human skills do by hand:

- Hook score (`hookscore.py`): five heuristics on the hook line (length, specificity, stakes, front-loading, address). Greetings and preambles are dealbreakers and fail the rules checker. A WEAK score (under 50) is sent back to the writer as a concrete fix on every attempt but the last, so it raises hooks without ever blocking a slot. The rules CLI prints `hook_score`, `hook_verdict`, `hook_formula` (which of the pack's 26 formulas it matches) and `hook_advice`; the feedback loop records the score and formula per reel and reports retention by both, so the data can show whether the score predicts anything on this account.
- Humanizer (`humanize.py` + `slop.json`): em dashes, curly quotes, invisible characters and slop words are stripped from every narration line and the caption on the one-shot path, and reported as "machine artefacts" by the rules checker so the headless Claude producer fixes them itself. The caption template lost "Save this for later" because the pack lists it as filler.
- Caption feed lint (`caption.py`): what the feed shows before "... more", the five-hashtag cap, links, hashtag walls and openers on a hashtag; a FAIL is a rules problem.
- `detect.py` (the five-check human-score panel) is available as `polish.human_score` for reports.
- `hookscore.py` and `hooks.json` are the newer versions from Jakeschincariol/master-skills (`skills/virality/tools`, MIT): same five checks, plus multi-pattern formula matching and the payoff rule (a money figure or number inside the first 8 words, reported as LEADS, LATE or MISSING). A WEAK hook's advice now says when the figure is missing or comes too late. The rest of master-skills was not taken: its `edit` skill is for raw talking-head footage (we generate ours; our render already does a two-pass loudness normalisation to -16 LUFS and QA checks it), `build` is a website builder. Its YouTube-counts collector and outlier ranker (`collect.py`, `swipe.py`) were vendored too and now feed the weekly niche watch with real public Shorts counts (`yt-dlp`, see ANALYTICS.md).

## What was taken from video-production-skills

Jakeschincariol/video-production-skills is a different render engine (HyperFrames: HTML and GSAP compositions rendered from the DOM) with no license file, so none of its code is vendored and the Remotion engine stays. Three things carried over as ideas: the music bed now ducks under the voice (`MusicBed` in `video-engine/src/compositions/Reel.tsx` reads the presenter's per-frame mouth envelope and pulls the bed down by up to 6 dB while the host speaks, back up in the gaps), the hook gets an impact on its first frame like the payoff already did, and its motion and composition guidance is distilled in `.claude/skills/create-cinematic-reel/motion-principles.md`, which the scene-building and review skills now read. Our own CC0 sound kit (`.claude/skills/sound-effects`) already covers the effects its bundled Pixabay set would add.

## Who writes the script

`LLM_PROVIDER=claude-cli` (the default here) schedules this chat: the produce step launches a headless Claude Code session (`claude -p`) inside the repo with a small tool allowlist (read, search, write the storyboard, and a Bash rule limited to the rules checker; no permission bypass, anything else is refused). It reads the generate-script and create-storyboard skills and the reference storyboard, writes `data/storyboards/<id>.json`, runs the rules checker on it and fixes it until it passes, then exits. It runs on the Claude subscription, no API tokens. One-time setup: `cd tools\claude-cli && npm install` (the folder's `package.json` pins the CLI and allows its install script; the installed files are git-ignored), then `tools\claude-cli\node_modules\.bin\claude auth login` once (browser sign-in; the login lives in `C:\Users\<you>\.claude`). The CLI is installed inside the project on purpose: the Claude desktop app is a packaged app whose `%APPDATA%` is virtualized, so a global `npm install -g` run from a session inside the app lands in the app's private folder and Task Scheduler never sees it (that is why the first two scheduled runs on 2026-11-11 fell back to Sarvam). `services/llm/client.py` looks in `tools/claude-cli` first, then PATH, then `%APPDATA%\npm`. If the CLI is missing, not logged in, times out (25 minutes) or produces an invalid file, the same step falls back to the one-shot providers in `LLM_FALLBACKS` (`sarvam`, then `ollama`): `sarvam-105b-conversations` answers in about a second; `ollama` is the local open-source model. `anthropic` (set `ANTHROPIC_API_KEY`; `claude-opus-5-5`) is also available.

## Free fallbacks when Sarvam runs out

Both paid calls fall back to open-source tools on a quota, credit, auth, rate-limit or outage error (HTTP 401/402/403/429/5xx or a network failure); a plain validation error does not trigger a fallback.

- Voice: `TTS_FALLBACK=kokoro`. `synthesize_storyboard` restarts the whole reel on Kokoro (`am_fenrir`) so one voice carries the reel; cache keys include the provider, so nothing paid is re-spent.
- Script: `LLM_FALLBACKS=ollama`. `services/llm/client.py` tries the primary, then each fallback in order. The Ollama provider talks to a local server (`OLLAMA_URL`), uses `OLLAMA_MODEL` or any pulled model, and forces JSON output. Install once with `winget install Ollama.Ollama`, then `ollama pull qwen2.5:7b-instruct` (about 4.7 GB) and optionally `qwen2.5:3b-instruct` for a faster, weaker option. Small local models get a simpler contract (`SYSTEM_LITE` in `services/story/generate.py`: five fixed scenes with sentence counts; the payoff scene and the dated kicker are inserted by code; 75-word floor). Measured on this laptop's CPU with `qwen2.5:7b-instruct`: a valid storyboard in about 3.5 minutes, well inside the 150-minute production window.
- The log line `llm: ollama used instead of sarvam` marks every fallback, so a drained Sarvam balance is visible in `output/logs/`.

## Scheduling on this machine

```
.venv\Scripts\python -m services.scheduler.daily install-tasks   # registers REELFORGE\produce-*, publish-*, dm-bot, analytics, niche-watch
.venv\Scripts\python -m services.scheduler.daily status          # the queue
.venv\Scripts\python -m services.scheduler.daily remove-tasks
```

The tasks run as the logged-in user with the venv Python, logging to `output/logs/<task>.log`. `install-tasks` ends by setting four laptop-safe properties on every task that `schtasks` leaves wrong by default: run as soon as possible after a missed start, wake the computer to run, allow starting on battery, and do not stop when switching to battery (plus a 3-hour execution limit). Without them a laptop that sleeps through 13:00 silently skips the whole afternoon reel, which is what happened on 2026-11-12. `harden-tasks` re-applies them on their own. Wake-to-run only works from sleep, not from a shut-down machine, so the laptop must at least be asleep, not off, at the slot times (the render needs about 15 minutes at 1080p and the Instagram upload a few more).

## Safety

Nothing is posted without a passed QA report and a passed rules check. A failed slot leaves a log and no post. Set `AUTOPUBLISH=false` to pause posting without touching the schedule.
