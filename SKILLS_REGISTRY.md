# REELFORGE Skills Registry

Every candidate repository was cloned with `git clone --depth 1` into an isolated scratchpad directory, its license, last commit, file types, SKILL.md and any executable scripts were read **before** anything was copied into this project. Nothing from a candidate repo was executed during evaluation. Installed skills live in `.claude/skills/` (project scope) so they are versioned with REELFORGE.

Evaluation date: 2026-11-05.

## Installed

| Skill | Source | License | Last commit | Installation | Purpose | Used by | Invocation | Security review | Status |
|---|---|---|---|---|---|---|---|---|---|
| remotion-best-practices (router) plus remotion-create, remotion-markup, remotion-render, remotion-studio, remotion-captions, remotion-interactivity, remotion-docs, remotion-multimedia, remotion-maps, remotion-saas, remotion-license, remotion-upgrade | github.com/remotion-dev/skills (official, v4.0.540) | MIT | 2026-11-02 | `npx skills add remotion-dev/skills -a claude-code -y` (run in project root) | Official best practices for compositions, timing, transitions, fonts, audio, 3D, captions, rendering, Studio | video-engine, render-reel, review-reel, create-cinematic-reel, create-animated-explainer | Read `.claude/skills/remotion-best-practices/SKILL.md` first, then the specific REFERENCE.md. Claude Code loads them as `/remotion-*` | Markdown, YAML, SVG icons and two `.mjs` map-prep helpers only (not executed, maps unused). No hooks, no install scripts. | Installed |
| remotion-motion-graphics | github.com/JulianVolodia/claudestory_claude-remotion-skill | MIT | 2026-08-12 | Copied `remotion-motion-graphics/` folder into `.claude/skills/` (the repo's documented install method) | Motion-design craft rules: no linear easing, 2 to 3 property entrances, stagger, 5-layer stack, mandatory render, extract frames, inspect loop. Component patterns for mesh backgrounds, grade, grain, vignette, word reveal, counters, parallax. | Design system tokens in video-engine/src/design, all scene components, review-reel skill | Read SKILL.md, references/motion-patterns.md and references/design-rules.md before writing scenes | SKILL.md, two reference markdowns and one `theme.ts` template. No scripts. `build-skill.sh` in the repo root only zips the folder and was not copied. | Installed |
| sound-effects | github.com/Barty-Bart/motion-graphics | MIT (code), CC0 1.0 (bundled OGG library from Freesound and Kenney) | 2026-10-13 | Copied `skills/sound-effects/` into `.claude/skills/` | 7-sound default kit plus 103 CC0 alternatives (whoosh, pop, click, riser, impact, tick, glitch, ambience) with a manifest carrying source URL and license per file. Scripts measure loudness (target -16 LUFS, true peak below -4 dBFS) on the exported MP4. | Sound design in video-engine (`assets/sfx` is populated from this kit), review-reel loudness check | Read `.claude/skills/sound-effects/SKILL.md`. Scripts need a venv with librosa, soundfile, numpy, scipy, matplotlib. | `scripts/sound_pass.py` (169 lines) and `report.py` (27 lines) read: argparse, json, subprocess (ffmpeg calls), librosa, soundfile, scipy, matplotlib. Only network access is an optional runtime download of Mixkit typing SFX to a project cache, which REELFORGE does not use (Mixkit forbids redistribution; CC0 files only). No credentials, no deletion outside its own temp files. | Installed |
| reel-export | github.com/charlie947/motion-graphics-skills | MIT | 2026-10-11 | Copied `skills/reel-export/` into `.claude/skills/` | Instagram-safe export recipe: safe zone (y 360 to 1420, right 120px clear), H.264 yuv420p bt709 limited range, faststart, silent AAC track if no audio, ffprobe acceptance check | services/rendering encoder settings and services/quality technical QA | Read SKILL.md for the ffmpeg and ffprobe recipes | Markdown only. | Installed |
| frontend-design | github.com/anthropics/skills | Anthropic skills license (see LICENSE.txt in folder) | 2026-11 | Copied `skills/frontend-design/` into `.claude/skills/` | Distinctive UI design guidance, avoids templated AI aesthetics | apps/dashboard (Phase 7) | Load before designing dashboard screens | Markdown only. | Installed |
| webapp-testing | github.com/anthropics/skills | Anthropic skills license | 2026-11 | Copied `skills/webapp-testing/` into `.claude/skills/` | Playwright-based local web app testing with a server lifecycle helper | Dashboard tests (Phase 7), Remotion Studio smoke tests | `python .claude/skills/webapp-testing/scripts/with_server.py --help` | `with_server.py` uses subprocess and localhost sockets only. | Installed |

## Evaluated and not installed

| Candidate | Source | License | Why not installed | What was borrowed |
|---|---|---|---|---|
| Remotion Claude Code plugin | github.com/remotion-dev/claude-code-plugin | MIT | Identical skill content to remotion-dev/skills (same version 4.0.540, same `skills/remotion-best-practices` tree). Installing it would duplicate the 13 skills already in the project. The `claude` CLI is also not on PATH in child shells of this desktop session, so `claude plugin install remotion@remotion` could not be executed non-interactively. | Nothing extra needed. If the user wants it user-wide, run `claude plugin marketplace add remotion-dev/claude-code-plugin` then `claude plugin install remotion@remotion` from an interactive terminal. |
| HyperFrames | github.com/heygen-com/hyperframes | Apache 2.0 | A separate HTML plus GSAP rendering framework with its own CLI and studio. REELFORGE's primary engine is Remotion by requirement; running two render stacks adds weight without a second output format. Its skills (motion-doctrine, cut-the-curve, seam-craft) are tied to its seam-gate scripts and `index.html` structure. | The "vector law" for scene seams: exit and entry share an axis and direction, cuts land mid-motion, never crossfade without a carrier. Encoded in `video-engine/src/design/motion.ts` transition presets. |
| motion-broll, object-separation, explainer-* | github.com/Barty-Bart/motion-graphics | MIT | Built for editing a user's existing footage with Playwright and a Python pipeline (`setup.sh` installs Playwright and Chromium into the project). REELFORGE generates everything from code, no source footage. | Cue-list idea (one SFX per cue, SFX 2 frames early). |
| motion-effects, vox-explainer, title-sequence-3d, animated-chart, launch-video, loop-cover, and the rest of the pack | github.com/charlie947/motion-graphics-skills | MIT | Prompt-driven recipes that build single HTML files with `window.seek()` and export via HyperFrames, and each begins by asking the user four questions, which does not fit an autonomous pipeline. | Recipe ideas for chart morphs, masked type, perspective shift and the "one object changing shape" rule for premium motion, reused as Remotion components. |
| canvas-design, algorithmic-art | github.com/anthropics/skills | Anthropic | Produce static PNG/PDF art or p5.js sketches with their own timing, not Remotion frame-driven animation. | Seeded-randomness principle for particles (Style C candidates). |
| remotion-maps, remotion-saas | installed with the official set | MIT | Installed as part of the official bundle but unused: Reels do not need maps or SaaS rendering. Left in place so `/remotion-best-practices` routing works unchanged. | None. |

## Existing user-level skills reused

| Skill | Location | Used for |
|---|---|---|
| ig-reel | ~/.claude/skills/ig-reel | Hook formulas and beat-sheet structure for generate-script |
| ig-caption | ~/.claude/skills/ig-caption | Caption and hashtag export in services/publishing |
| ig-human | ~/.claude/skills/ig-human | De-slop pass on narration before TTS |
| reelsmith | ~/.claude/skills/reelsmith (global skill; code in the sibling `New folder/reelsmith` project, built by another Claude session on 2026-11-12, private, no license file, not vendored) | Evaluated 2026-11-12. `node "<reelsmith>/worker/cli.mjs" score --script s.json --duration N --no-claude` gives a rules-only retention estimate with fixes pinned to seconds (tonight's Tree-Ising storyboard: 80/100, hook too long, CTA too long); `hooks` and `carousel` spin a winner into new copy; `make` renders a caption-only 1080x1920 reel with the Windows voice in about 10 s. Its reels have no VRM host, so they never meet the house format and are never auto-published. Not wired into `services/scheduler/daily.py` yet (the user decides). Security read of `worker/`: only `node:fs/os/path/child_process`; it spawns bare `claude -p` from PATH (the project-local CLI in `tools/claude-cli` is not on the scheduler's PATH, so an unattended call must use `--no-claude` or set PATH), `ffmpeg`/`ffprobe` and a PowerShell TTS script; the only network calls are the optional paid ElevenLabs voice (never pass `--elevenlabs-key`) and the separate web app's Instagram poster, which the CLI never loads. |

## Project-local skills created by REELFORGE

Defined in `.claude/skills/` with their own SKILL.md (see each file for triggers, inputs, outputs, validation and error handling):

research-topic, generate-script, create-storyboard, create-cinematic-reel, create-animated-explainer, generate-voiceover, render-reel, review-reel, daily-production, optimize-animation.

## Rules applied during installation

- Read SKILL.md and all scripts before copying.
- No install hooks were run from third-party repos. The only executed third-party tool was the `skills` npm CLI (Vercel) which copies markdown folders.
- Mixkit assets are never stored in the repo (their license forbids redistribution). Only CC0 files from the sound-effects kit are used.
- Skills receive no secrets. Ada access is through the user's existing SSH key and is invoked only by REELFORGE's own scripts in `infra/ada`.
