---
name: generate-script
description: Write a 20 to 60 second English Reel script for a researched AI or tech topic in REELFORGE, spoken by the animated male host: an anxious-funny 5-second hook, narration, scene breakdown, on-screen text, caption and hashtags, a "comment LETS GO for the link" payoff, all traced to sources. Use when a topic is selected, or the user says "write the script", "script this topic".
---

# generate-script

## Purpose
Produce the structured script JSON that `create-storyboard` consumes, accurate to the research brief and natural when spoken by the host.

## Trigger
A topic in `data/topics/` has status `selected`, or the user names a topic with sources.

## Inputs
- Topic brief: name, category, verified claims, source URLs, publication dates.
- Hook formulas in `hooks.md` (this folder); de-slop rules from `ig-human`.

## Outputs
- `data/scripts/<id>.json`:
  `title, topic_id, category, hooks[3], hook_selected, narration, scenes[] (beat, narration, on_screen_text, visual), voice_instructions, caption, hashtags[], cta, sources[], dm_link`.

## House format (from 2026-11-10, every reel)
1. **Voice and language**: the host is male and speaks plain English (Kokoro `am_fenrir`, speed 1.05; the Hindi voices were tried on 2026-11-10 and dropped as too weak). Spoken, casual, contractions welcome. No `captionText` needed; captions come from the narration with exact word timings.
2. **Hook, first 5 seconds**: anxious and a bit funny, aimed at the viewer's job, money or status, trending-today where true. Two sentences, 9 to 14 words (hard cap 18, about 2.5 words/s). Pick from `hooks.md`, score anxiety / funny / clarity / truth. The host says it on screen, big.
3. **No link anywhere**: not in the narration, caption, titles, terminal commands or on-screen text. The last scene asks viewers to comment the keyword (`LETS GO`); the DM bot sends the link (`dm_link`, usually `sources[0]`). The caption's last line repeats the ask.
4. **On screen is Latin only**: titles, lines, CTA, captions, kicker.

## Workflow
1. Write three hooks from the library formulas. Score each (1 to 5) on anxiety, funny, clarity, truth. Pick the top; never a hook the body cannot pay off.
2. Narration structure: hook (2 sentences), context (1), development (3 to 4 beats, one idea each), payoff (result, number or reframe) then the fixed CTA ("Want the link? Comment LETS GO and I'll send it to your DMs. And follow, so you're not the last to know.").
3. Spoken-language rules: sentences under 14 words, no "delve", "game-changer", "revolutionary"; contractions welcome; numbers only when verified; avoid years and long numbers in the hook (TTS reads them slowly).
4. Every factual sentence maps to a source URL in `sources`. Unverified claims are cut, not softened.
5. Caption: first line survives the "... more" cut; one ask (the keyword); three to five hashtags (Instagram caps a post at 5; `ig-caption` skill); no URL.
6. Read it aloud mentally for TTS: expand abbreviations the engine mangles or add a `PRONUNCIATION` entry (`services/tts/provider.py`).

## Validation
Total narration 100 to 140 words for 40 to 55 s at the host's pace (about 2.5 words/s); hook under 18 words; no claim without a source; `python -m services.common.rules data/storyboards/<id>.json` passes once storyboarded.

## Error handling
- Thin research: go back to `research-topic`, do not invent.
- Hook longer than 5 s after synthesis: cut words, do not raise the speed above 1.2.

## Dependencies
Claude Code (this skill is executed by the agent), `ig-reel`, `ig-human`, `ig-caption` skills.
