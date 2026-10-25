"""Writers. `claude` uses the Anthropic API; `mock` is deterministic and free (tests, keyless runs)."""
from __future__ import annotations

import threading
from dataclasses import dataclass, field


class LLMError(Exception):
    """The model call failed after retries (network, rate limit, auth, injected fault). The run can resume."""


class LLMOutputError(Exception):
    """The model answered but the answer is unusable (refusal, truncated, invalid). Rejects one item."""


@dataclass
class Usage:
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read: int = 0
    cache_write: int = 0
    cost_usd: float = 0.0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def add(self, prices: dict, input_tokens=0, output_tokens=0, cache_read=0, cache_write=0) -> None:
        cost = (input_tokens * prices.get("input", 0) + output_tokens * prices.get("output", 0)
                + cache_read * prices.get("cache_read", 0) + cache_write * prices.get("cache_write", 0)) / 1e6
        with self._lock:
            self.calls += 1
            self.input_tokens += input_tokens
            self.output_tokens += output_tokens
            self.cache_read += cache_read
            self.cache_write += cache_write
            self.cost_usd += cost

    def take(self) -> dict:
        """Return and reset the counters (flushed into the runs table by the pipeline)."""
        with self._lock:
            d = {k: getattr(self, k) for k in ("calls", "input_tokens", "output_tokens", "cache_read",
                                                "cache_write", "cost_usd")}
            self.calls = self.input_tokens = self.output_tokens = self.cache_read = self.cache_write = 0
            self.cost_usd = 0.0
        return d


class Faults:
    """GE_FAULT=llm_timeout@write:3 -> the 3rd call in the 'write' stage raises LLMError, once per run."""

    def __init__(self, spec: str, already_fired):
        self.spec = spec.strip()
        self.already_fired = already_fired      # callable(spec) -> bool, backed by the DB
        self.fired_now = False
        self.counts: dict[str, int] = {}
        self._lock = threading.Lock()
        self.kind = self.stage = None
        self.nth = 0
        if self.spec:
            kind, _, rest = self.spec.partition("@")
            stage, _, n = rest.partition(":")
            self.kind, self.stage, self.nth = kind, stage, int(n or 1)

    def check(self, stage: str) -> None:
        if not self.spec or stage != self.stage:
            return
        with self._lock:
            self.counts[stage] = self.counts.get(stage, 0) + 1
            hit = self.counts[stage] == self.nth and not self.fired_now and not self.already_fired(self.spec)
            if hit:
                self.fired_now = True
        if hit:
            raise LLMError(f"injected fault {self.spec} (simulated {self.kind})")


def get_llm(settings, faults: Faults):
    prices = settings.config.prices["models"]
    if settings.llm == "claude":
        from growthengine.llm.claude import ClaudeWriter
        return ClaudeWriter(settings, prices.get(settings.model, {}), faults)
    if settings.llm == "mock":
        from growthengine.llm.mock import MockWriter
        return MockWriter(settings, prices["mock"], faults)
    raise SystemExit(f"unknown GE_LLM={settings.llm!r} (use claude or mock)")
