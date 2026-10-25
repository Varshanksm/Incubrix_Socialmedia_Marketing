"""Claude writer: structured outputs (validated Pydantic models), a cached system prefix (brand rules + the
whole input), server-side refusal fallback, and per-call token/cost accounting."""
from __future__ import annotations

import os

import anthropic

from growthengine.llm import Faults, LLMError, LLMOutputError, Usage
from growthengine.llm import prompts
from growthengine.models import AssetCopy, Cue, MediaMomentList, MomentList

FALLBACK_BETA = "server-side-fallback-2026-07-01"


class ClaudeWriter:
    name = "claude"

    def __init__(self, settings, prices: dict, faults: Faults):
        if not os.environ.get("ANTHROPIC_API_KEY") and not os.environ.get("ANTHROPIC_AUTH_TOKEN"):
            raise SystemExit("GE_LLM=claude needs ANTHROPIC_API_KEY in .env (or run with --llm mock)")
        self.settings = settings
        self.model = settings.model
        self.effort = settings.effort
        self.prices = prices
        self.faults = faults
        self.usage = Usage()
        self.fallbacks = os.environ.get("GE_LLM_FALLBACKS", "default").strip().lower() != "off"
        self.client = anthropic.Anthropic(max_retries=3, timeout=180.0)
        self.system = None

    def prepare(self, doc) -> None:
        """Build the stable prefix once per run. Identical bytes on every call -> prompt-cache hits."""
        self.system = [
            {"type": "text", "text": prompts.brand_rules(self.settings.config)},
            {"type": "text", "text": prompts.doc_block(doc), "cache_control": {"type": "ephemeral"}},
        ]

    def _call(self, stage: str, user: str, schema):
        self.faults.check(stage)
        kwargs = dict(model=self.model, max_tokens=16000, system=self.system,
                      messages=[{"role": "user", "content": user}],
                      output_format=schema, output_config={"effort": self.effort})
        try:
            if self.fallbacks:
                resp = self.client.beta.messages.parse(betas=[FALLBACK_BETA], fallbacks="default", **kwargs)
            else:
                resp = self.client.messages.parse(**kwargs)
        except anthropic.AuthenticationError as e:
            raise LLMError(f"Anthropic API rejected the key (check ANTHROPIC_API_KEY): {e}") from e
        except anthropic.BadRequestError as e:
            raise LLMOutputError(f"request rejected: {e}") from e
        except anthropic.APIError as e:   # connection, timeout, rate limit, 5xx (after the SDK's retries)
            raise LLMError(f"{type(e).__name__}: {e}") from e

        u = resp.usage
        self.usage.add(self.prices, input_tokens=u.input_tokens or 0, output_tokens=u.output_tokens or 0,
                       cache_read=getattr(u, "cache_read_input_tokens", 0) or 0,
                       cache_write=getattr(u, "cache_creation_input_tokens", 0) or 0)
        if resp.stop_reason == "refusal":
            raise LLMOutputError("model declined this item (refusal)")
        if resp.stop_reason == "max_tokens":
            raise LLMOutputError("answer was cut off (max_tokens)")
        parsed = getattr(resp, "parsed_output", None)
        if parsed is None:
            raise LLMOutputError("answer did not match the schema")
        return parsed

    def extract_text_moments(self, section):
        return self._call("moments", prompts.extract_text_prompt(section), MomentList).moments

    def extract_media_moments(self, cues: list[Cue]):
        return self._call("moments", prompts.extract_media_prompt(cues[0].idx, cues[-1].idx),
                          MediaMomentList).moments

    def write(self, m, platform: str, fmt: str, pcfg: dict, fcfg: dict) -> AssetCopy:
        return self._call("write", prompts.write_prompt(m, platform, fmt, pcfg, fcfg), AssetCopy)
