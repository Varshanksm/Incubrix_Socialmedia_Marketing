"""Load one long input into a Doc: markdown/text, an article URL or .html, a .vtt/.srt transcript,
or an audio/video file (with a transcript file, a same-name sidecar, or faster-whisper if installed)."""
from __future__ import annotations

import json
import re
import subprocess
import urllib.request
from html.parser import HTMLParser
from pathlib import Path

from growthengine.ids import file_sha, normalize, sha
from growthengine.models import Cue, Doc, Section

TEXT_EXT = {".md", ".markdown", ".txt"}
HTML_EXT = {".html", ".htm"}
SUB_EXT = {".vtt", ".srt"}
MEDIA_EXT = {".mp4", ".mov", ".mkv", ".webm", ".m4a", ".mp3", ".wav", ".aac", ".flac", ".ogg"}


class InputError(Exception):
    """The input cannot be used; the message says why and what to do instead."""


# ---------------- source ids ----------------

_version_tail = re.compile(r"([_\-\s](v\d+|final|draft|edit(ed)?|copy)|\s\(\d+\))+$", re.I)


def source_id_for(path_or_url: str) -> str:
    """A stable name for an input that survives edits: 'I2_blog_v2.md' and 'I2_blog.md' -> 'i2_blog'."""
    if path_or_url.startswith(("http://", "https://")):
        tail = re.sub(r"[?#].*$", "", path_or_url.rstrip("/")).split("/")[-1] or "page"
        stem = Path(tail).stem
    else:
        stem = Path(path_or_url).stem
    stem = _version_tail.sub("", stem)
    return re.sub(r"[^a-z0-9_\-]+", "_", stem.lower()).strip("_") or "input"


# ---------------- markdown / text ----------------

_md_link = re.compile(r"!?\[([^\]]*)\]\([^)]*\)")
_md_emph = re.compile(r"(\*\*|__|\*|_|`)")
_heading = re.compile(r"^(#{1,6})\s+(.*)$")
_list_item = re.compile(r"^\s*([-*+]|\d+[.)])\s+")


def _clean_md(line: str) -> str:
    line = _md_link.sub(r"\1", line)
    return _md_emph.sub("", line).strip()


_html_comment = re.compile(r"<!--.*?-->", re.S)


def parse_markdown(text: str) -> tuple[str, list[Section]]:
    text = _html_comment.sub("", text)
    title = ""
    sections: list[Section] = []
    heading, paras, buf = "", [], []
    in_code = False

    def flush_para():
        if buf:
            p = " ".join(x for x in buf if x).strip()
            if p:
                paras.append(p)
            buf.clear()

    def flush_section():
        flush_para()
        if paras:
            sections.append(Section(idx=len(sections), heading=heading, paragraphs=list(paras)))
        paras.clear()

    for raw in text.splitlines():
        if raw.strip().startswith("```"):
            in_code = not in_code
            continue
        if in_code:
            continue
        m = _heading.match(raw.strip())
        if m:
            level, htext = len(m.group(1)), _clean_md(m.group(2))
            if level == 1 and not title:
                title = htext
                flush_section()
                continue
            flush_section()
            heading = htext
            continue
        if not raw.strip():
            flush_para()
            continue
        line = re.sub(r"^\s*>\s?", "", raw)      # blockquotes keep their text
        if _list_item.match(line):
            line = _list_item.sub("", line)
            line = _clean_md(line)
            if line and line[-1] not in ".!?:":
                line += "."
            buf.append(line)
            continue
        buf.append(_clean_md(line))
    flush_section()
    return title, _group_untitled(sections)


def _group_untitled(sections: list[Section]) -> list[Section]:
    """A document without headings becomes sections of up to 3 paragraphs."""
    if len(sections) == 1 and not sections[0].heading and len(sections[0].paragraphs) > 3:
        ps = sections[0].paragraphs
        sections = [Section(idx=i // 3, heading="", paragraphs=ps[i:i + 3]) for i in range(0, len(ps), 3)]
    for i, s in enumerate(sections):
        s.idx = i
    return sections


# ---------------- html ----------------

class _HTMLText(HTMLParser):
    BLOCK = {"p", "li", "h1", "h2", "h3", "h4", "blockquote"}
    SKIP = {"script", "style", "nav", "footer", "header", "aside", "form", "noscript", "svg"}

    def __init__(self):
        super().__init__()
        self.lines: list[str] = []
        self._tag: str | None = None
        self._buf: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self._skip += 1
        elif tag in self.BLOCK and not self._skip:
            self._flush()
            self._tag = tag

    def handle_endtag(self, tag):
        if tag in self.SKIP and self._skip:
            self._skip -= 1
        elif tag in self.BLOCK and not self._skip:
            self._flush()

    def handle_data(self, data):
        if self._tag and not self._skip:
            self._buf.append(data)

    def _flush(self):
        text = re.sub(r"\s+", " ", "".join(self._buf)).strip()
        if text and self._tag:
            prefix = {"h1": "# ", "h2": "## ", "h3": "### ", "h4": "### ", "li": "- "}.get(self._tag, "")
            self.lines.append(prefix + text)
        self._buf, self._tag = [], None


def html_to_markdown(html: str) -> str:
    m = re.search(r"<(article|main)\b.*?</\1>", html, re.S | re.I)
    p = _HTMLText()
    p.feed(m.group(0) if m else html)
    p._flush()
    return "\n\n".join(p.lines)


def fetch_url(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "growthengine/0.1 (+content repurposing)"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            charset = r.headers.get_content_charset() or "utf-8"
            return r.read().decode(charset, errors="replace")
    except Exception as e:  # noqa: BLE001 - surfaced to the user as an input error
        raise InputError(f"could not fetch {url}: {e}. Save the page as .html or .md and pass the file instead.")


# ---------------- transcripts ----------------

_ts = re.compile(r"(?:(\d+):)?(\d{1,2}):(\d{2})[.,](\d{1,3})")


def _ms(ts: str) -> int:
    m = _ts.search(ts)
    if not m:
        raise InputError(f"bad timestamp {ts!r} in transcript")
    h, mnt, s, frac = m.groups()
    return ((int(h or 0) * 60 + int(mnt)) * 60 + int(s)) * 1000 + int(frac.ljust(3, "0")[:3])


def parse_subtitles(text: str) -> list[Cue]:
    cues: list[Cue] = []
    lines = text.replace("\r\n", "\n").split("\n")
    i = 0
    while i < len(lines):
        line = lines[i]
        if "-->" in line:
            a, b = line.split("-->", 1)
            start, end = _ms(a), _ms(b.strip().split(" ")[0])
            i += 1
            body = []
            while i < len(lines) and lines[i].strip():
                body.append(lines[i].strip())
                i += 1
            t = re.sub(r"<[^>]+>", "", " ".join(body)).strip()
            if t:
                cues.append(Cue(idx=len(cues), start_ms=start, end_ms=end, text=t))
        i += 1
    if not cues:
        raise InputError("transcript has no cues (expected WebVTT or SRT)")
    return cues


def cues_to_vtt(cues: list[Cue]) -> str:
    def t(ms: int) -> str:
        h, r = divmod(ms, 3_600_000)
        m, r = divmod(r, 60_000)
        s, ms_ = divmod(r, 1000)
        return f"{h:02d}:{m:02d}:{s:02d}.{ms_:03d}"
    out = ["WEBVTT", ""]
    for c in cues:
        out += [f"{t(c.start_ms)} --> {t(c.end_ms)}", c.text, ""]
    return "\n".join(out)


def probe(path: Path, ffprobe: str = "ffprobe") -> dict:
    try:
        r = subprocess.run([ffprobe, "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)],
                           capture_output=True, text=True, timeout=60)
    except FileNotFoundError:
        raise InputError("ffprobe not found: install ffmpeg or set FFPROBE in .env")
    if r.returncode != 0:
        raise InputError(f"ffprobe could not read {path.name}: {r.stderr.strip()[:200]}")
    return json.loads(r.stdout)


def transcribe(media: Path, cache_dir: Path, model_name: str, log=None) -> list[Cue]:
    """faster-whisper on CPU, cached by file hash so repeat runs never transcribe twice."""
    cache = cache_dir / f"{file_sha(media)[:16]}.{model_name}.vtt"
    if cache.is_file():
        return parse_subtitles(cache.read_text(encoding="utf-8"))
    try:
        from faster_whisper import WhisperModel  # type: ignore
    except ImportError:
        raise InputError(
            f"{media.name} has no transcript. Pass --transcript file.vtt, put {media.stem}.vtt next to it, "
            "or install speech-to-text: pip install -e .[stt]")
    if log:
        log.info("ingest", f"transcribing {media.name} with faster-whisper '{model_name}' (first run only)")
    model = WhisperModel(model_name, device="cpu", compute_type="int8")
    segments, _ = model.transcribe(str(media), vad_filter=True)
    cues = [Cue(idx=i, start_ms=int(s.start * 1000), end_ms=int(s.end * 1000), text=s.text.strip())
            for i, s in enumerate(segments) if s.text.strip()]
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(cues_to_vtt(cues), encoding="utf-8")
    return cues


# ---------------- entry point ----------------

def load(input_arg: str, settings, transcript: str | None = None, source_id: str | None = None, log=None) -> Doc:
    sid = source_id or source_id_for(input_arg)
    if input_arg.startswith(("http://", "https://")):
        md = html_to_markdown(fetch_url(input_arg))
        return _text_doc(md, sid, input_arg, fallback_title=sid)

    path = Path(input_arg)
    if not path.is_absolute():
        path = (Path.cwd() / path)
    if not path.is_file():
        raise InputError(f"input not found: {input_arg}")
    ext = path.suffix.lower()
    rel = settings.rel(path)

    if ext in TEXT_EXT:
        return _text_doc(path.read_text(encoding="utf-8", errors="replace"), sid, rel, fallback_title=path.stem)
    if ext in HTML_EXT:
        return _text_doc(html_to_markdown(path.read_text(encoding="utf-8", errors="replace")), sid, rel,
                         fallback_title=path.stem)
    if ext == ".pdf":
        try:
            from pypdf import PdfReader  # type: ignore
        except ImportError:
            raise InputError("PDF input needs pypdf (pip install pypdf), or export the PDF to .md/.txt")
        text = "\n\n".join((pg.extract_text() or "") for pg in PdfReader(str(path)).pages)
        return _text_doc(text, sid, rel, fallback_title=path.stem)
    if ext in SUB_EXT:
        cues = parse_subtitles(path.read_text(encoding="utf-8", errors="replace"))
        return Doc(source_id=sid, input_key=sha("cues:" + normalize(" ".join(c.text for c in cues))),
                   kind="media", title=path.stem, path=rel, cues=cues, media_path=None,
                   duration_ms=cues[-1].end_ms)
    if ext in MEDIA_EXT:
        info = probe(path, settings.ffprobe)
        duration_ms = int(float(info.get("format", {}).get("duration", 0)) * 1000)
        tpath = Path(transcript) if transcript else None
        if tpath is None:
            for cand in (path.with_suffix(".vtt"), path.with_suffix(".srt")):
                if cand.is_file():
                    tpath = cand
                    break
        if tpath is not None:
            if not tpath.is_file():
                raise InputError(f"transcript not found: {tpath}")
            cues = parse_subtitles(tpath.read_text(encoding="utf-8", errors="replace"))
        else:
            cues = transcribe(path, settings.data_dir / "transcripts", settings.whisper_model, log)
        key = sha(file_sha(path) + normalize(" ".join(c.text for c in cues)))
        return Doc(source_id=sid, input_key=key, kind="media", title=path.stem, path=rel, cues=cues,
                   media_path=str(path), duration_ms=duration_ms)
    raise InputError(f"unsupported input type '{ext}'. Use .md .txt .html .pdf .vtt .srt, an article URL, "
                     f"or audio/video ({', '.join(sorted(MEDIA_EXT))}).")


def _text_doc(md: str, sid: str, path: str, fallback_title: str) -> Doc:
    title, sections = parse_markdown(md)
    if not sections or sum(len(p.split()) for s in sections for p in s.paragraphs) < 80:
        raise InputError("input has too little text (need at least ~80 words of prose)")
    key = sha("text:" + normalize(" ".join(s.heading + " " + s.text for s in sections)))
    return Doc(source_id=sid, input_key=key, kind="text", title=title or fallback_title, path=path,
               sections=sections)
