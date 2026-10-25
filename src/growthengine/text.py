"""Small text helpers shared by the writers and the checks."""
from __future__ import annotations

import re

_sent = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"“'(])")
_emoji = re.compile("[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F000-\U0001F2FF]")


def sentences(text: str) -> list[str]:
    return [s.strip() for s in _sent.split(text.strip()) if s.strip()]


def words(text: str) -> list[str]:
    return re.findall(r"[A-Za-z0-9][A-Za-z0-9'’\-]*", text)


def trim_words(text: str, n: int) -> str:
    """First n words, without trailing punctuation."""
    w = text.split()
    out = " ".join(w[:n]).rstrip(",;:—-")
    return out


def emoji_count(text: str) -> int:
    return len(_emoji.findall(text))


def avg_sentence_words(text: str) -> float:
    ss = [s for p in text.split("\n") for s in sentences(p) if words(s)]
    if not ss:
        return 0.0
    return sum(len(words(s)) for s in ss) / len(ss)


def fit(text: str, limit: int) -> str:
    """Cut to `limit` characters at a sentence (or word) boundary."""
    if len(text) <= limit:
        return text
    out = ""
    for s in sentences(text):
        if len(out) + len(s) + 1 > limit:
            break
        out = (out + " " + s).strip()
    if not out:
        out = text[:limit].rsplit(" ", 1)[0]
    return out
