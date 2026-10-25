"""The run: ingest -> moments -> plan -> write -> dedupe -> render -> check -> export.

Every stage reads and writes the database, so a run that stops anywhere can be resumed (same run_id) and
a repeat run on the same input does no new work. Asset rows move planned -> written -> rendered -> ready,
or to rejected with a reason."""
from __future__ import annotations

import json
import shutil
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from pathlib import Path

from growthengine import checks, export, ingest
from growthengine.compose import compose, utm_link
from growthengine.db import DB, loads, now
from growthengine.dedupe import near_duplicate
from growthengine.fonts import resolve as resolve_fonts
from growthengine.ids import asset_id as make_asset_id, moment_key, normalize, sha
from growthengine.llm import Faults, LLMError, LLMOutputError, get_llm
from growthengine.logs import RunLog
from growthengine.models import AssetCopy, Doc, Moment
from growthengine.planner import allocate, next_monday, next_slot
from growthengine.render import RenderError
from growthengine.render.carousel import render_slides
from growthengine.render.text import render_text
from growthengine.render.video import render_short

STAGES = ["ingest", "moments", "plan", "write", "dedupe", "render", "check", "export"]
MEDIA_CHUNK_MS = 15 * 60 * 1000


class RunFailed(Exception):
    def __init__(self, run_id: str, stage: str, message: str):
        super().__init__(message)
        self.run_id, self.stage = run_id, stage


class Pipeline:
    def __init__(self, settings, quiet: bool = False):
        self.s = settings
        self.cfg = settings.config
        self.db = DB(settings.db_path)
        self.quiet = quiet

    # ------------------------------------------------------------------ entry
    def run(self, input_arg: str | None = None, *, count: int | None = None, transcript: str | None = None,
            source_id: str | None = None, rights: str | None = None, ai_media: bool = False,
            week: str | None = None, rewrite: bool = False, campaign: str | None = None,
            resume: str | None = None) -> dict:
        t0 = time.monotonic()
        if resume:
            row = self.db.one("SELECT * FROM runs WHERE run_id=?", (resume,))
            if row is None:
                raise SystemExit(f"no run {resume}")
            args = loads(row["args_json"], {})
            input_arg, count, transcript = row["input_path"], args.get("count"), args.get("transcript")
            source_id, rights, ai_media = args.get("source_id"), args.get("rights"), args.get("ai_media", False)
            week, campaign, rewrite = args.get("week"), args.get("campaign"), args.get("rewrite", False)
            run_id = resume
        else:
            run_id = time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:4]
        self.run_id = run_id
        self.log = RunLog(self.s.log_dir / f"run-{run_id}.jsonl", run_id, quiet=self.quiet)
        fired = bool(self.s.fault) and self.db.fault_fired(run_id, self.s.fault.strip())
        self.faults = Faults(self.s.fault, lambda spec: fired)     # decided here: workers never touch SQLite
        self.llm = get_llm(self.s, self.faults)
        self.count = count or int(self.cfg.mix.get("default_count", 20))
        self.campaign = campaign
        self.week_start = date.fromisoformat(week) if week else next_monday()
        if self.llm.name == "mock":
            self.log.warn("run", "using the mock writer (free, deterministic, lower quality). For real output set "
                                 "GE_LLM=claude and ANTHROPIC_API_KEY in .env")
        if resume:
            self.log.info("run", f"resuming run {run_id}")
            self.db.x("UPDATE runs SET status='running', error=NULL WHERE run_id=?", (run_id,))
        else:
            self.db.upsert("runs", {
                "run_id": run_id, "input_path": input_arg, "started_at": now(), "status": "running",
                "llm": self.llm.name, "model": self.llm.model, "count_target": self.count,
                "args_json": json.dumps({"count": count, "transcript": transcript, "source_id": source_id,
                                         "rights": rights, "ai_media": ai_media, "week": week,
                                         "campaign": campaign, "rewrite": rewrite})}, "run_id")
        stage = "ingest"
        try:
            stage = "ingest"
            doc = self._stage(stage, lambda: self._ingest(input_arg, transcript, source_id, rights, ai_media))
            self.doc = doc
            stage = "moments"
            moments, new_keys = self._stage(stage, lambda: self._moments(doc))
            stage = "plan"
            self._stage(stage, lambda: self._plan(doc, moments, new_keys, rewrite and not resume))
            for stage, fn in (("write", self._write), ("dedupe", self._dedupe), ("render", self._render),
                              ("check", self._check), ("export", self._export)):
                self._stage(stage, fn)
        except ingest.InputError:
            self._finish(t0, "failed", "input error")
            raise
        except Exception as e:  # noqa: BLE001 - model errors and anything unexpected: log, mark, resumable
            if self.faults.fired_now:
                self.db.mark_fault(run_id, self.faults.spec)
            msg = str(e) if isinstance(e, (LLMError, RunFailed)) else f"{type(e).__name__}: {e}"
            self.log.error(stage, f"run stopped: {msg}")
            self.db.stage(run_id, stage, "failed", msg)
            self._finish(t0, "failed", f"{stage}: {msg}")
            raise RunFailed(run_id, stage, msg) from e
        return self._finish(t0, "done", None)

    def _stage(self, name: str, fn):
        self.db.stage(self.run_id, name, "running")
        out = fn()
        self.db.add_run_usage(self.run_id, self.llm.usage.take())
        self.db.stage(self.run_id, name, "done")
        return out

    # ------------------------------------------------------------------ ingest
    def _ingest(self, input_arg, transcript, source_id, rights, ai_media) -> Doc:
        doc = ingest.load(input_arg, self.s, transcript=transcript, source_id=source_id, log=self.log)
        existing = self.db.one("SELECT rights_note, ai_media FROM inputs WHERE input_key=?", (doc.input_key,))
        note = rights or (existing["rights_note"] if existing else "") or ""
        self.db.upsert("inputs", {
            "input_key": doc.input_key, "source_id": doc.source_id, "kind": doc.kind, "title": doc.title,
            "path": doc.path, "words": doc.words, "duration_ms": doc.duration_ms, "rights_note": note,
            "ai_media": int(bool(ai_media or (existing and existing["ai_media"]))), "created_at": now()},
            "input_key")
        self.db.update("runs", "run_id", self.run_id, source_id=doc.source_id, input_key=doc.input_key,
                       input_words=doc.words, input_duration_ms=doc.duration_ms)
        kind = "video" if doc.media_path else doc.kind
        extra = f", {doc.duration_ms / 60000:.1f} min" if doc.duration_ms else ""
        self.log.info("ingest", f"{doc.source_id}: {kind}, {doc.words} words{extra}")
        return doc

    # ------------------------------------------------------------------ moments
    def _moments(self, doc: Doc) -> tuple[list[Moment], set[str]]:
        self.llm.prepare(doc)
        known = {r["key"] for r in self.db.q("SELECT key FROM moments")}
        llm_id = self.llm.model
        units = []
        if doc.kind == "text":
            for sec in doc.sections:
                units.append((sha(llm_id + "|" + normalize(sec.heading + " " + sec.text)), sec))
        else:
            chunk, start = [], doc.cues[0].start_ms
            for c in doc.cues:
                if chunk and c.end_ms - start > MEDIA_CHUNK_MS:
                    units.append((sha(llm_id + "|" + normalize(" ".join(x.text for x in chunk))), chunk))
                    chunk, start = [], c.start_ms
                chunk.append(c)
            if chunk:
                units.append((sha(llm_id + "|" + normalize(" ".join(x.text for x in chunk))), chunk))

        drafts: dict[str, list[dict]] = {}
        todo = []
        for h, unit in units:
            hit = self.db.one("SELECT moments_json FROM unit_cache WHERE unit_hash=? AND llm=?", (h, llm_id))
            if hit:
                drafts[h] = json.loads(hit["moments_json"])
            else:
                todo.append((h, unit))
        if todo:
            self.log.info("moments", f"extracting ideas from {len(todo)} new part(s); {len(units) - len(todo)} cached")
        fn = self.llm.extract_text_moments if doc.kind == "text" else self.llm.extract_media_moments
        with ThreadPoolExecutor(max_workers=max(1, self.s.concurrency)) as pool:
            futs = {pool.submit(fn, unit): (h, unit) for h, unit in todo}
            try:
                for f in as_completed(futs):
                    h, unit = futs[f]
                    try:
                        got = [d.model_dump() for d in f.result()]
                    except LLMOutputError as e:
                        self.log.warn("moments", f"skipped one part: {e}")
                        continue
                    drafts[h] = got
                    self.db.upsert("unit_cache", {"unit_hash": h, "llm": llm_id, "moments_json": json.dumps(got),
                                                  "created_at": now()}, ("unit_hash", "llm"))
            except LLMError:
                pool.shutdown(wait=False, cancel_futures=True)
                raise

        moments: dict[str, Moment] = {}
        for h, unit in units:
            for d in drafts.get(h, []):
                m = self._to_moment(doc, h, unit, d)
                if m and m.key not in moments:
                    moments[m.key] = m
        for m in moments.values():
            if m.key not in known:
                self.db.upsert("moments", {
                    "key": m.key, "source_id": m.source_id, "kind": m.kind, "quote": m.quote, "point": m.point,
                    "takeaway": m.takeaway, "details_json": json.dumps(m.details), "pillar": m.pillar,
                    "formats_json": json.dumps(m.formats), "score": m.score, "section_idx": m.section_idx,
                    "para_idx": m.para_idx, "start_ms": m.start_ms, "end_ms": m.end_ms, "unit_hash": m.unit_hash,
                    "first_run": self.run_id, "created_at": now()}, "key")
        new_keys = {k for k in moments if k not in known}
        self.log.info("moments", f"{len(moments)} ideas ({len(new_keys)} new)")
        return list(moments.values()), new_keys

    def _to_moment(self, doc: Doc, h: str, unit, d: dict) -> Moment | None:
        if doc.kind == "text":
            sec = unit
            q = normalize(d["quote"])
            if not q or q not in normalize(sec.text):
                self.log.warn("moments", f"dropped an idea whose quote is not in section {sec.idx + 1}",
                              quote=d["quote"][:80])
                return None
            para = next((i for i, p in enumerate(sec.paragraphs) if q in normalize(p)), 0)
            return Moment(key=moment_key(d["quote"]), source_id=doc.source_id, kind="text", quote=d["quote"],
                          point=d["point"], takeaway=d["takeaway"], details=d["details"], pillar=d["pillar"],
                          formats=d.get("formats") or ["text", "thread", "carousel", "graphic"],
                          score=int(d["score"]), section_idx=sec.idx, para_idx=para, unit_hash=h)
        by_idx = {c.idx: c for c in unit}
        a, b = d["start_cue"], d["end_cue"]
        if a not in by_idx or b not in by_idx or b < a:
            self.log.warn("moments", f"dropped a clip with cue range {a}-{b} outside the transcript part")
            return None
        span = [by_idx[i] for i in range(a, b + 1) if i in by_idx]
        fmt = self.cfg.formats["Short video"]
        while len(span) > 1 and span[-1].end_ms - span[0].start_ms > fmt["max_seconds"] * 1000 - 500:
            span.pop()
        dur = span[-1].end_ms - span[0].start_ms
        if dur < fmt.get("min_seconds", 12) * 1000:
            self.log.warn("moments", f"dropped a clip of {dur / 1000:.0f}s (too short)")
            return None
        quote = " ".join(c.text for c in span)
        return Moment(key=moment_key(quote), source_id=doc.source_id, kind="media", quote=quote, point=d["point"],
                      takeaway=d["takeaway"], details=d["details"], pillar=d["pillar"],
                      formats=["text", "thread", "carousel", "graphic", "video"], score=int(d["score"]),
                      start_ms=span[0].start_ms, end_ms=span[-1].end_ms, unit_hash=h)

    # ------------------------------------------------------------------ plan
    def _plan(self, doc: Doc, moments: list[Moment], new_keys: set[str], rewrite: bool) -> None:
        sid = doc.source_id
        rows = self.db.q("SELECT * FROM assets WHERE source_id=?", (sid,))
        if rewrite:
            n = 0
            for a in rows:
                if a["status"] != "rejected":
                    self.db.update("assets", "asset_id", a["asset_id"], status="planned", version=a["version"] + 1,
                                   updated_run=self.run_id, updated_at=now())
                    n += 1
            self.log.info("plan", f"--rewrite: {n} existing assets will be rewritten in place")
        keys = [m.key for m in moments]
        uses: dict[str, list[tuple[str, str]]] = {}
        if keys:
            marks = ",".join("?" for _ in keys)
            for a in self.db.q(f"SELECT moment_key, platform, format FROM assets WHERE moment_key IN ({marks})", keys):
                uses.setdefault(a["moment_key"], []).append((a["platform"], a["format"]))
        counts: dict[tuple[str, str], int] = {}
        for a in rows:
            if a["status"] != "rejected":
                counts[(a["platform"], a["format"])] = counts.get((a["platform"], a["format"]), 0) + 1
        existing = len(rows)
        n_new = self.count if existing == 0 else max(len(new_keys), self.count - existing)
        planned = allocate(self.cfg, moments, has_video=bool(doc.media_path), uses=uses, entry_counts=counts,
                           n_new=n_new, priority=new_keys)
        taken_ids = {r["asset_id"] for r in self.db.q("SELECT asset_id FROM assets")}
        for p in planned:
            pc = self.cfg.platforms[p.platform]["code"]
            fc = self.cfg.formats[p.fmt]["code"]
            aid = make_asset_id(pc, fc, p.moment.key, taken_ids)
            taken_ids.add(aid)
            taken = {r["scheduled_at"] for r in self.db.q(
                "SELECT scheduled_at FROM assets WHERE platform=? AND status!='rejected'", (p.platform,))}
            slot, _ = next_slot(self.cfg, p.platform, self.week_start, taken)
            self.db.upsert("assets", {
                "asset_id": aid, "moment_key": p.moment.key, "source_id": sid, "input_key": doc.input_key,
                "platform": p.platform, "format": p.fmt, "pillar": p.moment.pillar, "status": "planned",
                "scheduled_at": slot, "version": 1, "created_run": self.run_id, "updated_run": self.run_id,
                "llm": self.llm.model, "created_at": now(), "updated_at": now()}, "asset_id")
        if existing and not planned and not rewrite:
            self.log.info("plan", f"nothing new: {existing} assets already exist for {sid}")
        else:
            self.log.info("plan", f"{len(planned)} new assets planned (target {n_new}, existing {existing})")

    # ------------------------------------------------------------------ write
    def _moment(self, key: str) -> Moment:
        r = self.db.one("SELECT * FROM moments WHERE key=?", (key,))
        return Moment(key=r["key"], source_id=r["source_id"], kind=r["kind"], quote=r["quote"], point=r["point"],
                      takeaway=r["takeaway"], details=loads(r["details_json"], []), pillar=r["pillar"],
                      formats=loads(r["formats_json"], []), score=r["score"], section_idx=r["section_idx"],
                      para_idx=r["para_idx"], start_ms=r["start_ms"], end_ms=r["end_ms"], unit_hash=r["unit_hash"])

    def _reject(self, aid: str, reason: str) -> None:
        self.db.update("assets", "asset_id", aid, status="rejected", reason=reason, updated_run=self.run_id,
                       updated_at=now())
        src = self.s.content_dir / "assets" / aid
        if src.exists():
            dst = self.s.out_dir / "rejected" / aid
            if dst.exists():
                shutil.rmtree(dst)
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dst))
        self.log.warn("check", f"{aid} rejected: {reason}")

    def _write(self) -> None:
        rows = self.db.q("SELECT * FROM assets WHERE source_id=? AND status='planned' ORDER BY rowid",
                         (self.doc.source_id,))
        if not rows:
            return
        self.log.info("write", f"writing {len(rows)} assets with {self.llm.name} ({self.llm.model})")
        # SQLite stays on the main thread: load everything the workers need first.
        moments = {a["moment_key"]: self._moment(a["moment_key"]) for a in rows}

        def job(a):
            return self.llm.write(moments[a["moment_key"]], a["platform"], a["format"],
                                  self.cfg.platforms[a["platform"]], self.cfg.formats[a["format"]])

        with ThreadPoolExecutor(max_workers=max(1, self.s.concurrency)) as pool:
            futs = {pool.submit(job, a): a for a in rows}
            try:
                for f in as_completed(futs):
                    a = futs[f]
                    try:
                        copy: AssetCopy = f.result()
                    except LLMOutputError as e:
                        self._reject(a["asset_id"], f"writer: {e}")
                        continue
                    link = utm_link(self.cfg, a["platform"], a["asset_id"], self.campaign)
                    c = compose(self.cfg, copy, a["platform"], a["format"], link)
                    self.db.update("assets", "asset_id", a["asset_id"], status="written",
                                   copy_json=json.dumps(copy.model_dump(), ensure_ascii=False),
                                   caption=c["caption"], cta=c["cta"], utm_link=link,
                                   content_hash=sha(normalize(c["caption"])), updated_run=self.run_id,
                                   updated_at=now())
            except LLMError:
                pool.shutdown(wait=False, cancel_futures=True)
                raise

    # ------------------------------------------------------------------ dedupe
    def _dedupe(self) -> None:
        library = []
        for r in self.db.q("SELECT asset_id, platform, copy_json, content_hash FROM assets "
                           "WHERE status IN ('written','rendered','ready') ORDER BY created_at, rowid"):
            copy = loads(r["copy_json"], {})
            library.append({"asset_id": r["asset_id"], "platform": r["platform"], "hook": copy.get("hook", ""),
                            "body": copy.get("body", ""), "hash": r["content_hash"]})
        seen: list[dict] = []
        new_ids = {r["asset_id"] for r in self.db.q(
            "SELECT asset_id FROM assets WHERE source_id=? AND status='written'", (self.doc.source_id,))}
        for row in library:
            if row["asset_id"] in new_ids:
                exact = next((x for x in seen if x["hash"] == row["hash"]), None)
                hit = (exact["asset_id"], 100.0) if exact else near_duplicate(row["platform"], row["hook"],
                                                                               row["body"], seen)
                if hit:
                    self._reject(row["asset_id"], f"duplicate of {hit[0]} (similarity {hit[1]:.0f})")
                    continue
            seen.append(row)

    # ------------------------------------------------------------------ render
    def _render(self) -> None:
        rows = self.db.q("SELECT * FROM assets WHERE source_id=? AND status='written' ORDER BY rowid",
                         (self.doc.source_id,))
        if not rows:
            return
        fonts = resolve_fonts(self.s)
        design = self.cfg.brand.get("design", {})
        for a in rows:
            aid, fmt = a["asset_id"], a["format"]
            fcfg = self.cfg.formats[fmt]
            fam = fcfg["family"]
            out = self.s.content_dir / "assets" / aid
            if out.exists():
                shutil.rmtree(out)
            copy = AssetCopy.model_validate(loads(a["copy_json"], {}))
            composed = {"caption": a["caption"], "posts": [], "hashtags": []}
            if fam == "thread":
                composed["posts"] = a["caption"].split("\n\n---\n\n")
            used_fonts: list[str] = []
            try:
                if fam in ("text", "thread"):
                    files = render_text(out, composed, copy)
                elif fam in ("carousel", "graphic"):
                    slides = [s.model_dump() for s in copy.slides]
                    if fam == "carousel":
                        slides.append({"title": "Your next step", "body": a["cta"]})
                    else:
                        slides = slides[:1]
                    files = render_slides(out, slides, fonts, design, fcfg, as_pdf=a["platform"] == "LinkedIn")
                    used_fonts = [fonts.heading_id, fonts.body_id]
                else:
                    m = self._moment(a["moment_key"])
                    if not self.doc.media_path:
                        raise RenderError("no video file for this input")
                    files = render_short(out, Path(self.doc.media_path), self.doc.cues, m.start_ms, m.end_ms,
                                         copy.on_screen_hook, fonts, fcfg, self.s.ffmpeg, self.s.ffprobe,
                                         self.doc.path)
                    used_fonts = [fonts.video_id]
            except RenderError as e:
                self._reject(aid, f"render: {e}")
                continue
            self.db.update("assets", "asset_id", aid, status="rendered", files_json=json.dumps(files),
                           fonts_json=json.dumps(used_fonts), updated_run=self.run_id, updated_at=now())
            self.log.debug("render", f"{aid} rendered", files=files)
        self.log.info("render", f"rendered {len(rows)} assets")

    # ------------------------------------------------------------------ check
    def _check(self) -> None:
        rows = self.db.q("SELECT a.*, i.path AS input_path, i.rights_note FROM assets a "
                         "JOIN inputs i ON i.input_key = a.input_key "
                         "WHERE a.source_id=? AND a.status='rendered' ORDER BY a.rowid", (self.doc.source_id,))
        ready = 0
        for a in rows:
            aid = a["asset_id"]
            m = self.db.one("SELECT * FROM moments WHERE key=?", (a["moment_key"],))
            copy = loads(a["copy_json"], {})
            posts = a["caption"].split("\n\n---\n\n") if self.cfg.formats[a["format"]]["family"] == "thread" else []
            composed = {"caption": a["caption"], "posts": posts,
                        "hashtags": [w for w in a["caption"].split() if w.startswith("#")]}
            res = checks.run_all(
                self.s, platform=a["platform"], fmt=a["format"], copy=copy, composed=composed,
                moment={"quote": m["quote"], "point": m["point"], "details": loads(m["details_json"], [])},
                out=self.s.content_dir / "assets" / aid, files=loads(a["files_json"], {}),
                fonts_used=loads(a["fonts_json"], []), input_path=a["input_path"], input_note=a["rights_note"] or "",
                link=a["utm_link"], asset_id=aid, target=self.cfg.mix.get("targets", {}).get(a["format"], ""),
                scheduled_at=a["scheduled_at"])
            self.db.update("assets", "asset_id", aid, checks_json=json.dumps(res, ensure_ascii=False))
            bad = checks.failures(res)
            if bad:
                self._reject(aid, "; ".join(bad))
            else:
                self.db.update("assets", "asset_id", aid, status="ready", reason=None, updated_run=self.run_id,
                               updated_at=now())
                ready += 1
        if rows:
            self.log.info("check", f"{ready} of {len(rows)} passed every check")

    # ------------------------------------------------------------------ export
    def _export(self) -> None:
        res = export.export_all(self.s, self.db)
        self.log.info("export", f"calendar.csv: {res['rows']} ready assets in the library")

    # ------------------------------------------------------------------ summary
    def _finish(self, t0: float, status: str, error: str | None) -> dict:
        run_id = self.run_id
        elapsed = time.monotonic() - t0
        r = self.db.one("SELECT * FROM runs WHERE run_id=?", (run_id,))
        sid = r["source_id"]
        q = lambda sql, *a: self.db.one(sql, a)[0]  # noqa: E731
        produced = q("SELECT COUNT(*) FROM assets WHERE updated_run=? AND status='ready' AND version=1 "
                     "AND created_run=?", run_id, run_id)
        updated = q("SELECT COUNT(*) FROM assets WHERE updated_run=? AND status='ready' AND version>1", run_id)
        rejected = q("SELECT COUNT(*) FROM assets WHERE updated_run=? AND status='rejected'", run_id)
        dupes = q("SELECT COUNT(*) FROM assets WHERE updated_run=? AND status='rejected' AND reason LIKE 'duplicate%'",
                  run_id)
        unchanged = q("SELECT COUNT(*) FROM assets WHERE source_id=? AND status='ready' AND updated_run!=?",
                      sid, run_id) if sid else 0
        self.db.update("runs", "run_id", run_id, ended_at=now(), status=status, error=error,
                       produced=produced, updated=updated, rejected=rejected - dupes, duplicates=dupes,
                       unchanged=unchanged, runtime_s=(r["runtime_s"] or 0) + elapsed)
        r = self.db.one("SELECT * FROM runs WHERE run_id=?", (run_id,))
        summary = {k: r[k] for k in r.keys() if k != "args_json"}
        summary["library_ready"] = q("SELECT COUNT(*) FROM assets WHERE status='ready'")
        summary["errors_logged"] = self.log.errors
        summary["log"] = self.s.rel(self.log.path)
        out = self.s.out_dir / run_id
        out.mkdir(parents=True, exist_ok=True)
        (out / "run_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
        summary["summary_path"] = self.s.rel(out / "run_summary.json")
        return summary
