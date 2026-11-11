"""One LLM call for the unattended pipeline, with a provider switch.

    LLM_PROVIDER in .env: sarvam | ollama | anthropic | claude-cli   (default: first available)
    LLM_FALLBACKS in .env: comma list tried in order when the primary is exhausted or
    unreachable (default "ollama,kokoro-free" -> ollama). Quota, auth, rate-limit and
    server errors trigger the fallback; a validation error (HTTP 400) does not.

  sarvam      Sarvam chat completions (SARVAM_API_KEY, model SARVAM_CHAT_MODEL, default
              sarvam-105b-conversations: answers directly; sarvam-105b reasons first, slower)
  anthropic   Anthropic API via the official SDK (ANTHROPIC_API_KEY or an `ant auth login` profile),
              model ANTHROPIC_MODEL, default claude-opus-5-5
  claude-cli  Claude Code headless (`claude -p`), uses the logged-in Claude subscription
  ollama      local open-source model through Ollama (OLLAMA_URL, OLLAMA_MODEL; default
              qwen2.5:7b-instruct, falls back to any pulled qwen2.5/llama model); free, runs on CPU

`complete_json(system, user)` returns the parsed JSON object the model wrote;
it strips code fences and retries once with the parse error fed back.
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
from typing import Any

from services.common.env import load_dotenv

log = logging.getLogger("reelforge.llm")

_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.M)
_EXHAUSTED = re.compile(r"HTTP (401|402|403|429|5\d\d)|quota|credit|insufficient|exhausted|rate.?limit|not found on PATH|unreachable|Connection|timed out", re.I)


class ProviderExhausted(RuntimeError):
    """The provider cannot serve right now (credits, auth, rate limit, down)."""


_ROOT = __import__("pathlib").Path(__file__).resolve().parents[2]


def claude_executable() -> str | None:
    """Claude Code CLI: the project-local install first (tools/claude-cli, which scheduled
    tasks can see), then PATH, then the npm global shim under %APPDATA%.

    The Claude desktop app is a packaged (MSIX) app: anything a session installs under
    %APPDATA% lands in the app's private LocalCache\Roaming and is invisible to Task
    Scheduler, so a global `npm install -g` never reaches the real machine."""
    local = _ROOT / "tools" / "claude-cli" / "node_modules" / ".bin" / ("claude.cmd" if os.name == "nt" else "claude")
    if local.exists():
        return str(local)
    exe = shutil.which("claude")
    if exe:
        return exe
    appdata = os.environ.get("APPDATA", "")
    for name in ("claude.cmd", "claude"):
        cand = os.path.join(appdata, "npm", name)
        if appdata and os.path.exists(cand):
            return cand
    return None


def _extract_json(text: str) -> Any:
    cleaned = _FENCE.sub("", text.strip()).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start >= 0 and end > start:
            return json.loads(cleaned[start : end + 1])
        raise


class LLM:
    def __init__(self, provider: str | None = None, model: str | None = None, fallbacks: list[str] | None = None) -> None:
        load_dotenv()
        self.provider = (provider or os.environ.get("LLM_PROVIDER") or self._auto()).strip().lower()
        self.model = model  # None = provider default / env override
        raw = fallbacks if fallbacks is not None else [p.strip().lower() for p in (os.environ.get("LLM_FALLBACKS") or "ollama").split(",") if p.strip()]
        self.fallbacks = [p for p in raw if p != self.provider]
        self.used_provider = self.provider

    @staticmethod
    def _auto() -> str:
        if claude_executable():
            return "claude-cli"
        if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"):
            return "anthropic"
        if os.environ.get("SARVAM_API_KEY"):
            return "sarvam"
        raise RuntimeError("no LLM available: set SARVAM_API_KEY, ANTHROPIC_API_KEY, or install the Claude Code CLI")

    # ---- providers -------------------------------------------------------
    def _ollama(self, system: str, user: str, max_tokens: int, temperature: float) -> str:
        import httpx

        base = (os.environ.get("OLLAMA_URL") or "http://127.0.0.1:11434").rstrip("/")
        try:
            tags = httpx.get(f"{base}/api/tags", timeout=10).json()
        except Exception as exc:
            raise ProviderExhausted(f"ollama unreachable at {base}: {exc}")
        local = [m.get("name", "") for m in tags.get("models", [])]
        wanted = self.model or os.environ.get("OLLAMA_MODEL") or "qwen2.5:7b-instruct"
        model = next((m for m in [wanted] + sorted(local, reverse=True) if m in local), None)
        if not model:
            raise ProviderExhausted(f"ollama has no model pulled (wanted {wanted}); run: ollama pull {wanted}")
        others = [m for m in local if m != model]
        for attempt, m in enumerate([model] + others[:1]):
            body = {"model": m, "stream": False, "format": "json",
                    "options": {"temperature": temperature if attempt == 0 else 0.3, "num_ctx": 8192, "num_predict": min(max_tokens, 2500), "repeat_penalty": 1.05, "repeat_last_n": 128},
                    "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
            r = httpx.post(f"{base}/api/chat", json=body, timeout=1800)
            if r.status_code == 200:
                self.used_model = m
                return r.json()["message"]["content"]
            log.warning("ollama %s: HTTP %s %s", m, r.status_code, r.text[:160])
            if "repeat" not in r.text.lower() or not others:
                break
        raise ProviderExhausted(f"ollama chat failed: HTTP {r.status_code} {r.text[:200]}")

    def _sarvam(self, system: str, user: str, max_tokens: int, temperature: float) -> str:
        import httpx

        key = os.environ.get("SARVAM_API_KEY", "")
        if not key:
            raise RuntimeError("SARVAM_API_KEY is empty")
        model = self.model or os.environ.get("SARVAM_CHAT_MODEL") or "sarvam-105b-conversations"
        # sarvam-105b reasons before it answers and the reasoning counts against
        # max_tokens (a 25-word answer used ~2,700 reasoning tokens), so give it room.
        r = httpx.post(
            "https://api.sarvam.ai/v1/chat/completions",
            headers={"api-subscription-key": key, "Content-Type": "application/json"},
            json={"model": model, "temperature": temperature, "max_tokens": min(max_tokens, 8192) if "conversations" in model else max(max_tokens, 16000), "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]},
            timeout=600,
        )
        if r.status_code != 200:
            err = f"Sarvam chat failed: HTTP {r.status_code} {r.text[:300]}"
            raise ProviderExhausted(err) if r.status_code != 400 or _EXHAUSTED.search(r.text) else RuntimeError(err)
        data = r.json()
        msg = data["choices"][0]["message"]
        content = msg.get("content")
        if not content:
            used = (data.get("usage") or {}).get("completion_tokens_details") or {}
            raise RuntimeError(f"Sarvam returned no content (reasoning tokens {used.get('reasoning_tokens')}; finish {data['choices'][0].get('finish_reason')})")
        return content

    def _anthropic(self, system: str, user: str, max_tokens: int, temperature: float) -> str:
        import anthropic

        client = anthropic.Anthropic()
        model = self.model or os.environ.get("ANTHROPIC_MODEL") or "claude-opus-5-5"
        with client.messages.stream(model=model, max_tokens=max_tokens, system=system, messages=[{"role": "user", "content": user}]) as stream:
            msg = stream.get_final_message()
        if msg.stop_reason == "refusal":
            raise RuntimeError("Anthropic API refused the request")
        return "".join(b.text for b in msg.content if b.type == "text")

    def _claude_cli(self, system: str, user: str, max_tokens: int, temperature: float) -> str:
        """One-shot text answer from Claude Code; no tools are granted."""
        exe = claude_executable()
        if not exe:
            raise ProviderExhausted("claude CLI not found (npm install -g @anthropic-ai/claude-code)")
        import tempfile

        # Multi-line arguments are cut at the first newline by the npm shim on Windows:
        # the user prompt goes over stdin and the system prompt through a file.
        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8") as f:
            f.write(system)
            sys_file = f.name
        cmd = [exe, "-p", "--append-system-prompt-file", sys_file, "--output-format", "text", "--max-turns", "1", "--disallowedTools", "Bash", "Write", "Edit"]
        try:
            r = subprocess.run(cmd, input=user, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=900, cwd=str(_ROOT))
        finally:
            try:
                os.unlink(sys_file)
            except OSError:
                pass
        out = (r.stdout or "") + (r.stderr or "")
        if r.returncode != 0 or "Not logged in" in out:
            raise ProviderExhausted(f"claude -p unavailable: {out[-300:].strip()}")
        return r.stdout

    # ---- public ----------------------------------------------------------
    def _call(self, provider: str, system: str, user: str, max_tokens: int, temperature: float) -> str:
        fn = {"sarvam": self._sarvam, "ollama": self._ollama, "anthropic": self._anthropic, "claude-cli": self._claude_cli}.get(provider)
        if fn is None:
            raise RuntimeError(f"unknown LLM provider {provider!r}")
        log.info("llm %s: %d chars system, %d chars user", provider, len(system), len(user))
        try:
            return fn(system, user, max_tokens, temperature)
        except ProviderExhausted:
            raise
        except Exception as exc:  # network errors, auth exceptions from SDKs
            if _EXHAUSTED.search(str(exc)) or exc.__class__.__name__ in ("ConnectError", "ConnectTimeout", "ReadTimeout", "AuthenticationError", "RateLimitError", "PermissionDeniedError", "InternalServerError"):
                raise ProviderExhausted(f"{provider}: {exc}") from exc
            raise

    def complete(self, system: str, user: str, max_tokens: int = 6000, temperature: float = 0.6) -> str:
        """Primary provider, then each fallback in order when one is exhausted or down."""
        errors: list[str] = []
        for provider in [self.provider] + self.fallbacks:
            try:
                text = self._call(provider, system, user, max_tokens, temperature)
                self.used_provider = provider
                if provider != self.provider:
                    log.warning("llm: %s used instead of %s (%s)", provider, self.provider, errors[-1][:160] if errors else "")
                return text
            except ProviderExhausted as exc:
                errors.append(f"{provider}: {exc}")
                log.warning("llm provider %s unavailable: %s", provider, str(exc)[:200])
        raise RuntimeError("every LLM provider failed:\n  " + "\n  ".join(errors))

    def complete_json(self, system: str, user: str, max_tokens: int = 6000, temperature: float = 0.6, retries: int = 1) -> Any:
        text = self.complete(system, user, max_tokens, temperature)
        for attempt in range(retries + 1):
            try:
                return _extract_json(text)
            except (json.JSONDecodeError, ValueError) as exc:
                if attempt >= retries:
                    raise RuntimeError(f"LLM did not return valid JSON: {exc}\n{text[:500]}")
                text = self.complete(system, user + f"\n\nYour previous answer was not valid JSON ({exc}). Return the JSON object only.", max_tokens, 0.3)
        raise RuntimeError("unreachable")
