---
name: create-cinematic-reel
description: Build or extend a Style A (cinematic AI) reel in the REELFORGE Remotion engine from a storyboard: scene components, seams, SFX cues, captions, using the design tokens and the five-layer stack. Use when adding a new scene kind, when a storyboard uses style "cinematic", or when the user says "make this reel cinematic", "add a scene", "new visual for X".
---

# create-cinematic-reel

## Purpose
Implement the visuals for a storyboard in Style A (dark cinematic, violet hero, glow, particles, depth, kinetic type) so the `Reel` composition can render it.

## Trigger
A storyboard scene has a `kind` with no component in `video-engine/src/scenes/`, or an existing scene needs a stronger visual.

## Inputs
- The storyboard scene (`kind`, `title`, `highlight`, `data`, narration timing from the timed spec).
- Design tokens: `src/design/tokens.ts`, `src/design/motion.ts`.
- Craft rules: `.claude/skills/remotion-motion-graphics/SKILL.md` and `references/`. Remotion rules: `.claude/skills/remotion-markup/SKILL.md`.

## Outputs
- `src/scenes/<Kind>Scene.tsx` registered in `src/compositions/Reel.tsx::SceneByKind`.
- Kind added to `src/schema/reelSpec.ts` and `services/common/schema.py`.

## Workflow
1. Read the scene's narration and decide the ONE thing the viewer must notice. Sketch the stage: title block at y 360, visual stage y 640 to 1200, captions at y 1240.
2. Compose from primitives: `ReelFrame` is provided by `Reel`; use `ParticleField`, `Orb`, `Entrance`, `Stagger`, `WordReveal`, `Counter`, `Kicker`, `Mono`.
3. Time beats to the narration: derive frames from `scene.audioEndMs - scene.startMs` fractions, never magic numbers. Something moves in the first 15 frames. Hold after each hit (15 to 20 frames).
4. One hero-coloured glowing element per frame. Exits faster than entrances. Every `interpolate` clamped. No CSS transitions, no `Math.random`, no `useFrame`.
5. Add SFX cues in `Reel.tsx::SfxCues` if the scene needs more than the seam whoosh (SFX 2 to 3 frames before the visual).
6. `npx tsc --noEmit`, then preview in Studio (`npx remotion studio --no-open`, open `/Reel`), then `render-reel` draft, then `review-reel`.

## Validation
Frames extracted and inspected; the pre-delivery checklist in `references/design-rules.md` passes.

## Error handling
- Text wraps unexpectedly: set `maxWidth` on `WordReveal` and use px `columnGap`, not em.
- Elements pop in at frame 0: the previous scene's seam is `null`; check `SceneShell` props.

## Dependencies
Remotion 4.0.540, fonts in `public/fonts`, SFX in `public/sfx`.

## Motion principles

Read `.claude/skills/create-cinematic-reel/motion-principles.md` before writing, editing or reviewing a scene: vary eases, speeds and entry directions; build, breathe, resolve; three layers and two focal points per scene; no animation on frame 0; one sound accent per visual hit.
