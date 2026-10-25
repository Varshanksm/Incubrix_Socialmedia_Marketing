"""Text posts and threads: the final file is the exact text to paste."""
from __future__ import annotations

import json
from pathlib import Path


def render_text(out: Path, composed: dict, copy) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    src = out / "source"
    src.mkdir(exist_ok=True)
    if composed["posts"]:
        final = out / "thread.txt"
        final.write_text("\n\n---\n\n".join(composed["posts"]) + "\n", encoding="utf-8")
    else:
        final = out / "post.txt"
        final.write_text(composed["caption"] + "\n", encoding="utf-8")
    (src / "copy.json").write_text(json.dumps(copy.model_dump(), indent=2, ensure_ascii=False), encoding="utf-8")
    return {"final": final.name, "source": "source/copy.json"}
