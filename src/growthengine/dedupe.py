"""Near-duplicate detection against the whole library (same platform only: the same idea may appear on two
different platforms by design)."""
from __future__ import annotations

from rapidfuzz import fuzz

from growthengine.ids import normalize

HOOK_THRESHOLD = 90
BODY_THRESHOLD = 85


def near_duplicate(platform: str, hook: str, body: str, library: list[dict]) -> tuple[str, float] | None:
    """library rows: {asset_id, platform, hook, body}. Returns (asset_id, score) of the first near-duplicate."""
    h, b = normalize(hook), normalize(body)[:600]
    for row in library:
        if row["platform"] != platform:
            continue
        hs = fuzz.token_sort_ratio(h, normalize(row["hook"]))
        if hs >= HOOK_THRESHOLD:
            return row["asset_id"], hs
        if b and row["body"]:
            bs = fuzz.ratio(b, normalize(row["body"])[:600])
            if bs >= BODY_THRESHOLD:
                return row["asset_id"], bs
    return None
