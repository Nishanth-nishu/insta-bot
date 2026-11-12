# Motion principles for REELFORGE scenes

Distilled for the Remotion engine from the motion and composition guidance in Jakeschincariol/video-production-skills (hyperframes-creative, 2026-09). Written in our own words; the engine there is HTML and GSAP, ours is React and Remotion, so only the principles carry over, not the code.

## Variety is the whole game

- Not the same ease on every animation. Vary eases the way you vary font weights: at most two independent animations in a scene with the same easing.
- Not the same speed on everything. The slowest motion in a reel should be about three times slower than the fastest. Pick durations on purpose.
- Not the same entry on every element. Mix: from left, from right, from scale, opacity only, letter-spacing. The default "slide up 30 px and fade" on everything reads as a template.
- Each scene gets its own stagger rhythm.
- One ambient motion per scene, and a different one per scene: slow pan, subtle rotation, scale push, colour shift, or nothing. Stillness after motion is a tool.
- Do not start at frame 0. Offset the first animation by 3 to 9 frames; a zero-delay start looks like a jump cut.

## Easing is emotion, speed is weight

- Snappy out-eases read as confident and light; long in-out eases read as heavy and serious; a spring with overshoot reads as playful. Choose by the beat's mood, not by habit.
- Big, important things move slower; small accents move fast. Exits are faster than entrances.

## Scene structure: build, breathe, resolve

- Build (first 30 percent of the scene): elements enter, staggered, never all at once.
- Breathe (30 to 70 percent): the content is readable and alive with exactly one ambient motion.
- Resolve (last 30 percent): a decisive exit or a held end that the next seam cuts into.

## Transitions carry meaning

- Crossfade says "this continues". Hard cut says "wake up". A slow dissolve says "drift with me". Pick the seam by what the next scene means, not by what looks smooth.

## Composition in a 1080 x 1920 frame

- Two focal points per scene minimum, so the eye has somewhere to travel. Never one text block floating in empty space.
- Hero text fills 60 to 80 percent of the width. Web-sized text looks lost on a phone.
- Three layers per scene: a background treatment (glow, oversized faded type, colour panel), the content, and accents (rules, labels, data bars). A flat solid background reads as "nothing loaded".
- Anchor to edges and use zones (data left, content right; metadata bar on top, full width below). Centred-and-floating is a web pattern.
- Structural elements (rules, dividers, panels) create paths for the eye and animate well with a scaleX from zero.
- A connector line must name its start, its end and its job (reveal, route, validate, emphasise). A line the frame can lose without losing meaning is decoration: cut it.

## Sound follows the picture

- One accent per visual hit, synced to the frame it lands on, never a bed of effects. The music bed drops under the voice (Reel.tsx ducks it from the presenter's mouth envelope) and comes back in the gaps.
- A riser into the payoff, an impact when it lands, a whoosh into a seam: that is the whole default kit. Add a tick or pop only where a number counts or a card lands.

## How to use this

Read it before writing or editing a scene component (create-cinematic-reel, create-animated-explainer) and when reviewing one (optimize-animation, review-reel). Determinism rules still win: every motion is a pure function of the frame.
