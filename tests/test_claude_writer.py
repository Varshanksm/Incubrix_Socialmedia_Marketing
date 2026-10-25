"""The Claude writer against a stub client (no network, no key): request shape, usage/cost, refusals, errors."""
from types import SimpleNamespace

import httpx2
import anthropic
import pytest

from growthengine.llm import Faults, LLMError, LLMOutputError
from growthengine.models import AssetCopy, Moment


class StubMessages:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def parse(self, **kw):
        self.calls.append(kw)
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def usage(i=1000, o=500, cr=800, cw=0):
    return SimpleNamespace(input_tokens=i, output_tokens=o, cache_read_input_tokens=cr, cache_creation_input_tokens=cw)


def copy():
    return AssetCopy(hook="h", body="b", posts=[], slides=[], on_screen_hook="", title="", hashtags=[])


def moment():
    return Moment(key="k", source_id="s", kind="text", quote="q", point="p", takeaway="t", details=["d"],
                  pillar="P1", formats=["text"], score=3, section_idx=0, para_idx=0)


@pytest.fixture
def writer(settings, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")
    from growthengine.llm.claude import ClaudeWriter
    w = ClaudeWriter(settings, settings.config.prices["models"]["claude-opus-5-5"], Faults("", lambda s: False))
    from growthengine.ingest import load
    w.prepare(load("samples/I2_blog.md", settings))
    return w


def attach(w, responses):
    stub = StubMessages(responses)
    w.client = SimpleNamespace(beta=SimpleNamespace(messages=stub), messages=stub)
    return stub


def test_request_uses_structured_output_cache_and_fallback(writer):
    stub = attach(writer, [SimpleNamespace(stop_reason="end_turn", usage=usage(), parsed_output=copy())])
    out = writer.write(moment(), "LinkedIn", "Text post", writer.settings.config.platforms["LinkedIn"],
                       writer.settings.config.formats["Text post"])
    assert out.hook == "h"
    kw = stub.calls[0]
    assert kw["model"] == "claude-opus-5-5" and kw["output_format"] is AssetCopy
    assert kw["fallbacks"] == "default" and kw["betas"] == ["server-side-fallback-2026-07-01"]
    assert kw["system"][-1]["cache_control"] == {"type": "ephemeral"}       # the whole input is the cached prefix
    assert "Section 0" in kw["system"][-1]["text"]
    u = writer.usage.take()
    assert u["calls"] == 1 and u["cache_read"] == 800
    assert u["cost_usd"] == pytest.approx((1000 * 4 + 500 * 20 + 800 * 0.2) / 1e6)


def test_refusal_and_truncation_reject_the_item(writer):
    attach(writer, [SimpleNamespace(stop_reason="refusal", usage=usage(), parsed_output=None),
                    SimpleNamespace(stop_reason="max_tokens", usage=usage(), parsed_output=None)])
    args = (moment(), "X", "Thread", writer.settings.config.platforms["X"], writer.settings.config.formats["Thread"])
    with pytest.raises(LLMOutputError, match="refusal"):
        writer.write(*args)
    with pytest.raises(LLMOutputError, match="cut off"):
        writer.write(*args)


def test_api_errors_stop_the_run_so_it_can_resume(writer):
    req = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    attach(writer, [anthropic.APIConnectionError(request=req)])
    with pytest.raises(LLMError):
        writer.write(moment(), "X", "Thread", writer.settings.config.platforms["X"],
                     writer.settings.config.formats["Thread"])


def test_prompts_build_for_every_platform_and_format(settings):
    from growthengine.llm import prompts
    cfg = settings.config
    for e in cfg.mix["week"]:
        text = prompts.write_prompt(moment(), e["platform"], e["format"], cfg.platforms[e["platform"]],
                                    cfg.formats[e["format"]])
        assert "FORMAT:" in text and "HASHTAGS:" in text
    assert "Do NOT mention IncuBrix" in prompts.brand_rules(cfg)      # no verified claims yet


def test_missing_key_gives_a_clear_message(settings, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    from growthengine.llm.claude import ClaudeWriter
    with pytest.raises(SystemExit, match="ANTHROPIC_API_KEY"):
        ClaudeWriter(settings, {}, Faults("", lambda s: False))
