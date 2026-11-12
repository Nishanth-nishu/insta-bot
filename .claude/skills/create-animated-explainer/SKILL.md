---
name: create-animated-explainer
description: Build Style B (animated explainer) scenes in the REELFORGE engine: architecture diagrams, neural networks, data flow, RAG and agent pipelines, vector search, terminal and code animations, charts. Use when a storyboard uses style "explainer", or the user says "explain X visually", "diagram of", "animate this architecture".
---

# create-animated-explainer

## Purpose
Every scene explains or reinforces one idea with a diagram that moves: nodes appear in reading order, data packets travel the edges, the active element is the single highlighted thing.

## Trigger
Storyboard `style: "explainer"` or a scene whose `data` describes a structure (nodes, stages, layers, request/response).

## Inputs
- Scene `data` fields (for example `nodes`, `stages`, `request`, `response`, `scratchpad`, `vectors`, `layers`, `series`).
- Palette `explainer` in `src/design/tokens.ts` (cyan hero, violet accent, grid background enabled).

## Outputs
- Scene components in `src/scenes/` (the Phase 2 set: `LoopScene`, `ReasoningScene`, `ToolCallScene`, `MemoryScene`, `WorkflowScene` are explainer-grade and reusable).
- Planned (Phase 3): `NeuralNetScene`, `TransformerScene`, `AttentionScene`, `RagScene`, `TerminalScene`, `CodeDiffScene`, `ChartScene`.

## Workflow
1. Map the idea to a diagram grammar: boxes (components), rings (loops), stacks (memory), grids (vectors), lanes (pipelines). Keep one grammar per scene.
2. Build geometry in SVG with absolute coordinates inside the 1080x620 stage; labels as HTML on top so fonts stay crisp.
3. Reveal in reading order with `spring` offsets of 7 to 10 frames per node; connectors draw with `strokeDasharray` / `strokeDashoffset`.
4. Motion must mean something: a packet moves only when the narration says data moves; a node lights only when it is being talked about.
5. Sync beats to narration fractions of the scene's audio duration.
6. Type-check, Studio preview, draft render, `review-reel`.

## Validation
Each node label readable at phone size (font at least 26 px for labels, 40 px for node names), no overlapping labels, diagram fully inside the safe zone, something new on screen at least every 90 frames.

## Error handling
- SVG filters (`feGaussianBlur`) are expensive at high particle counts; reduce in the draft profile via `particleScale`.
- If a `data` field is missing, components fall back to sensible defaults, but the storyboard validator should catch it first.

## Dependencies
`@remotion/shapes`, `@remotion/paths` for path lengths, design tokens.

## Motion principles

Read `.claude/skills/create-cinematic-reel/motion-principles.md` before writing, editing or reviewing a scene: vary eases, speeds and entry directions; build, breathe, resolve; three layers and two focal points per scene; no animation on frame 0; one sound accent per visual hit.
