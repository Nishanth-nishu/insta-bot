# REELFORGE

A local-first, autonomous production studio for faceless AI and technology Instagram Reels. Research, script, storyboard, original motion graphics, local voiceover, synced captions, local rendering, QA and export, with zero paid API or cloud rendering spend.

- Video engine: Remotion 4 (React, TypeScript, Three.js) in `video-engine/`
- Services: Python 3.11, pydantic, SQLite in `services/`
- Dashboard: React, Vite, Tailwind in `apps/dashboard/` (Phase 7)
- GPU work: optional Ada cluster offload via `infra/ada/` (RTX 3090 nodes). Everything has a local CPU fallback.

## Quick start

```bash
cd video-engine
npm install
npx remotion studio
```

Render the Phase 1 smoke test:

```bash
cd video-engine
npx remotion render src/index.ts SmokeTest ../output/drafts/smoke-test.mp4 --codec h264 --crf 18 --concurrency 2
```

Python services:

```bash
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python -m pytest tests
```

## Documents

- [docs/ENVIRONMENT_REPORT.md](docs/ENVIRONMENT_REPORT.md): verified hardware and software, local and Ada.
- [SKILLS_REGISTRY.md](SKILLS_REGISTRY.md): every Claude Code skill evaluated, installed or rejected, with security notes.
- [ARCHITECTURE.md](ARCHITECTURE.md): system design, data model, engine layout, Ada integration.
- [ROADMAP.md](ROADMAP.md): phases, acceptance criteria and status.

## Layout

```
apps/            dashboard (React) and backend (FastAPI)
video-engine/    Remotion project: design tokens, layers, typography, scenes, compositions
services/        research, scripts, storyboard, tts, captions, rendering, quality, publishing, scheduler
data/            SQLite database, topic / script / storyboard JSON
assets/          fonts (OFL), sfx (CC0), music, images
output/          drafts, approved, rejected, covers, reports, frames (git-ignored media)
infra/ada/       SLURM templates and sync scripts for the Ada cluster
tests/           pytest suites
.claude/skills/  installed third-party skills and REELFORGE's own project skills
```

## Budget rules

No paid AI, video, image, TTS or stock APIs. No cloud GPU rentals. No paid hosting or databases. Free open-source software, local hardware and the authorised Ada cluster only. Generated videos, model weights and secrets are never committed.

## House format (since 2026-11-10)

Every reel has an animated host: a prebuilt anime character, the VRoid Project's CC0 `HairSample_Male` avatar (`video-engine/public/models/host.vrm`, licence in `assets/models/LICENSE.txt`), driven by `video-engine/src/layers/VrmHost.tsx` through the model's own rig (VRM expressions for lip-sync and blinks, humanoid bones for head sway, breathing and a relaxed arm pose; any VRM file drops in via `presenter.model`). Alternatives kept as `presenter.look`: `image` (your own picture animated in layers by `ImageHost.tsx`) and the vector looks in `Presenter.tsx`, a male English voice (Sarvam Bulbul when `VOICE_PROVIDER=sarvam` in `.env`, else Kokoro `am_fenrir`; see `.env.example`), an anxious-funny hook inside the first 5 seconds (`.claude/skills/generate-script/hooks.md`), and no link anywhere: the last scene asks viewers to comment `LETS GO` and `services/publishing/dm_bot.py` sends the link by DM. `python -m services.common.rules data/storyboards/<id>.json` enforces all of it before the voiceover.

## Daily automation

Two reels a day, unattended: `services/scheduler/daily.py` produces each reel ahead of time (research, LLM script via `services/story/generate.py`, voiceover, render, QA, package) and posts it on the minute at 15:30 and 20:40 IST with an SEO caption that carries the posting date, then answers LETS GO comments. `install-tasks` registers the Windows scheduled tasks; `AUTOPUBLISH` in `.env` is the kill switch. Details in `docs/AUTOMATION.md`. A feedback loop (`services/analytics/`) collects Instagram insights for every post, scores reels on retention, views and engagement against the account's own median, and feeds a what-is-working brief and category weights back into the next day's topic choice and script. Details in `docs/ANALYTICS.md`.

## Publishing

REELFORGE prepares a publication package (MP4, cover, caption, hashtags, checklist) for manual upload. It never posts to Instagram on its own. After you approve a post, `services/publishing/instagram.py` publishes it through the official Graph API and `dm_bot.py` answers the keyword comments.
