"""`audit` rehearses IncuBrix's random 20-asset audit from the files on disk; `trace` follows one asset back
to its evidence (live review)."""
from __future__ import annotations

import csv
import random

from growthengine import checks
from growthengine.db import DB, loads
from growthengine.export import source_label


def audit(settings, db: DB, sample: int = 20, seed: int | None = None) -> dict:
    ready = [r["asset_id"] for r in db.q("SELECT asset_id FROM assets WHERE status='ready' ORDER BY asset_id")]
    if not ready:
        return {"sampled": 0, "passed": 0, "rate": 0.0, "failures": {}}
    rng = random.Random(seed)
    pick = rng.sample(ready, min(sample, len(ready)))
    cal_path = settings.content_dir / "calendar.csv"
    cal = {}
    if cal_path.is_file():
        with cal_path.open(encoding="utf-8-sig", newline="") as f:
            cal = {row["asset_id"]: row for row in csv.DictReader(f)}
    failures: dict[str, list[str]] = {}
    for aid in pick:
        a = db.one("SELECT a.*, i.path AS input_path, i.rights_note FROM assets a "
                   "JOIN inputs i ON i.input_key=a.input_key WHERE a.asset_id=?", (aid,))
        m = db.one("SELECT * FROM moments WHERE key=?", (a["moment_key"],))
        out = settings.content_dir / "assets" / aid
        caption_file = out / "caption.txt"
        caption = caption_file.read_text(encoding="utf-8").rstrip("\n") if caption_file.is_file() else ""
        fam = settings.config.formats[a["format"]]["family"]
        posts = caption.split("\n\n---\n\n") if fam == "thread" else []
        res = checks.run_all(
            settings, platform=a["platform"], fmt=a["format"], copy=loads(a["copy_json"], {}),
            composed={"caption": caption, "posts": posts, "hashtags": [w for w in caption.split() if w.startswith("#")]},
            moment={"quote": m["quote"], "point": m["point"], "details": loads(m["details_json"], [])},
            out=out, files=loads(a["files_json"], {}), fonts_used=loads(a["fonts_json"], []),
            input_path=a["input_path"], input_note=a["rights_note"] or "", link=a["utm_link"], asset_id=aid,
            target=settings.config.mix.get("targets", {}).get(a["format"], ""), scheduled_at=a["scheduled_at"])
        bad = checks.failures(res)
        row = cal.get(aid)
        if row is None:
            bad.append("export: missing from calendar.csv")
        elif row["caption"] != caption:
            bad.append("export: calendar.csv caption differs from caption.txt")
        if bad:
            failures[aid] = bad
    passed = len(pick) - len(failures)
    return {"sampled": len(pick), "passed": passed, "rate": passed / len(pick), "failures": failures,
            "seed": seed, "sample": pick}


def trace(settings, db: DB, asset_id: str) -> str:
    a = db.one("SELECT * FROM assets WHERE asset_id=?", (asset_id,))
    if a is None:
        raise SystemExit(f"no asset {asset_id}")
    m = db.one("SELECT * FROM moments WHERE key=?", (a["moment_key"],))
    i = db.one("SELECT * FROM inputs WHERE input_key=?", (a["input_key"],))
    copy = loads(a["copy_json"], {})
    res = loads(a["checks_json"], [])
    claims = settings.config.claims.get("verified") or []
    text = (a["caption"] or "").lower()
    used_claims = [c for c in claims if any(p.lower() in text for p in c.get("match", []))]
    rights = checks.rights_match(settings.config.rights.get("inputs"), i["path"]) if i else None
    lines = [
        f"{asset_id}  [{a['status']}]  {a['platform']} / {a['format']} / {a['pillar']}  v{a['version']}",
        f"  hook:        {copy.get('hook', '')}",
        f"  scheduled:   {a['scheduled_at']}",
        f"  utm:         {a['utm_link']}",
        f"  files:       content/assets/{asset_id}/ -> {loads(a['files_json'], {})}",
        "  source",
        f"    input:     {i['path'] if i else '?'}  ({i['title'] if i else ''})",
        f"    location:  {source_label(m, a['source_id'], '')}",
        f"    quote:     \"{(m['quote'] if m else '')[:220]}\"",
        "  claims about the product:",
    ]
    lines += [f"    - {c['text']}  -> evidence: {c.get('evidence', '?')} (seen {c.get('seen', '?')})"
              for c in used_claims] or ["    none (the copy makes no product-feature claim)"]
    lines += [
        "  rights",
        f"    input:     {(rights or {}).get('owner') or (i['rights_note'] if i else '') or 'NOT DECLARED'}",
        f"    fonts:     {', '.join(loads(a['fonts_json'], [])) or 'none'}",
        f"  checks:      {sum(r['ok'] for r in res)}/{len(res)} passed"
        + ("" if all(r["ok"] for r in res) else " -> " + "; ".join(checks.failures(res))),
    ]
    if a["status"] == "rejected":
        lines.append(f"  rejected:    {a['reason']}")
    lines.append(f"  created by run {a['created_run']}, last updated by run {a['updated_run']}")
    return "\n".join(lines)
