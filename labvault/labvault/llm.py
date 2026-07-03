"""Local LLM client abstraction.

Two backends, both expected to run on the same machine (or the homelab LAN):
  - OllamaBackend      -> POST {base}/api/chat        (default, gemma3)
  - OpenAICompatBackend-> POST {base}/chat/completions (LM Studio, llama.cpp, vLLM)

Nothing here ever reaches a cloud provider: config.check_llm_privacy() gates the
endpoint at startup. If the LLM is unreachable, callers get LLMUnavailable and
degrade to deterministic regex extraction (everything flagged for review).
"""

from __future__ import annotations

import json
import re
from typing import Any

import httpx

from .config import Settings

_JSON_BLOCK = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


class LLMUnavailable(RuntimeError):
    """Raised when the local model can't be reached or returns nothing usable."""


class LLMBackend:
    def chat(self, system: str, user: str, *, json_mode: bool = False) -> str:
        raise NotImplementedError

    def available(self) -> bool:
        try:
            self.chat("You are a health check.", "Reply with the single word OK.")
            return True
        except Exception:
            return False


class OllamaBackend(LLMBackend):
    def __init__(self, settings: Settings):
        self.base = settings.llm_base_url.rstrip("/")
        self.model = settings.llm_model
        self.timeout = settings.llm_timeout

    def chat(self, system: str, user: str, *, json_mode: bool = False) -> str:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "stream": False,
            "options": {"temperature": 0},
        }
        if json_mode:
            payload["format"] = "json"
        try:
            resp = httpx.post(f"{self.base}/api/chat", json=payload, timeout=self.timeout)
            resp.raise_for_status()
        except httpx.HTTPError as e:
            raise LLMUnavailable(f"Ollama request failed: {e}") from e
        return resp.json().get("message", {}).get("content", "")


class OpenAICompatBackend(LLMBackend):
    def __init__(self, settings: Settings):
        self.base = settings.llm_base_url.rstrip("/")
        self.model = settings.llm_model
        self.timeout = settings.llm_timeout

    def chat(self, system: str, user: str, *, json_mode: bool = False) -> str:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        try:
            resp = httpx.post(f"{self.base}/chat/completions", json=payload, timeout=self.timeout)
            resp.raise_for_status()
        except httpx.HTTPError as e:
            raise LLMUnavailable(f"OpenAI-compatible request failed: {e}") from e
        return resp.json()["choices"][0]["message"]["content"]


class NullBackend(LLMBackend):
    """Used when LABVAULT_LLM_BACKEND=none; forces regex-only pipeline."""

    def chat(self, system: str, user: str, *, json_mode: bool = False) -> str:
        raise LLMUnavailable("LLM backend disabled (LABVAULT_LLM_BACKEND=none)")

    def available(self) -> bool:
        return False


def make_backend(settings: Settings) -> LLMBackend:
    settings.check_llm_privacy()
    backend = settings.llm_backend.lower()
    if backend == "ollama":
        return OllamaBackend(settings)
    if backend in ("openai", "openai-compat", "lmstudio"):
        return OpenAICompatBackend(settings)
    if backend == "none":
        return NullBackend()
    raise ValueError(f"Unknown LABVAULT_LLM_BACKEND: {settings.llm_backend!r}")


def extract_json(text: str) -> Any:
    """Best-effort parse of a model reply into JSON.

    Handles fenced code blocks and leading/trailing prose that small models
    sometimes emit even in JSON mode.
    """
    text = (text or "").strip()
    if not text:
        raise ValueError("empty response")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    m = _JSON_BLOCK.search(text)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass
    # Grab the outermost {...} or [...] span.
    for open_ch, close_ch in (("[", "]"), ("{", "}")):
        start = text.find(open_ch)
        end = text.rfind(close_ch)
        if 0 <= start < end:
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                continue
    raise ValueError("no JSON found in response")
