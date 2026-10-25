"""SQLite state: the single source of truth for calendar.csv, the workbook rows and run summaries."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS inputs (
  input_key TEXT PRIMARY KEY, source_id TEXT, kind TEXT, title TEXT, path TEXT,
  words INTEGER, duration_ms INTEGER, rights_note TEXT, ai_media INTEGER DEFAULT 0, created_at TEXT
);
CREATE TABLE IF NOT EXISTS unit_cache (
  unit_hash TEXT, llm TEXT, moments_json TEXT, created_at TEXT, PRIMARY KEY (unit_hash, llm)
);
CREATE TABLE IF NOT EXISTS moments (
  key TEXT PRIMARY KEY, source_id TEXT, kind TEXT, quote TEXT, point TEXT, takeaway TEXT,
  details_json TEXT, pillar TEXT, formats_json TEXT, score INTEGER, section_idx INTEGER, para_idx INTEGER,
  start_ms INTEGER, end_ms INTEGER, unit_hash TEXT, first_run TEXT, created_at TEXT
);
CREATE TABLE IF NOT EXISTS assets (
  asset_id TEXT PRIMARY KEY, moment_key TEXT, source_id TEXT, input_key TEXT, platform TEXT, format TEXT,
  pillar TEXT, status TEXT, reason TEXT, copy_json TEXT, caption TEXT, cta TEXT, utm_link TEXT,
  files_json TEXT, checks_json TEXT, fonts_json TEXT, scheduled_at TEXT, version INTEGER DEFAULT 1,
  created_run TEXT, updated_run TEXT, content_hash TEXT, llm TEXT,
  test_url TEXT, impressions TEXT, engagement_rate TEXT, clicks TEXT,
  created_at TEXT, updated_at TEXT
);
CREATE TABLE IF NOT EXISTS runs (
  run_id TEXT PRIMARY KEY, input_path TEXT, source_id TEXT, input_key TEXT, started_at TEXT, ended_at TEXT,
  status TEXT, llm TEXT, model TEXT, count_target INTEGER, input_words INTEGER, input_duration_ms INTEGER,
  produced INTEGER DEFAULT 0, updated INTEGER DEFAULT 0, unchanged INTEGER DEFAULT 0,
  rejected INTEGER DEFAULT 0, duplicates INTEGER DEFAULT 0, runtime_s REAL DEFAULT 0,
  calls INTEGER DEFAULT 0, input_tokens INTEGER DEFAULT 0, output_tokens INTEGER DEFAULT 0,
  cache_read INTEGER DEFAULT 0, cache_write INTEGER DEFAULT 0, cost_usd REAL DEFAULT 0, error TEXT,
  args_json TEXT
);
CREATE TABLE IF NOT EXISTS stages (
  run_id TEXT, stage TEXT, status TEXT, started_at TEXT, ended_at TEXT, error TEXT,
  PRIMARY KEY (run_id, stage)
);
CREATE TABLE IF NOT EXISTS faults (run_id TEXT, spec TEXT, PRIMARY KEY (run_id, spec));
"""


def now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class DB:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    # ----- generic -----
    def q(self, sql: str, args: tuple = ()) -> list[sqlite3.Row]:
        return self.conn.execute(sql, args).fetchall()

    def one(self, sql: str, args: tuple = ()):
        return self.conn.execute(sql, args).fetchone()

    def x(self, sql: str, args: tuple = ()) -> None:
        self.conn.execute(sql, args)
        self.conn.commit()

    def upsert(self, table: str, row: dict, key: str | tuple[str, ...]) -> None:
        cols = list(row)
        keys = (key,) if isinstance(key, str) else key
        updates = ", ".join(f"{c}=excluded.{c}" for c in cols if c not in keys)
        sql = (f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)}) "
               f"ON CONFLICT ({', '.join(keys)}) DO " + (f"UPDATE SET {updates}" if updates else "NOTHING"))
        self.conn.execute(sql, tuple(row[c] for c in cols))
        self.conn.commit()

    def update(self, table: str, key_col: str, key_val, **fields) -> None:
        sets = ", ".join(f"{k}=?" for k in fields)
        self.conn.execute(f"UPDATE {table} SET {sets} WHERE {key_col}=?", (*fields.values(), key_val))
        self.conn.commit()

    # ----- runs / stages -----
    def stage(self, run_id: str, stage: str, status: str, error: str | None = None) -> None:
        row = {"run_id": run_id, "stage": stage, "status": status, "error": error}
        if status == "running":
            row["started_at"] = now()
        else:
            row["ended_at"] = now()
        self.upsert("stages", row, ("run_id", "stage"))

    def add_run_usage(self, run_id: str, usage: dict) -> None:
        self.conn.execute(
            "UPDATE runs SET calls=calls+?, input_tokens=input_tokens+?, output_tokens=output_tokens+?, "
            "cache_read=cache_read+?, cache_write=cache_write+?, cost_usd=cost_usd+? WHERE run_id=?",
            (usage.get("calls", 1), usage.get("input_tokens", 0), usage.get("output_tokens", 0),
             usage.get("cache_read", 0), usage.get("cache_write", 0), usage.get("cost_usd", 0.0), run_id))
        self.conn.commit()

    def fault_fired(self, run_id: str, spec: str) -> bool:
        return self.one("SELECT 1 FROM faults WHERE run_id=? AND spec=?", (run_id, spec)) is not None

    def mark_fault(self, run_id: str, spec: str) -> None:
        self.x("INSERT OR IGNORE INTO faults (run_id, spec) VALUES (?, ?)", (run_id, spec))


def loads(s: str | None, default):
    return json.loads(s) if s else default
