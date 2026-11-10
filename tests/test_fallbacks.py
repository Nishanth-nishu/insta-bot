"""Paid -> free fallbacks: LLM provider chain and TTS provider switch."""
import numpy as np
import pytest

from services.llm.client import LLM, ProviderExhausted
from services.tts import synthesize as synth
from services.tts.provider import ProviderExhausted as TTSExhausted
from services.tts.provider import TTSResult, WordTiming


def test_llm_falls_back_to_next_provider_on_exhaustion(monkeypatch):
    llm = LLM(provider="sarvam", fallbacks=["ollama"])
    calls = []

    def sarvam(system, user, max_tokens, temperature):
        calls.append("sarvam")
        raise ProviderExhausted("Sarvam chat failed: HTTP 402 insufficient_quota_error")

    def ollama(system, user, max_tokens, temperature):
        calls.append("ollama")
        return '{"ok": true}'

    monkeypatch.setattr(llm, "_sarvam", sarvam)
    monkeypatch.setattr(llm, "_ollama", ollama)
    assert llm.complete_json("s", "u") == {"ok": True}
    assert calls == ["sarvam", "ollama"] and llm.used_provider == "ollama"


def test_llm_validation_error_does_not_fall_back(monkeypatch):
    llm = LLM(provider="sarvam", fallbacks=["ollama"])
    monkeypatch.setattr(llm, "_sarvam", lambda *a: (_ for _ in ()).throw(RuntimeError("Sarvam chat failed: HTTP 400 bad field")))
    monkeypatch.setattr(llm, "_ollama", lambda *a: '{"ok": true}')
    with pytest.raises(RuntimeError, match="HTTP 400"):
        llm.complete("s", "u")


def test_llm_all_providers_down_raises_with_every_error(monkeypatch):
    llm = LLM(provider="sarvam", fallbacks=["ollama"])
    monkeypatch.setattr(llm, "_sarvam", lambda *a: (_ for _ in ()).throw(ProviderExhausted("HTTP 429")))
    monkeypatch.setattr(llm, "_ollama", lambda *a: (_ for _ in ()).throw(ProviderExhausted("ollama unreachable")))
    with pytest.raises(RuntimeError, match="every LLM provider failed"):
        llm.complete("s", "u")


class _Prov:
    name = "fake"
    sample_rate = 24000

    def __init__(self, fail):
        self.fail = fail

    def available(self):
        return True

    def resolve_voice(self, v):
        return v

    def synthesize(self, text, voice, speed, caption_text=None):
        if self.fail:
            raise TTSExhausted("Sarvam TTS failed (HTTP 402: credits exhausted)")
        audio = np.zeros(2400, dtype=np.float32)
        return TTSResult(audio, 24000, [WordTiming(w, i * 0.02, i * 0.02 + 0.01) for i, w in enumerate(text.split())], self.name, voice, 0.0)


def test_tts_switches_to_kokoro_when_sarvam_is_exhausted(monkeypatch, tmp_path):
    from services.common.schema import Storyboard

    used = []

    def fake_get_provider(name, device="auto", piper_model=None):
        used.append(name)
        p = _Prov(fail=(name == "sarvam"))
        p.name = name
        return p

    monkeypatch.setattr(synth, "get_provider", fake_get_provider)
    monkeypatch.setattr(synth.paths, "PUBLIC", tmp_path / "public")
    monkeypatch.setattr(synth.paths, "ASSETS", tmp_path / "assets")
    monkeypatch.setattr(synth.paths, "AUDIO_CACHE", tmp_path / "cache")
    monkeypatch.setattr(synth.paths, "STORYBOARDS", tmp_path / "sb")
    for d in ("public", "assets", "cache", "sb"):
        (tmp_path / d).mkdir()
    monkeypatch.setenv("VOICE_PROVIDER", "sarvam")
    monkeypatch.delenv("TTS_FALLBACK", raising=False)
    sb = Storyboard.model_validate({"id": "t", "title": "t", "caption": "Comment LETS GO.", "presenter": {"enabled": False},
                                    "scenes": [{"id": "hook", "kind": "hook", "narration": "Coders, be scared."}, {"id": "end", "kind": "payoff", "narration": "Comment LETS GO.", "cta": "Comment LETS GO"}]})
    spec = synth.synthesize_storyboard(sb, device="cpu")
    assert used == ["sarvam", "kokoro"] and spec.voice["provider"] == "kokoro"
