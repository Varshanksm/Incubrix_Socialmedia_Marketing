"""JSONL run log (one line per event) plus short console messages."""
from __future__ import annotations

import json
import sys
import threading
from pathlib import Path

from growthengine.db import now


class RunLog:
    def __init__(self, path: Path, run_id: str, quiet: bool = False):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.run_id = run_id
        self.quiet = quiet
        self._lock = threading.Lock()
        self.errors = 0

    def event(self, level: str, stage: str, message: str, **fields) -> None:
        rec = {"ts": now(), "run_id": self.run_id, "level": level, "stage": stage, "message": message, **fields}
        if level == "error":
            self.errors += 1
        with self._lock:
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            if not self.quiet and level in ("info", "warn", "error"):
                tag = {"info": "", "warn": "WARN ", "error": "ERROR "}[level]
                print(f"[{stage}] {tag}{message}", file=sys.stderr if level != "info" else sys.stdout)

    def info(self, stage: str, message: str, **f) -> None:
        self.event("info", stage, message, **f)

    def warn(self, stage: str, message: str, **f) -> None:
        self.event("warn", stage, message, **f)

    def error(self, stage: str, message: str, **f) -> None:
        self.event("error", stage, message, **f)

    def debug(self, stage: str, message: str, **f) -> None:
        self.event("debug", stage, message, **f)
