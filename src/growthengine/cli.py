"""growthengine command line."""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

from growthengine import __version__


def _settings(args):
    from growthengine.settings import load_settings
    return load_settings(llm=getattr(args, "llm", None))


def cmd_run(args) -> int:
    from growthengine.ingest import InputError
    from growthengine.pipeline import Pipeline, RunFailed

    if not args.input and not args.resume:
        print("growthengine run: give --input <file-or-url> or --resume <run_id>", file=sys.stderr)
        return 2
    s = _settings(args)
    if args.fault is not None:
        s.fault = args.fault
    p = Pipeline(s, quiet=args.quiet)
    try:
        r = p.run(args.input, count=args.count, transcript=args.transcript, source_id=args.source_id,
                  rights=args.rights, ai_media=args.ai_media, week=args.week, rewrite=args.rewrite,
                  campaign=args.campaign, resume=args.resume)
    except InputError as e:
        print(f"input error: {e}", file=sys.stderr)
        return 2
    except RunFailed as e:
        print(f"\nRun {e.run_id} stopped at '{e.stage}': {e}\n"
              f"Fix the cause, then continue where it stopped:\n  growthengine run --resume {e.run_id}",
              file=sys.stderr)
        return 3
    print_summary(r)
    return 0


def print_summary(r: dict) -> None:
    mins = (r["runtime_s"] or 0) / 60
    tokens = (r["input_tokens"] or 0) + (r["output_tokens"] or 0) + (r["cache_read"] or 0) + (r["cache_write"] or 0)
    print(f"""
run {r['run_id']}  [{r['status']}]  {r['source_id']}  ({r['llm']}: {r['model']})
  input            {r['input_words']} words{f", {r['input_duration_ms'] / 60000:.1f} min" if r['input_duration_ms'] else ''}
  produced (new)   {r['produced']}
  updated          {r['updated']}
  unchanged        {r['unchanged']}
  rejected         {r['rejected']}
  duplicates       {r['duplicates']}
  runtime          {mins:.2f} min
  model calls      {r['calls']}  ({tokens:,} tokens, {r['cache_read']:,} from cache)
  direct cost      ${r['cost_usd']:.4f}
  library (ready)  {r['library_ready']}
  summary          {r['summary_path']}
  log              {r['log']}""")


def cmd_audit(args) -> int:
    from growthengine.audit import audit
    from growthengine.db import DB
    s = _settings(args)
    res = audit(s, DB(s.db_path), args.sample, args.seed)
    if not res["sampled"]:
        print("no ready assets yet")
        return 1
    print(f"audit (seed {res['seed']}): {res['passed']}/{res['sampled']} passed every check = {res['rate']:.0%}")
    for aid, bad in res["failures"].items():
        print(f"  {aid}: " + "; ".join(bad))
    return 0 if res["rate"] >= args.min_rate else 1


def cmd_trace(args) -> int:
    from growthengine.audit import trace
    from growthengine.db import DB
    s = _settings(args)
    print(trace(s, DB(s.db_path), args.asset_id))
    return 0


def cmd_export(args) -> int:
    from growthengine import export
    from growthengine.db import DB
    s = _settings(args)
    db = DB(s.db_path)
    res = export.export_all(s, db)
    print(f"calendar.csv: {res['rows']} rows -> {s.rel(res['calendar'])}")
    if args.workbook:
        out = Path(args.out) if args.out else s.out_dir / "workbook_filled.xlsx"
        n = export.fill_workbook(s, db, Path(args.workbook), out)
        print(f"workbook: {n} rows written to 02_ASSETS -> {out}  (open it in Excel to recalculate)")
    return 0


def cmd_status(args) -> int:
    from growthengine.db import DB
    s = _settings(args)
    db = DB(s.db_path)
    rows = db.q("SELECT platform, format, status, COUNT(*) n FROM assets GROUP BY 1, 2, 3 ORDER BY 1, 2, 3")
    if not rows:
        print("library is empty: run  growthengine run --input <file>")
        return 0
    print(f"{'platform':16} {'format':12} {'status':9} n")
    for r in rows:
        print(f"{r['platform']:16} {r['format']:12} {r['status']:9} {r['n']}")
    ready = db.q("SELECT platform, format FROM assets WHERE status='ready'")
    fams = [s.config.formats[r["format"]]["family"] for r in ready]
    print(f"\nready: {len(ready)} on {len({r['platform'] for r in ready})} platforms | "
          f"short videos {fams.count('video')} | carousels+graphics {fams.count('carousel') + fams.count('graphic')}")
    print("\nlast runs:")
    for r in db.q("SELECT run_id, source_id, status, produced, rejected, duplicates, runtime_s, cost_usd "
                  "FROM runs ORDER BY started_at DESC LIMIT 6"):
        print(f"  {r['run_id']}  {r['source_id'] or '-':20} {r['status']:7} new {r['produced']:>3}  "
              f"rejected {r['rejected']:>3}  dupes {r['duplicates']:>3}  "
              f"{(r['runtime_s'] or 0) / 60:5.2f} min  ${r['cost_usd'] or 0:.4f}")
    return 0


def cmd_record(args) -> int:
    from growthengine import export
    from growthengine.db import DB, now
    s = _settings(args)
    db = DB(s.db_path)
    if db.one("SELECT 1 FROM assets WHERE asset_id=?", (args.asset_id,)) is None:
        raise SystemExit(f"no asset {args.asset_id}")
    fields = {k: v for k, v in {"test_url": args.url, "impressions": args.views, "engagement_rate": args.er,
                                "clicks": args.clicks}.items() if v is not None}
    db.update("assets", "asset_id", args.asset_id, updated_at=now(), **fields)
    export.export_all(s, db)
    print(f"recorded {args.asset_id}: {fields}")
    return 0


def cmd_reject(args) -> int:
    from growthengine import export
    from growthengine.db import DB, now
    s = _settings(args)
    db = DB(s.db_path)
    if db.one("SELECT 1 FROM assets WHERE asset_id=?", (args.asset_id,)) is None:
        raise SystemExit(f"no asset {args.asset_id}")
    db.update("assets", "asset_id", args.asset_id, status="rejected", reason=f"human review: {args.reason}",
              updated_at=now())
    src = s.content_dir / "assets" / args.asset_id
    if src.exists():
        dst = s.out_dir / "rejected" / args.asset_id
        if dst.exists():
            shutil.rmtree(dst)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst))
    export.export_all(s, db)
    print(f"rejected {args.asset_id}: {args.reason}")
    return 0


def cmd_rerender(args) -> int:
    from growthengine import export
    from growthengine.db import DB, now
    from growthengine.fonts import resolve
    from growthengine.render.carousel import rerender
    s = _settings(args)
    db = DB(s.db_path)
    a = db.one("SELECT * FROM assets WHERE asset_id=?", (args.asset_id,))
    if a is None or s.config.formats[a["format"]]["family"] not in ("carousel", "graphic"):
        raise SystemExit("rerender works for carousels and graphics (edit source/slides.json first)")
    out = s.content_dir / "assets" / args.asset_id
    files = rerender(out, resolve(s), as_pdf=a["platform"] == "LinkedIn")
    db.update("assets", "asset_id", args.asset_id, files_json=json.dumps(files), updated_at=now())
    export.export_all(s, db)
    print(f"re-rendered {args.asset_id} from source/slides.json (run `growthengine audit` to re-check)")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="growthengine", description=__doc__)
    p.add_argument("--version", action="version", version=f"growthengine {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="turn one long input into a week of platform-ready assets")
    r.add_argument("--input", help="file (.md .txt .html .pdf .vtt .srt, audio/video) or article URL")
    r.add_argument("--resume", metavar="RUN_ID", help="continue a run that stopped")
    r.add_argument("--count", type=int, help="assets to make from this input (default: config/mix.yaml)")
    r.add_argument("--transcript", help="transcript (.vtt/.srt) for an audio/video input")
    r.add_argument("--source-id", help="stable name for this input (default: from the file name, minus _v2 etc.)")
    r.add_argument("--rights", help='who owns this input and under what terms, e.g. "Own recording"')
    r.add_argument("--ai-media", action="store_true", help="input contains AI voice/avatar/images (AI label needed)")
    r.add_argument("--week", help="first posting week (Monday, YYYY-MM-DD); default next Monday")
    r.add_argument("--campaign", help="utm_campaign (default from config/utm.yaml)")
    r.add_argument("--rewrite", action="store_true", help="rewrite this input's existing assets in place")
    r.add_argument("--llm", choices=["claude", "mock"], help="override GE_LLM")
    r.add_argument("--fault", help="fault injection, e.g. llm_timeout@write:3 (overrides GE_FAULT)")
    r.add_argument("--quiet", action="store_true")
    r.set_defaults(fn=cmd_run)

    a = sub.add_parser("audit", help="rehearse the random audit on ready assets")
    a.add_argument("--sample", type=int, default=20)
    a.add_argument("--seed", type=int)
    a.add_argument("--min-rate", type=float, default=0.85)
    a.set_defaults(fn=cmd_audit)

    t = sub.add_parser("trace", help="follow one asset back to its source and evidence")
    t.add_argument("asset_id")
    t.set_defaults(fn=cmd_trace)

    e = sub.add_parser("export", help="rewrite calendar.csv (and optionally fill a workbook copy)")
    e.add_argument("--workbook", help="path to a COPY of the submission workbook")
    e.add_argument("--out", help="where to save the filled workbook (default out/workbook_filled.xlsx)")
    e.set_defaults(fn=cmd_export)

    st = sub.add_parser("status", help="library counts and recent runs")
    st.set_defaults(fn=cmd_status)

    rec = sub.add_parser("record", help="record real-audience test results for a published asset")
    rec.add_argument("asset_id")
    rec.add_argument("--url")
    rec.add_argument("--views")
    rec.add_argument("--er", help="engagement rate as a fraction, e.g. 0.034")
    rec.add_argument("--clicks")
    rec.set_defaults(fn=cmd_record)

    rj = sub.add_parser("reject", help="reject an asset after human review (moves it out of the main output)")
    rj.add_argument("asset_id")
    rj.add_argument("--reason", required=True)
    rj.set_defaults(fn=cmd_reject)

    rr = sub.add_parser("rerender", help="re-render a carousel/graphic after editing source/slides.json")
    rr.add_argument("asset_id")
    rr.set_defaults(fn=cmd_rerender)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8")   # Windows consoles default to cp1252
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    return args.fn(args)
