# Feedback loop: learn from watch time, post better tomorrow

Since 2026-11-11 the daily pipeline measures every posted reel and feeds what it learns back into the next day's topic choice and script. Everything lives in `services/analytics/` and writes to `data/analytics/` (git-ignored).

| Step | Module | When | Output |
|---|---|---|---|
| Collect | `insights.py` | task `REELFORGE\analytics`, daily 12:30 IST | `insights.json`: one snapshot per reel per run (views, reach, likes, comments, saves, shares, average watch time, total watch time) with the reel's age in hours |
| Learn | `learn.py` | same task, and again at the start of every `produce` | `performance.json` (one row per settled reel), `brief.md` (what is working, in words), `weights.json` (nudges for the topic picker) |
| Niche watch | `niche.py` | task `REELFORGE\niche-watch`, Mondays 12:00 IST | `niche_evidence.md`: public YouTube Shorts counts for the creators in `niche_channels.txt`, each video ranked as a multiple of its own channel's median with the hook formula and our hook score (yt-dlp, no login); then `niche.md`: a swipe file of hook shapes, trending topics and format notes, written by a headless Claude Code session that reads the evidence first and uses web search only to fill in the week's topics |

```
.venv\Scripts\python -m services.scheduler.daily analytics     # collect + learn now
.venv\Scripts\python -m services.analytics.insights --show     # latest numbers per reel
.venv\Scripts\python -m services.analytics.learn --print       # rebuild and print the brief
.venv\Scripts\python -m services.scheduler.daily niche         # run the niche watch now
```

## How a reel is scored

Everything is relative to this account's own median, so a small account learns from its first week instead of waiting for big numbers.

- retention = average watch time / reel length. The hook and the pacing.
- engagement = (likes + comments + saves + shares) / reach. Did it earn an action.
- views. Did Instagram push it.
- index = 0.45 x retention + 0.30 x views + 0.25 x engagement, each as value / median, capped at 3.

A reel counts once it is 20 hours old (a "settled" snapshot), because reels keep collecting views for a day or two. Each reel is tagged with what we chose for it: topic category (from the topic file), hook text and hook family (question, if-you, number, warning, callout, statement; classified from the first scene), hook score, Reelsmith's pre-flight retention estimate (rules only; see AUTOMATION.md), scene kinds, narration words, length bucket and slot. The brief groups the index by each of those, so the two predicted scores (hook score, retention estimate) can be checked against the measured retention before anyone trusts them.

## What changes, and what never does

- Topic choice: `pick_topic` multiplies a candidate's research score by its category weight. Weights are clamped to 0.75 .. 1.3, so the loop nudges, it does not take over.
- Script: the one-shot prompt gets the brief's "Do more / do less" lines and the niche swipe file appended; the headless Claude producer is told to read `data/analytics/brief.md` and `data/analytics/niche.md`. The house format (host, voice, no links, LETS GO gate, dated kicker, SEO caption) is fixed by the rules checker and is not up for learning.
- Guardrail: below 4 settled reels the brief says "collecting" and every weight is 1.0. A group (a category, a hook family) needs at least 2 reels before it gets a weight. One lucky reel cannot swing the channel.
- Retention warnings: median retention under 35 percent adds a line telling the writer that people leave in the first seconds; over 60 percent tells it to keep the hook and push the payoff.

## Reading the brief

`data/analytics/brief.md` is plain Markdown: top three reels with their hooks, the weakest reel, tables by category, hook formula, length and slot, then the "Do more / do less" lines. It is rewritten every day; the performance table next to it has the raw rows.

## Dependencies and limits

- The collector needs the same Graph API access as publishing (`IG_ACCESS_TOKEN`, `IG_USER_ID`). An "API access blocked" answer is logged per reel and never stops the run.
- Average watch time comes from `ig_reels_avg_watch_time`; if the API ever retires a metric name the collector drops it and keeps the rest.
- The loop starts changing choices after the fourth settled reel, so about two days of posting at two reels a day.
- The niche watch runs on the Claude subscription through the project-local CLI (`tools/claude-cli`). If the CLI is missing or logged out the step is skipped and the previous `niche.md` stays in use.
- The evidence step needs `yt-dlp` (in `requirements.txt`) and reads only public channel tabs: no login, no cookies. `data/analytics/niche_channels.txt` holds the handles (seeded with twelve AI-news creators whose medians sit between about 2k and 36k views, within reach of a new account); edit it to change who is watched. The ranking needs at least four videos per channel, uses the vendored `collect.py` and `swipe.py` from master-skills, and the first run on 2026-11-12 found 73 outliers among 371 Shorts. Raw view counts are never the signal: a video that did 10x its own channel's median is the thing to copy the shape of, never the words.
