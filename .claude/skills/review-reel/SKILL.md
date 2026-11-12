---
name: review-reel
description: Quality-assure a rendered REELFORGE reel. Technical QA with ffprobe and loudness, a contact sheet, frame-by-frame visual inspection against the motion craft checklist, and an editorial pass on the script. Use after every render, or when the user says "review the reel", "QA this", "does it pass".
---

# review-reel

## Purpose
Decide pass or fail for a rendered MP4 and record why. A render is not done because the renderer exited 0.

## Inputs
- MP4 path, its timed spec, the storyboard source list.

## Outputs
- `output/reports/<name>.technical.json` and `<name>.contact.png`.
- Extracted frames in `output/frames/<name>/`.
- A written verdict with defects and fixes (in the reel's report markdown).

## Workflow
1. Technical:
   ```bash
   .venv\Scripts\python -m services.quality.technical output/drafts/<name>.mp4 --spec data/storyboards/<id>.timed.json --expect-width 1080 --expect-height 1920
   ```
   Checks resolution, 9:16, fps, H.264, AAC, duration vs spec, loudness (-16 LUFS target, true peak under -1 dBFS), and freeze detection.
2. Visual: extract one frame per scene at its midpoint and one at each seam (`ffmpeg -ss <t> -i in.mp4 -frames:v 1 f.png`) and LOOK at every frame. Check, in order of frequency:
   - text touching or leaving the frame, anything below y=1420 or right of x=960 (platform UI);
   - elements visible before entrance or after exit (missing clamp);
   - more than one glowing hero element per frame;
   - captions overlapping scene content;
   - dead scenes (nothing moving for more than 90 frames);
   - fonts falling back to a system face.
3. Presenter and format: the host is visible and his mouth moves while the voice plays (check a frame mid-sentence against one in a pause); hook audio ends by 5.5 s (`scenes[0].endMs` in the timed spec); no URL or repo link anywhere on screen; captions are plain English; the payoff asks for the `LETS GO` comment; the badge does not cover scene content (it sits at x 58..284, y 1178..1404).
4. Editorial: hook lands in the first 1.5 s, each scene explains one thing, every claim in the narration traces to `sources`, no fabricated numbers, narration reads naturally (run the `ig-human` skill's de-slop checks on the script).
5. Optional independent review: spawn a subagent with the frames and the checklist from `.claude/skills/remotion-motion-graphics/references/design-rules.md` and compare verdicts.

## Validation
Pass requires: all technical checks PASS, no visual defect of severity "blocking", editorial checks satisfied. Otherwise the reel moves to `output/rejected/` with the report.

## Error handling
- `freezedetect` false positives on intentional holds under 2.5 s are ignored by design; longer holds are defects.
- If loudness is outside range, adjust `music.volume` in the storyboard or the VO normalisation, re-render.

## Dependencies
FFmpeg/ffprobe, Python venv.

## Motion principles

Read `.claude/skills/create-cinematic-reel/motion-principles.md` before writing, editing or reviewing a scene: vary eases, speeds and entry directions; build, breathe, resolve; three layers and two focal points per scene; no animation on frame 0; one sound accent per visual hit.
