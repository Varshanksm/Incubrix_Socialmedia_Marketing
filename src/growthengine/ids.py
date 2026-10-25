"""Content-derived keys. Same text in, same key out, so repeat runs and refreshes never renumber assets."""
from __future__ import annotations

import hashlib
import re
import unicodedata

_ws = re.compile(r"\s+")
_punct = re.compile(r"[^\w\s]")


def normalize(text: str) -> str:
    """Lowercase, strip accents/punctuation, collapse whitespace."""
    t = unicodedata.normalize("NFKD", text)
    t = "".join(ch for ch in t if not unicodedata.combining(ch))
    t = _punct.sub(" ", t.lower())
    return _ws.sub(" ", t).strip()


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def file_sha(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def moment_key(quote: str) -> str:
    return sha(normalize(quote))[:16]


def asset_id(platform_code: str, format_code: str, mkey: str, taken: set[str] | None = None) -> str:
    """{platform}-{format}-{moment_key[:6]}; grows the suffix only if two moments collide on 6 characters."""
    for n in range(6, len(mkey) + 1):
        aid = f"{platform_code}-{format_code}-{mkey[:n]}"
        if not taken or aid not in taken:
            return aid
    raise ValueError(f"cannot make a unique asset id for moment {mkey}")
