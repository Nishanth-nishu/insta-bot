"""Turn a storyboard into a timed ReelSpec with voiceover and captions.

For each scene: synthesize narration (cached by text hash), measure its
duration, lay scenes out back to back with lead-in and pad, build the full
voice track, and emit:

    video-engine/public/voice/<reel_id>/full.wav     (48 kHz stereo-safe mono)
    data/storyboards/<reel_id>.timed.json             (ReelSpec for Remotion)
    assets/audio/<reel_id>/scene_<id>.wav + words.json

Usage:
    python -m services.tts.synthesize data/storyboards/agents-benchmark.json [--device cpu|cuda|auto] [--force]
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import sys
import time
from pathlib import Path

import numpy as np
import soundfile as sf

from services.captions.build import sentence_captions, validate_monotonic, word_captions
from services.common import paths
from services.common.rules import check_storyboard
from services.common.schema import Caption, PresenterSpec, ReelSpec, Storyboard, TimedScene
from services.common.env import load_dotenv
from services.tts.expressions import expression_track
from services.tts.provider import ProviderExhausted, TTSResult, WordTiming, get_provider, pick_provider_name, realign_words, text_hash

log = logging.getLogger("reelforge.tts.synthesize")
OUT_SR = 48000


def _resample(audio: np.ndarray, sr_in: int, sr_out: int) -> np.ndarray:
    if sr_in == sr_out:
        return audio
    n_out = int(round(len(audio) * sr_out / sr_in))
    x_old = np.linspace(0, 1, len(audio), endpoint=False)
    x_new = np.linspace(0, 1, n_out, endpoint=False)
    return np.interp(x_new, x_old, audio).astype(np.float32)


def _cached(cache_dir: Path, key: str) -> TTSResult | None:
    wav = cache_dir / f"{key}.wav"
    meta = cache_dir / f"{key}.json"
    if wav.exists() and meta.exists():
        audio, sr = sf.read(wav, dtype="float32")
        m = json.loads(meta.read_text(encoding="utf-8"))
        words = [WordTiming(w["text"], w["start"], w["end"]) for w in m.get("words", [])]
        return TTSResult(audio, sr, words, m.get("provider", ""), m.get("voice", ""), 0.0)
    return None


def mouth_envelope(track: np.ndarray, sr: int, fps: int = 30) -> list[float]:
    """Per-frame mouth openness 0..1 for the presenter: RMS per video frame,
    scaled so loud syllables hit 1, lightly smoothed, rounded to 3 decimals."""
    hop = max(1, sr // fps)
    n = len(track) // hop
    if n == 0:
        return []
    rms = np.sqrt(np.mean(track[: n * hop].reshape(n, hop) ** 2, axis=1))
    voiced = rms[rms > 1e-4]
    ref = float(np.percentile(voiced, 92)) if len(voiced) else 1.0
    env = np.clip(rms / (ref or 1.0), 0, 1)
    env = np.convolve(env, [0.25, 0.5, 0.25], mode="same")
    env[env < 0.06] = 0.0
    return [round(float(v), 3) for v in env]


def load_rig(sb: Storyboard) -> dict | None:
    """For the image host: the rig JSON next to the illustration, and a copy of
    the illustration under video-engine/public/ so Remotion can serve it."""
    if sb.presenter.look != "image" or not sb.presenter.image:
        return None
    img = paths.ASSETS / sb.presenter.image
    rig_file = img.with_suffix(".json")
    if not img.exists() or not rig_file.exists():
        raise RuntimeError(f"presenter.look is 'image' but {img} or {rig_file} is missing (drop the illustration in assets/images/host.png)")
    public_img = paths.PUBLIC / sb.presenter.image
    public_img.parent.mkdir(parents=True, exist_ok=True)
    if not public_img.exists() or public_img.stat().st_mtime < img.stat().st_mtime:
        try:
            from PIL import Image

            im = Image.open(img).convert("RGBA")
            if im.width > 1000:  # the hero shows the figure at ~520 px wide; keep decode cheap
                im = im.resize((1000, round(im.height * 1000 / im.width)), Image.LANCZOS)
            im.save(public_img, optimize=True)
        except ImportError:
            shutil.copy(img, public_img)
    rig = json.loads(rig_file.read_text(encoding="utf-8"))
    rig.pop("_comment", None)
    rig["image"] = sb.presenter.image
    return rig


def _store(cache_dir: Path, key: str, r: TTSResult) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    sf.write(cache_dir / f"{key}.wav", r.audio, r.sample_rate)
    (cache_dir / f"{key}.json").write_text(
        json.dumps({"provider": r.provider, "voice": r.voice, "words": [w.__dict__ for w in r.words]}, indent=1),
        encoding="utf-8",
    )


def synthesize_storyboard(sb: Storyboard, device: str = "auto", force: bool = False, provider_name: str | None = None, allow_rule_violations: bool = False) -> ReelSpec:
    """Synthesize with the chosen provider; if a paid provider is exhausted
    (credits, auth, rate limit, down), start over with the free local one
    (TTS_FALLBACK, default kokoro) so the whole reel keeps one voice."""
    try:
        return _synthesize(sb, device, force, provider_name, allow_rule_violations)
    except ProviderExhausted as exc:
        fallback = (os.environ.get("TTS_FALLBACK") or "kokoro").strip().lower()
        chosen = pick_provider_name(provider_name, sb.voice.provider)
        if chosen == fallback:
            raise
        log.warning("voice provider %s unavailable (%s); falling back to %s", chosen, str(exc)[:160], fallback)
        return _synthesize(sb, device, force, fallback, allow_rule_violations)


def _synthesize(sb: Storyboard, device: str, force: bool, provider_name: str | None, allow_rule_violations: bool) -> ReelSpec:
    paths.ensure_dirs()
    problems = check_storyboard(sb)
    if problems:
        msg = "storyboard breaks the house rules:" + "".join(chr(10) + "  - " + x for x in problems)
        if not allow_rule_violations:
            raise RuntimeError(msg + chr(10) + "(use --allow-rule-violations to override)")
        log.warning(msg)
    load_dotenv()
    name = pick_provider_name(provider_name, sb.voice.provider)
    provider = get_provider(name, device=device)
    if not provider.available():
        hint = " (SARVAM_API_KEY is empty in .env; paste the key or set VOICE_PROVIDER=kokoro)" if name == "sarvam" else ""
        raise RuntimeError(f"TTS provider {provider.name} is not available in this environment{hint}")
    voice_id = provider.resolve_voice(sb.voice.voice)
    log.info("voice provider %s, voice %s", provider.name, voice_id)

    voice_dir = paths.PUBLIC / "voice" / sb.id
    voice_dir.mkdir(parents=True, exist_ok=True)
    scene_audio_dir = paths.ASSETS / "audio" / sb.id
    scene_audio_dir.mkdir(parents=True, exist_ok=True)

    cursor_ms = 400  # leading silence before the first word
    track: list[np.ndarray] = [np.zeros(int(OUT_SR * cursor_ms / 1000), dtype=np.float32)]
    captions: list[Caption] = []
    timed: list[TimedScene] = []
    caption_mode = "word"
    total_synth = 0.0

    for scene in sb.scenes:
        key = text_hash(scene.narration, provider.name, voice_id, sb.voice.speed, scene.captionText)
        result = None if force else _cached(paths.AUDIO_CACHE, key)
        if result is None:
            log.info("synthesizing scene %s (%d chars)", scene.id, len(scene.narration))
            result = provider.synthesize(scene.narration, voice_id, sb.voice.speed, caption_text=scene.captionText)
            _store(paths.AUDIO_CACHE, key, result)
            total_synth += result.synth_seconds
        else:
            log.info("cache hit for scene %s", scene.id)

        result.words = realign_words(result.words, scene.captionText or scene.narration)
        audio48 = _resample(result.audio, result.sample_rate, OUT_SR)
        audio_ms = int(round(len(audio48) / OUT_SR * 1000))
        start_ms = cursor_ms
        audio_start = start_ms + scene.leadInMs
        audio_end = audio_start + audio_ms
        end_ms = max(audio_end + scene.padAfterMs, start_ms + scene.minDurationMs)

        track.append(np.zeros(int(OUT_SR * scene.leadInMs / 1000), dtype=np.float32))
        track.append(audio48)
        track.append(np.zeros(int(OUT_SR * (end_ms - audio_end) / 1000), dtype=np.float32))

        sf.write(scene_audio_dir / f"scene_{scene.id}.wav", result.audio, result.sample_rate)
        if result.words:
            captions.extend(word_captions(result.words, audio_start))
        else:
            caption_mode = "sentence"
            captions.extend(sentence_captions(scene.captionText or scene.narration, audio_start, audio_ms))

        timed.append(TimedScene(**scene.model_dump(), startMs=start_ms, endMs=end_ms, audioStartMs=audio_start, audioEndMs=audio_end))
        cursor_ms = end_ms

    full = np.concatenate(track)
    # Gentle peak normalisation so the VO sits around -3 dBFS peak.
    peak = float(np.abs(full).max()) or 1.0
    full = full * (10 ** (-3 / 20) / peak)
    sf.write(voice_dir / "full.wav", full, OUT_SR, subtype="PCM_16")

    envelope = mouth_envelope(full, OUT_SR, 30)
    problems = validate_monotonic(captions)
    if problems:
        log.warning("caption problems: %s", problems[:5])

    spec = ReelSpec(
        id=sb.id,
        title=sb.title,
        style=sb.style,
        durationMs=cursor_ms,
        voice={"file": f"voice/{sb.id}/full.wav", "provider": provider.name, "voice": voice_id, "speed": sb.voice.speed},
        presenter=PresenterSpec(**sb.presenter.model_dump(), fps=30, envelope=envelope, rig=load_rig(sb), expressions=expression_track(timed, captions, envelope, 30, cursor_ms)),
        music=sb.music,
        captions=captions,
        captionMode=caption_mode,  # type: ignore[arg-type]
        scenes=timed,
        caption=sb.caption,
        hashtags=sb.hashtags,
        sources=sb.sources,
        dmKeyword=sb.dmKeyword,
        dmLink=sb.dmLink or (sb.sources[0] if sb.sources else None),
    )
    out = paths.STORYBOARDS / f"{sb.id}.timed.json"
    out.write_text(spec.model_dump_json(indent=1), encoding="utf-8")
    # Copy for Remotion Studio (served from public/specs) so previews need no props file.
    spec_pub = paths.PUBLIC / "specs"
    spec_pub.mkdir(parents=True, exist_ok=True)
    (spec_pub / f"{sb.id}.json").write_text(spec.model_dump_json(indent=1), encoding="utf-8")
    (scene_audio_dir / "words.json").write_text(json.dumps([c.model_dump() for c in captions], indent=1), encoding="utf-8")
    log.info("wrote %s (%.1fs, %d captions, synth %.1fs)", out, cursor_ms / 1000, len(captions), total_synth)
    return spec


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("storyboard")
    ap.add_argument("--device", default="auto")
    ap.add_argument("--provider", default=None)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--allow-rule-violations", action="store_true", help="synthesize even if services.common.rules reports problems")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    sb = Storyboard.model_validate_json(Path(args.storyboard).read_text(encoding="utf-8"))
    t0 = time.time()
    spec = synthesize_storyboard(sb, device=args.device, force=args.force, provider_name=args.provider, allow_rule_violations=args.allow_rule_violations)
    print(json.dumps({"id": spec.id, "durationMs": spec.durationMs, "captions": len(spec.captions), "captionMode": spec.captionMode, "scenes": [(s.id, s.startMs, s.endMs) for s in spec.scenes], "seconds": round(time.time() - t0, 1)}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
