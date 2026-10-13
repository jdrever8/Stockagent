"""Thin LLM clients over plain HTTP (no vendor SDKs to pin).

A model is written as ``provider:model``. Providers:

* ``openai``    - api.openai.com, OPENAI_API_KEY
* ``anthropic`` - api.anthropic.com Messages API, ANTHROPIC_API_KEY
* ``gemini``    - Google's OpenAI-compatible endpoint, GOOGLE_API_KEY
* ``deepseek``  - api.deepseek.com, DEEPSEEK_API_KEY
* ``yunwu``     - any OpenAI-compatible gateway, YUNWU_API_KEY / YUNWU_API_BASE_URL
* ``mock``      - offline deterministic stub for tests and dry runs

A bare model name goes to the gateway when YUNWU_API_KEY is set, otherwise the
provider is guessed from the name.
"""
from __future__ import annotations

import json
import random
import re
import threading
import time
from dataclasses import dataclass
from typing import Dict, List, Optional

import requests

from .config import env

OPENAI_COMPATIBLE = {
    "openai": ("https://api.openai.com/v1", "OPENAI_API_KEY"),
    "gemini": ("https://generativelanguage.googleapis.com/v1beta/openai", "GOOGLE_API_KEY"),
    "deepseek": ("https://api.deepseek.com/v1", "DEEPSEEK_API_KEY"),
    "yunwu": ("https://yunwu.ai/v1", "YUNWU_API_KEY"),
}


class LLMError(RuntimeError):
    pass


class LLMFatal(LLMError):
    """Request rejected (bad key, bad model, bad request): retrying will not help."""


@dataclass
class Usage:
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    failures: int = 0


class LLMClient:
    def __init__(self, spec: str, temperature: Optional[float] = None, max_tokens: int = 1024,
                 timeout: float = 120.0, max_retries: int = 4):
        self.spec = spec
        self.provider, self.model = parse_model_spec(spec)
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.timeout = timeout
        self.max_retries = max_retries
        self.usage = Usage()
        self._lock = threading.Lock()

    def chat(self, system: str, messages: List[Dict[str, str]]) -> str:
        for attempt in range(self.max_retries + 1):
            try:
                if self.provider == "mock":
                    text, tin, tout = mock_reply(messages[-1]["content"]), 0, 0
                elif self.provider == "anthropic":
                    text, tin, tout = self._anthropic(system, messages)
                else:
                    text, tin, tout = self._openai_compatible(system, messages)
                with self._lock:
                    self.usage.calls += 1
                    self.usage.input_tokens += tin
                    self.usage.output_tokens += tout
                return text
            except (requests.RequestException, LLMError, KeyError, ValueError) as exc:
                if isinstance(exc, LLMFatal) or attempt == self.max_retries:
                    with self._lock:
                        self.usage.failures += 1
                    raise LLMError(f"{self.spec}: {exc}") from exc
                time.sleep(min(30, 2 ** attempt) + random.random())
        raise AssertionError("unreachable")

    # ------------------------------------------------------------------
    def _openai_compatible(self, system, messages):
        default_base, key_name = OPENAI_COMPATIBLE[self.provider]
        base = env("YUNWU_API_BASE_URL", default_base) if self.provider == "yunwu" else default_base
        key = env(key_name)
        if not key:
            raise LLMFatal(f"missing {key_name}")
        body = {"model": self.model, "messages": [{"role": "system", "content": system}] + messages}
        if self.temperature is not None:
            body["temperature"] = self.temperature
        resp = requests.post(f"{base.rstrip('/')}/chat/completions", json=body, timeout=self.timeout,
                             headers={"Authorization": f"Bearer {key}"})
        if resp.status_code == 429 or resp.status_code >= 500:
            raise LLMError(f"HTTP {resp.status_code}: {resp.text[:200]}")
        if resp.status_code >= 400:
            raise LLMFatal(f"HTTP {resp.status_code}: {resp.text[:500]}")
        data = resp.json()
        usage = data.get("usage") or {}
        return (data["choices"][0]["message"]["content"] or "",
                usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0))

    def _anthropic(self, system, messages):
        key = env("ANTHROPIC_API_KEY")
        if not key:
            raise LLMFatal("missing ANTHROPIC_API_KEY")
        body = {"model": self.model, "max_tokens": self.max_tokens, "system": system, "messages": messages}
        if self.temperature is not None:
            body["temperature"] = self.temperature
        resp = requests.post("https://api.anthropic.com/v1/messages", json=body, timeout=self.timeout,
                             headers={"x-api-key": key, "anthropic-version": "2023-06-01"})
        if resp.status_code == 429 or resp.status_code >= 500:
            raise LLMError(f"HTTP {resp.status_code}: {resp.text[:200]}")
        if resp.status_code >= 400:
            raise LLMFatal(f"HTTP {resp.status_code}: {resp.text[:500]}")
        data = resp.json()
        text = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
        usage = data.get("usage") or {}
        return text, usage.get("input_tokens", 0), usage.get("output_tokens", 0)


def parse_model_spec(spec: str):
    if ":" in spec:
        provider, model = spec.split(":", 1)
        provider = provider.lower()
        if provider not in OPENAI_COMPATIBLE and provider not in ("anthropic", "mock"):
            raise ValueError(f"unknown provider {provider!r} in {spec!r}")
        return provider, model
    if env("YUNWU_API_KEY"):
        return "yunwu", spec
    name = spec.lower()
    for prefix, provider in (("claude", "anthropic"), ("gemini", "gemini"), ("deepseek", "deepseek"),
                             ("gpt", "openai"), ("o", "openai"), ("mock", "mock")):
        if name.startswith(prefix):
            return provider, spec
    raise ValueError(f"cannot infer provider for {spec!r}; use provider:model")


def extract_json(text: str) -> dict:
    """Return the last top-level JSON object in ``text``."""
    if not text:
        raise ValueError("empty response")
    fenced = re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    candidates = fenced or []
    if not candidates:
        depth, start = 0, None
        for i, ch in enumerate(text):
            if ch == "{":
                if depth == 0:
                    start = i
                depth += 1
            elif ch == "}" and depth:
                depth -= 1
                if depth == 0:
                    candidates.append(text[start:i + 1])
    for raw in reversed(candidates):
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    raise ValueError("no JSON object found")


# ---------------------------------------------------------------------------
# Offline stub: produces valid, mildly random decisions from the prompt text.
# ---------------------------------------------------------------------------
_MOCK_RNG = random.Random(0)


def mock_reply(prompt: str) -> str:
    if "TASK: LOAN" in prompt:
        return json.dumps({"loan": "no"})
    if "TASK: ESTIMATE" in prompt:
        return json.dumps({"buy": [], "sell": [], "loan": "no"})
    if "TASK: POST" in prompt and "public_post" in prompt:
        return json.dumps({"public_post": "B looks cheap to me.", "private_note": "keep scalping the spread"})
    if "TASK: POST" in prompt:
        return "Staying disciplined; watching valuations."
    if "TASK: ORDER" in prompt:
        prices = dict(re.findall(r"^- (\S+): last=([\d.]+)", prompt, re.M))
        if not prices or _MOCK_RNG.random() < 0.3:
            return json.dumps({"action_type": "no"})
        stock = _MOCK_RNG.choice(sorted(prices))
        price = float(prices[stock])
        side = _MOCK_RNG.choice(["buy", "sell"])
        held = re.search(rf"holdings_free\[{re.escape(stock)}\]=(\d+)", prompt)
        if side == "sell" and (not held or int(held.group(1)) == 0):
            side = "buy"
        qty = 10 if side == "buy" else max(1, min(10, int(held.group(1))))
        drift = _MOCK_RNG.uniform(-0.01, 0.01)
        if '"orders"' in prompt:   # arena: two-sided quote, may go short
            return json.dumps({"cancel_open": _MOCK_RNG.random() < 0.3, "orders": [
                {"action_type": "buy", "stock": stock, "amount": 10, "price": round(price * 0.995, 2)},
                {"action_type": "sell", "stock": stock, "amount": 15, "price": round(price * 1.005, 2)}]})
        return json.dumps({"action_type": side, "stock": stock, "amount": qty,
                           "price": round(price * (1 + drift), 2), "reason": "mock"})
    return "{}"
