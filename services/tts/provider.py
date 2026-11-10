"""TTS provider abstraction.

    TTSProvider
        |-- KokoroProvider   (hexgrad/Kokoro-82M, Apache-2.0, CPU or CUDA, word timestamps)
        |-- PiperProvider    (OHF-Voice piper1-gpl, GPL-3.0, CPU, sentence-level timing only)

Each provider returns 24 kHz (Kokoro) or 22.05 kHz (Piper) mono float32 audio
plus a list of word timings in seconds relative to the start of that audio.

Kokoro's English pipeline gives exact per-word timestamps. Its Hindi pipeline
(voices `hf_*` / `hm_*`, used for Hinglish) gives none, so the provider
synthesizes sentence by sentence and spreads each sentence's voiced span over
its words by character count. Captions then follow the speech to within a
word, which is enough for four-word caption pages.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np

log = logging.getLogger("reelforge.tts")

DEVANAGARI_RE = re.compile(r"[ऀ-ॿ]")
SENTENCE_RE = re.compile(r"(?<=[.!?।])\s+")
# Kokoro voice prefix -> pipeline language code.
LANG_BY_PREFIX = {"a": "a", "b": "b", "e": "e", "f": "f", "h": "h", "i": "i", "j": "j", "p": "p", "z": "z"}


def lang_for_voice(voice: str) -> str:
    return LANG_BY_PREFIX.get(voice[:1].lower(), "a")


def split_sentences(text: str) -> list[str]:
    return [s.strip() for s in SENTENCE_RE.split(text.strip()) if s.strip()]


@dataclass
class WordTiming:
    text: str
    start: float
    end: float


@dataclass
class TTSResult:
    audio: np.ndarray  # mono float32
    sample_rate: int
    words: list[WordTiming] = field(default_factory=list)
    provider: str = ""
    voice: str = ""
    synth_seconds: float = 0.0
    estimated: bool = False  # word timings estimated rather than measured

    @property
    def duration(self) -> float:
        return float(len(self.audio)) / self.sample_rate


class TTSProvider:
    name = "base"
    sample_rate = 24000

    def resolve_voice(self, voice: str) -> str:
        """The voice id actually used (providers may map or override it)."""
        return voice

    def synthesize(self, text: str, voice: str, speed: float = 1.0, caption_text: str | None = None) -> TTSResult:  # pragma: no cover - interface
        raise NotImplementedError

    def available(self) -> bool:  # pragma: no cover - interface
        return False


# Pronunciation overrides for terms the G2P tends to mangle. Applied to the
# text sent to the engine only; captions keep the original spelling.
PRONUNCIATION: dict[str, str] = {
    r"\bLLMs\b": "L L Ms",
    r"\bLLM\b": "L L M",
    r"\bRAG\b": "rag",
    r"\bGPU\b": "G P U",
    r"\bGPUs\b": "G P Us",
    r"\bAPI\b": "A P I",
    r"\bAPIs\b": "A P Is",
    r"\bJSON\b": "jason",
    r"\barXiv\b": "archive",
    r"\bSQL\b": "sequel",
    r"\bnpm\b": "N P M",
    r"\bCUDA\b": "koo-da",
    r"\bPyTorch\b": "pie-torch",
    r"\bDM\b": "D M",
    r"\bDMs\b": "D Ms",
    r"\bMIT\b": "M I T",
    r"\bPR\b": "P R",
    r"\bPRs\b": "P Rs",
    r"\bMD\b": "M D",
    r"\bdsh-superagent\b": "D S H super agent",
    r"\bGitHub\b": "git hub",
}


def apply_pronunciation(text: str) -> str:
    out = text
    for pattern, replacement in PRONUNCIATION.items():
        out = re.sub(pattern, replacement, out)
    return out


def voiced_span(audio: np.ndarray, sr: int, rel_db: float = -32.0, hop_s: float = 0.01) -> tuple[float, float]:
    """Start and end (seconds) of the part of `audio` louder than `rel_db`
    relative to its own peak RMS, so leading and trailing silence is skipped."""
    hop = max(1, int(sr * hop_s))
    n = len(audio) // hop
    if n == 0:
        return 0.0, len(audio) / sr
    rms = np.sqrt(np.mean(audio[: n * hop].reshape(n, hop) ** 2, axis=1))
    peak = float(rms.max()) or 1.0
    idx = np.where(rms > peak * 10 ** (rel_db / 20))[0]
    if len(idx) == 0:
        return 0.0, len(audio) / sr
    return idx[0] * hop / sr, min(len(audio) / sr, (idx[-1] + 1) * hop / sr)


def estimate_word_timings(audio: np.ndarray, sr: int, words: list[str], offset: float = 0.0) -> list[WordTiming]:
    """Spread the voiced span of `audio` across `words`, weighted by letter
    count plus a constant per word for the gap between words."""
    if not words:
        return []
    start, end = voiced_span(audio, sr)
    weights = [len(re.sub(r"[^\w]", "", w)) + 1.2 for w in words]
    total = sum(weights) or 1.0
    cursor = start
    out: list[WordTiming] = []
    for w, wt in zip(words, weights):
        d = (end - start) * wt / total
        out.append(WordTiming(w, offset + cursor, offset + cursor + d))
        cursor += d
    return out


class KokoroProvider(TTSProvider):
    name = "kokoro"
    sample_rate = 24000
    SENTENCE_GAP_S = 0.12
    LEAD_KEEP_S = 0.05
    TAIL_KEEP_S = 0.10

    def __init__(self, device: str = "auto", lang_code: str | None = None) -> None:
        self.device = device
        self.lang_code = lang_code  # None = pick from the voice prefix
        self._pipelines: dict[str, object] = {}

    def available(self) -> bool:
        try:
            import kokoro  # noqa: F401

            return True
        except Exception:  # pragma: no cover
            return False

    def _get(self, lang: str):
        if lang not in self._pipelines:
            import torch
            from kokoro import KPipeline

            device = None
            if self.device == "cuda" or (self.device == "auto" and torch.cuda.is_available()):
                device = "cuda"
            elif self.device == "cpu":
                device = "cpu"
            t0 = time.time()
            self._pipelines[lang] = KPipeline(lang_code=lang, repo_id="hexgrad/Kokoro-82M", device=device)
            log.info("Kokoro pipeline %s ready on %s in %.1fs", lang, device or "auto", time.time() - t0)
        return self._pipelines[lang]

    def _run(self, pipe, text: str, voice: str, speed: float) -> tuple[np.ndarray, list[WordTiming]]:
        chunks: list[np.ndarray] = []
        words: list[WordTiming] = []
        offset = 0.0
        for result in pipe(text, voice=voice, speed=speed):
            audio = result.audio.numpy() if hasattr(result.audio, "numpy") else np.asarray(result.audio)
            audio = audio.astype(np.float32)
            for tok in result.tokens or []:
                if tok.start_ts is None or tok.end_ts is None or not tok.text.strip():
                    continue
                words.append(WordTiming(tok.text, offset + float(tok.start_ts), offset + float(tok.end_ts)))
            chunks.append(audio)
            offset += len(audio) / self.sample_rate
        return (np.concatenate(chunks) if chunks else np.zeros(0, dtype=np.float32)), words

    def synthesize(self, text: str, voice: str = "am_fenrir", speed: float = 1.0, caption_text: str | None = None) -> TTSResult:
        lang = self.lang_code or lang_for_voice(voice)
        pipe = self._get(lang)
        t0 = time.time()
        if lang != "h" and not DEVANAGARI_RE.search(text):
            audio_all, words = self._run(pipe, apply_pronunciation(text), voice, speed)
            return TTSResult(audio_all, self.sample_rate, _realign_words(words, caption_text or text), self.name, voice, time.time() - t0)

        # Hinglish path: sentence by sentence, estimated word timings.
        spoken_sents = split_sentences(text)
        shown_sents = split_sentences(caption_text) if caption_text else spoken_sents
        aligned = len(shown_sents) == len(spoken_sents)
        if not aligned:
            log.warning("captionText has %d sentences but narration %d; timing the whole scene as one span", len(shown_sents), len(spoken_sents))
        chunks: list[np.ndarray] = []
        words: list[WordTiming] = []
        offset = 0.0
        gap = np.zeros(int(self.sample_rate * self.SENTENCE_GAP_S), dtype=np.float32)
        for i, sent in enumerate(spoken_sents):
            audio, _ = self._run(pipe, apply_pronunciation(sent), voice, speed)
            # The Hindi pipeline pads each sentence with ~1 s of silence; keep
            # 50 ms before the first syllable and 100 ms after the last.
            vs, ve = voiced_span(audio, self.sample_rate)
            audio = audio[int(max(0.0, vs - self.LEAD_KEEP_S) * self.sample_rate) : int(min(len(audio) / self.sample_rate, ve + self.TAIL_KEEP_S) * self.sample_rate)]
            if aligned:
                words.extend(estimate_word_timings(audio, self.sample_rate, re.findall(r"\S+", shown_sents[i]), offset))
            chunks.append(audio)
            offset += len(audio) / self.sample_rate
            if i < len(spoken_sents) - 1:
                chunks.append(gap)
                offset += len(gap) / self.sample_rate
        audio_all = np.concatenate(chunks) if chunks else np.zeros(0, dtype=np.float32)
        if not aligned:
            words = estimate_word_timings(audio_all, self.sample_rate, re.findall(r"\S+", caption_text or text))
        return TTSResult(audio_all, self.sample_rate, words, self.name, voice, time.time() - t0, estimated=True)


class PiperProvider(TTSProvider):
    """Fallback. Requires `pip install piper-tts` and a downloaded .onnx voice."""

    name = "piper"
    sample_rate = 22050

    def __init__(self, model_path: Optional[Path] = None) -> None:
        self.model_path = model_path

    def available(self) -> bool:
        try:
            import piper  # noqa: F401

            return self.model_path is not None and Path(self.model_path).exists()
        except Exception:
            return False

    def synthesize(self, text: str, voice: str = "", speed: float = 1.0, caption_text: str | None = None) -> TTSResult:
        from piper import PiperVoice  # type: ignore

        t0 = time.time()
        pv = PiperVoice.load(str(self.model_path))
        self.sample_rate = pv.config.sample_rate
        pcm = bytearray()
        for chunk in pv.synthesize(apply_pronunciation(text)):
            pcm.extend(chunk.audio_int16_bytes)
        audio = np.frombuffer(bytes(pcm), dtype=np.int16).astype(np.float32) / 32768.0
        return TTSResult(audio, self.sample_rate, [], self.name, voice or Path(str(self.model_path)).stem, time.time() - t0)


KOKORO_VOICE_RE = re.compile(r"^[a-z]{2}_[a-z]+$")


class ProviderExhausted(RuntimeError):
    """The paid provider cannot serve (credits, auth, rate limit, down); fall back to a free one."""


_TTS_EXHAUSTED = re.compile(r"HTTP (401|402|403|429|5\d\d)|quota|credit|insufficient|exhausted|rate.?limit", re.I)


class SarvamProvider(TTSProvider):
    """Sarvam AI Bulbul text-to-speech over plain HTTPS (no SDK).

    Settings come from the environment / git-ignored .env:
        SARVAM_API_KEY    required, never logged
        SARVAM_MODEL      bulbul:v3 (default) or bulbul:v4-flash (v2 is deprecated server-side)
        SARVAM_SPEAKER    speaker id for the model (default shubh)
        SARVAM_LANGUAGE   en-IN (default), hi-IN, ... (code-mixed text is fine)

    Sarvam returns no word timestamps, so each sentence is synthesized on its
    own, trimmed of silence, and its voiced span is spread over the words
    (same approach as the Kokoro Hindi path). Storyboard voices that look like
    Kokoro ids (am_fenrir) are ignored in favour of SARVAM_SPEAKER.
    """

    name = "sarvam"
    sample_rate = 24000
    URL = "https://api.sarvam.ai/text-to-speech"
    SENTENCE_GAP_S = 0.12
    LEAD_KEEP_S = 0.05
    TAIL_KEEP_S = 0.10
    MAX_CHARS = {"bulbul:v2": 1500}

    def __init__(self, api_key: str | None = None, model: str | None = None, speaker: str | None = None, language: str | None = None, post=None) -> None:
        import os

        self.api_key = api_key if api_key is not None else os.environ.get("SARVAM_API_KEY", "")
        self.model = model or os.environ.get("SARVAM_MODEL") or "bulbul:v3"
        self.speaker = speaker or os.environ.get("SARVAM_SPEAKER") or "shubh"
        self.language = language or os.environ.get("SARVAM_LANGUAGE") or "en-IN"
        self._post = post  # injectable for tests

    def available(self) -> bool:
        return bool(self.api_key)

    def resolve_voice(self, voice: str) -> str:
        return self.speaker if (not voice or KOKORO_VOICE_RE.match(voice)) else voice

    def _request(self, text: str, speaker: str, speed: float) -> np.ndarray:
        import base64
        import io

        import httpx
        import soundfile as sf

        limit = self.MAX_CHARS.get(self.model, 2500)
        if len(text) > limit:
            raise RuntimeError(f"Sarvam {self.model} accepts at most {limit} characters per request; split the narration")
        body = {
            "text": text,
            "language_code": self.language,
            "model": self.model,
            "speaker": speaker,
            "pace": max(0.5, min(2.0, speed)),
            "speech_sample_rate": self.sample_rate,
            "output_audio_codec": "wav",
        }
        headers = {"api-subscription-key": self.api_key, "Content-Type": "application/json"}
        post = self._post or (lambda **kw: httpx.post(**kw))
        last_err = ""
        for attempt in range(3):
            try:
                resp = post(url=self.URL, json=body, headers=headers, timeout=60)
            except Exception as exc:  # network down: let the caller fall back
                raise ProviderExhausted(f"Sarvam TTS unreachable: {exc}") from exc
            if resp.status_code == 200:
                data = resp.json()
                raw = b"".join(base64.b64decode(a) for a in data.get("audios", []))
                audio, sr = sf.read(io.BytesIO(raw), dtype="float32")
                if audio.ndim > 1:
                    audio = audio.mean(axis=1)
                self.sample_rate = int(sr)
                return audio.astype(np.float32)
            last_err = f"HTTP {resp.status_code}: {resp.text[:200]}"
            if resp.status_code in (429, 500, 502, 503) and attempt < 2:
                time.sleep(1.5 * (attempt + 1))
                continue
            break
        if _TTS_EXHAUSTED.search(last_err):
            raise ProviderExhausted(f"Sarvam TTS failed ({last_err})")
        raise RuntimeError(f"Sarvam TTS failed ({last_err})")

    def synthesize(self, text: str, voice: str = "", speed: float = 1.0, caption_text: str | None = None) -> TTSResult:
        if not self.api_key:
            raise RuntimeError("SARVAM_API_KEY is empty; put it in .env or set VOICE_PROVIDER=kokoro")
        speaker = self.resolve_voice(voice)
        t0 = time.time()
        spoken_sents = split_sentences(text)
        shown_sents = split_sentences(caption_text) if caption_text else spoken_sents
        aligned = len(shown_sents) == len(spoken_sents)
        chunks: list[np.ndarray] = []
        words: list[WordTiming] = []
        offset = 0.0
        for i, sent in enumerate(spoken_sents):
            audio = self._request(apply_pronunciation(sent), speaker, speed)
            sr = self.sample_rate
            vs, ve = voiced_span(audio, sr)
            audio = audio[int(max(0.0, vs - self.LEAD_KEEP_S) * sr) : int(min(len(audio) / sr, ve + self.TAIL_KEEP_S) * sr)]
            if aligned:
                words.extend(estimate_word_timings(audio, sr, re.findall(r"\S+", shown_sents[i]), offset))
            chunks.append(audio)
            offset += len(audio) / sr
            if i < len(spoken_sents) - 1:
                gap = np.zeros(int(sr * self.SENTENCE_GAP_S), dtype=np.float32)
                chunks.append(gap)
                offset += len(gap) / sr
        audio_all = np.concatenate(chunks) if chunks else np.zeros(0, dtype=np.float32)
        if not aligned:
            words = estimate_word_timings(audio_all, self.sample_rate, re.findall(r"\S+", caption_text or text))
        return TTSResult(audio_all, self.sample_rate, words, self.name, speaker, time.time() - t0, estimated=True)


_PUNCT_ONLY = re.compile(r"^[^\w]+$")


def merge_punctuation(words: list[WordTiming]) -> list[WordTiming]:
    """Kokoro emits punctuation as its own tokens. Attach trailing punctuation
    to the previous word and leading punctuation (quotes, brackets) to the
    next word so captions read like text."""
    out: list[WordTiming] = []
    pending_prefix = ""
    for w in words:
        if _PUNCT_ONLY.match(w.text):
            if out and w.text not in ("(", "[", "“", "\""):
                out[-1] = WordTiming(out[-1].text + w.text, out[-1].start, max(out[-1].end, w.end))
            else:
                pending_prefix += w.text
            continue
        out.append(WordTiming(pending_prefix + w.text, w.start, w.end))
        pending_prefix = ""
    return out


def realign_words(words: list[WordTiming], original: str) -> list[WordTiming]:
    """Map engine tokens back onto the original words so captions show the
    original spelling (e.g. "LLM" not "L L M"). Punctuation-only tokens are
    merged first. If counts still diverge, keep engine tokens."""
    merged = merge_punctuation(words)
    originals = [w for w in re.findall(r"\S+", original)]
    if len(originals) == len(merged):
        return [WordTiming(o, w.start, w.end) for o, w in zip(originals, merged)]
    return merged


_realign_words = realign_words


def text_hash(text: str, provider: str, voice: str, speed: float, caption_text: str | None = None) -> str:
    payload = [text, provider, voice, speed] + ([caption_text] if caption_text else [])
    return hashlib.sha1(json.dumps(payload, ensure_ascii=False).encode("utf-8")).hexdigest()[:16]


def pick_provider_name(cli: str | None, storyboard: str) -> str:
    """Provider precedence: --provider flag, then VOICE_PROVIDER in the
    environment / .env, then the storyboard's voice.provider."""
    import os

    return (cli or os.environ.get("VOICE_PROVIDER") or storyboard or "kokoro").strip().lower()


def get_provider(name: str = "kokoro", device: str = "auto", piper_model: Optional[Path] = None) -> TTSProvider:
    if name == "piper":
        return PiperProvider(piper_model)
    if name == "sarvam":
        return SarvamProvider()
    return KokoroProvider(device=device)
