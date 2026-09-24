"""The smallest configurable model interface that is not a registry.

One HTTP shape -- OpenAI-compatible `/chat/completions` -- because Ollama, Groq
and OpenRouter all speak it. Selecting a provider is setting two environment
variables, not registering a class.

    KGQ_BASE_URL   http://localhost:11434/v1        (Ollama)
                   https://api.groq.com/openai/v1   (Groq)
                   https://openrouter.ai/api/v1     (OpenRouter)
    KGQ_MODEL      the model name that provider expects
    KGQ_API_KEY    omitted for a local model

stdlib only: `urllib.request`. No SDK, no vendor branch, no import that fails
when a key is absent. The deterministic half of the system runs with no provider
configured at all.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from kgq import DEMO_VERSION


class ProviderError(RuntimeError):
    """The model could not be reached or returned something unusable."""


USER_AGENT = f"evidence-first-kg/{DEMO_VERSION}"


@dataclass
class Usage:
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_hits: int = 0
    seconds: float = 0.0

    def add(self, other: "Usage") -> None:
        self.calls += other.calls
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.cache_hits += other.cache_hits
        self.seconds += other.seconds

    def as_dict(self) -> dict:
        return {"llm_calls": self.calls, "input_tokens": self.input_tokens,
                "output_tokens": self.output_tokens, "cache_hits": self.cache_hits,
                "seconds": round(self.seconds, 3)}


@dataclass
class Provider:
    """An OpenAI-compatible chat endpoint, plus a content-addressed cache."""
    base_url: str
    model: str
    api_key: str | None = None
    timeout: int = 120
    cache_path: str | None = None
    usage: Usage = field(default_factory=Usage)

    @classmethod
    def from_env(cls, cache_path: str | None = None) -> "Provider":
        base = os.environ.get("KGQ_BASE_URL")
        model = os.environ.get("KGQ_MODEL")
        if not base or not model:
            raise ProviderError(
                "no model configured. Set KGQ_BASE_URL and KGQ_MODEL (and KGQ_API_KEY "
                "for a hosted provider), or run a deterministic-only command.")
        return cls(base.rstrip("/"), model, os.environ.get("KGQ_API_KEY"),
                   cache_path=cache_path)

    # ── cache: identical input never costs twice ────────────────────────
    def _cache_db(self):
        if not self.cache_path:
            return None
        Path(self.cache_path).parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(self.cache_path)
        con.execute("CREATE TABLE IF NOT EXISTS response("
                    " key TEXT PRIMARY KEY, model TEXT, body TEXT,"
                    " input_tokens INT, output_tokens INT)")
        return con

    def _key(self, messages: list[dict], temperature: float) -> str:
        blob = json.dumps({"m": self.model, "t": temperature, "msgs": messages},
                          sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(blob.encode()).hexdigest()

    def chat(self, messages: list[dict], *, temperature: float = 0.0,
             max_tokens: int = 1200) -> str:
        key = self._key(messages, temperature)
        con = self._cache_db()
        if con is not None:
            row = con.execute("SELECT body, input_tokens, output_tokens FROM response"
                              " WHERE key=?", (key,)).fetchone()
            if row:
                self.usage.cache_hits += 1
                self.usage.input_tokens += row[1] or 0
                self.usage.output_tokens += row[2] or 0
                con.close()
                return row[0]

        payload = json.dumps({"model": self.model, "messages": messages,
                              "temperature": temperature,
                              "max_tokens": max_tokens}).encode()
        # Identify the client honestly. urllib's default "Python-urllib/x.y" is
        # rejected outright by some providers' CDNs (Groq's edge answers it with
        # Cloudflare 1010), which surfaces as an unexplained 403 and an
        # abstention. See eval/j2/DEFECT_001_user_agent.md.
        headers = {"Content-Type": "application/json", "User-Agent": USER_AGENT}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        req = urllib.request.Request(f"{self.base_url}/chat/completions",
                                     data=payload, headers=headers)
        t0 = time.perf_counter()
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                data = json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            raise ProviderError(f"{self.base_url}: HTTP {e.code} {e.read()[:200]!r}") from None
        except Exception as e:                       # network, DNS, timeout, bad JSON
            raise ProviderError(f"{self.base_url}: {type(e).__name__}: {e}") from None
        elapsed = time.perf_counter() - t0

        try:
            body = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            raise ProviderError(f"unexpected response shape: {str(data)[:200]}") from None
        u = data.get("usage") or {}
        self.usage.calls += 1
        self.usage.seconds += elapsed
        self.usage.input_tokens += u.get("prompt_tokens", 0)
        self.usage.output_tokens += u.get("completion_tokens", 0)
        if con is not None:
            con.execute("INSERT OR REPLACE INTO response VALUES(?,?,?,?,?)",
                        (key, self.model, body, u.get("prompt_tokens", 0),
                         u.get("completion_tokens", 0)))
            con.commit(); con.close()
        return body


@dataclass
class ScriptedProvider:
    """A provider that returns prepared replies. For tests and offline demos.

    It exists so the whole answer path -- including validation, rejection and
    regeneration -- can be exercised with no network and no key, and so an
    adversarial reply can be injected deliberately.
    """
    replies: list[str]
    model: str = "scripted"
    usage: Usage = field(default_factory=Usage)
    seen: list = field(default_factory=list)

    def chat(self, messages: list[dict], *, temperature: float = 0.0,
             max_tokens: int = 1200) -> str:
        self.seen.append(messages)
        self.usage.calls += 1
        self.usage.input_tokens += sum(len(m["content"]) for m in messages) // 4
        if not self.replies:
            raise ProviderError("scripted provider exhausted")
        body = self.replies.pop(0)
        self.usage.output_tokens += len(body) // 4
        return body
