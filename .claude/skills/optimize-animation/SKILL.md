---
name: optimize-animation
description: Make a REELFORGE Remotion scene render faster or look better without breaking determinism: profile render time per scene, reduce expensive filters and particle counts per quality profile, fix easing and layering defects found in review. Use when a render is slow, RAM spikes, frames look generic, or the user says "optimize", "speed up the render", "this scene looks flat".
---

# optimize-animation

## Purpose
Balance visual quality against render cost on the real hardware (local 16 GB CPU box, Ada RTX 3090 nodes).

## Trigger
Benchmark shows under 8 frames/s locally at draft, peak RAM over 6 GB, or review flagged "generic" motion.

## Inputs
- `output/reports/<id>.<profile>.benchmark.json`, the frame inspection notes, the scene source.

## Outputs
- Edited scene components; updated `quality` multipliers in `src/design/tokens.ts`; a before/after line in the benchmark report.

## Workflow
1. Measure first: render one scene in isolation (`--frames=<start>-<end>`) at draft and note frames/s.
2. Cost ranking to attack in order: CSS `filter: blur()` on large areas, SVG filters with high `stdDeviation`, hundreds of absolutely positioned DOM particles, `backdrop-filter`, big `box-shadow` stacks, text-shadow glow on many words.
3. Scale by profile: particles via `particleScale`, blur radius via a `qualityFactor`, skip grain at draft if needed.
4. Quality fixes from the craft checklist: replace lone fades with 2 to 3 property entrances, add holds, ensure one hero glow, add Ken Burns to any still, add a hold before the climax.
5. Re-render the same frames, compare frames/s and the extracted frames side by side.

## Validation
No visual regression in the extracted frames; frames/s improved or quality improved with cost documented.

## Error handling
- If determinism breaks (frame differs across two renders), look for time-based values, `Math.random`, or CSS animations.

## Dependencies
Remotion CLI, ffmpeg, psutil sampler in `services/rendering/local_render.py`.

## Motion principles

Read `.claude/skills/create-cinematic-reel/motion-principles.md` before writing, editing or reviewing a scene: vary eases, speeds and entry directions; build, breathe, resolve; three layers and two focal points per scene; no animation on frame 0; one sound accent per visual hit.
