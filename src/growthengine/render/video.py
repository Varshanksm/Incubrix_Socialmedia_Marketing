"""9:16 short from a stretch of a long video: cut at cue boundaries, blurred-pad reframe, burned captions
(ASS), on-screen hook for the first 2 seconds, loudness normalised. Sources are kept for re-editing."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

from growthengine.ingest import probe
from growthengine.models import Cue
from growthengine.render import RenderError


def _ass_time(ms: int) -> str:
    ms = max(0, ms)
    h, r = divmod(ms, 3_600_000)
    m, r = divmod(r, 60_000)
    s, r = divmod(r, 1000)
    return f"{h}:{m:02d}:{s:02d}.{r // 10:02d}"


def _esc(t: str) -> str:
    return t.replace("\\", "").replace("{", "(").replace("}", ")").replace("\n", " ")


def caption_chunks(cues: list[Cue], start_ms: int, end_ms: int, max_words: int = 4) -> list[tuple[int, int, str]]:
    """Split each cue into short chunks timed by their share of the cue's characters."""
    out = []
    for c in cues:
        if c.end_ms <= start_ms or c.start_ms >= end_ms:
            continue
        ws = c.text.split()
        chunks = [" ".join(ws[i:i + max_words]) for i in range(0, len(ws), max_words)]
        total = sum(len(x) for x in chunks) or 1
        t = c.start_ms
        for ch in chunks:
            dur = (c.end_ms - c.start_ms) * len(ch) / total
            a, b = int(t), int(t + dur)
            t += dur
            a, b = max(a, start_ms), min(b, end_ms)
            if b > a:
                out.append((a - start_ms, b - start_ms, ch))
    return out


def write_ass(path: Path, chunks, hook: str, family: str, w: int, h: int) -> None:
    caption_margin = int(h * 0.25)     # keep captions above the bottom ~20% UI zone
    hook_margin = int(h * 0.16)        # and the hook below the top ~14%
    lines = [
        "[Script Info]", "ScriptType: v4.00+", f"PlayResX: {w}", f"PlayResY: {h}", "WrapStyle: 0",
        "ScaledBorderAndShadow: yes", "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, "
        "Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: Caption,{family},76,&H00FFFFFF,&H00FFFFFF,&H00000000,&H64000000,-1,0,0,0,100,100,0,0,1,6,2,"
        f"2,80,80,{caption_margin},1",
        f"Style: Hook,{family},84,&H00000000,&H00000000,&H0047B5FF,&H0047B5FF,-1,0,0,0,100,100,0,0,3,18,0,"
        f"8,90,90,{hook_margin},1",
        "", "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    if hook:
        lines.append(f"Dialogue: 1,{_ass_time(0)},{_ass_time(2000)},Hook,,0,0,0,,{_esc(hook)}")
    for a, b, text in chunks:
        lines.append(f"Dialogue: 0,{_ass_time(a)},{_ass_time(b)},Caption,,0,0,0,,{_esc(text)}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def render_short(out: Path, media: Path, cues: list[Cue], start_ms: int, end_ms: int, hook: str, fonts,
                 fmt_cfg: dict, ffmpeg: str = "ffmpeg", ffprobe: str = "ffprobe", source_rel: str = "") -> dict:
    w, h = fmt_cfg["width"], fmt_cfg["height"]
    dur_ms = end_ms - start_ms
    if dur_ms > fmt_cfg["max_seconds"] * 1000:
        raise RenderError(f"clip is {dur_ms / 1000:.1f}s, over the {fmt_cfg['max_seconds']}s limit")
    out.mkdir(parents=True, exist_ok=True)
    src = out / "source"
    src.mkdir(exist_ok=True)

    info = probe(media, ffprobe)
    vstreams = [s for s in info["streams"] if s.get("codec_type") == "video"]
    has_audio = any(s.get("codec_type") == "audio" for s in info["streams"])
    if not vstreams:
        raise RenderError("input has no video stream (audio-only inputs are used for text formats only)")

    chunks = caption_chunks(cues, start_ms, end_ms)
    write_ass(src / "captions.ass", chunks, hook, fonts.video_family, w, h)
    (src / "cut.json").write_text(json.dumps({
        "input": source_rel, "start_ms": start_ms, "end_ms": end_ms, "reframe": "blur-pad 9:16",
        "hook": hook, "caption_font": fonts.video_family,
        "cues": [{"start_ms": c.start_ms, "end_ms": c.end_ms, "text": c.text} for c in cues
                 if c.end_ms > start_ms and c.start_ms < end_ms],
    }, indent=2, ensure_ascii=False), encoding="utf-8")

    # libass gets relative paths (no drive-letter colons to escape on Windows); cwd = the source folder.
    try:
        fontsdir = os.path.relpath(fonts.video_dir, src).replace("\\", "/")
    except ValueError:   # different drive: copy the fonts next to the captions
        fontsdir = "fonts"
        shutil.copytree(fonts.video_dir, src / "fonts", dirs_exist_ok=True)
    vf = (f"[0:v]split[a][b];"
          f"[a]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},boxblur=30:3[bg];"
          f"[b]scale={w}:{h}:force_original_aspect_ratio=decrease[fg];"
          f"[bg][fg]overlay=(W-w)/2:(H-h)/2,subtitles=captions.ass:fontsdir={fontsdir},fps=30,format=yuv420p[v]")
    final = out / "final.mp4"
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
           "-ss", f"{start_ms / 1000:.3f}", "-t", f"{dur_ms / 1000:.3f}", "-i", str(media.resolve())]
    if not has_audio:
        cmd += ["-f", "lavfi", "-t", f"{dur_ms / 1000:.3f}", "-i", "anullsrc=r=48000:cl=stereo"]
    cmd += ["-filter_complex", vf, "-map", "[v]", "-map", "0:a:0" if has_audio else "1:a:0",
            "-af", "loudnorm=I=-14:TP=-1.5:LRA=11", "-ar", "48000",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "21", "-c:a", "aac", "-b:a", "160k",
            "-movflags", "+faststart", "-shortest", str(final.resolve())]
    (src / "render.cmd").write_text(
        "# Run from this folder (source/). Re-run after editing captions.ass or cut.json values.\n"
        + subprocess.list2cmdline(cmd) + "\n", encoding="utf-8")
    try:
        r = subprocess.run(cmd, cwd=src, capture_output=True, text=True, timeout=600)
    except FileNotFoundError:
        raise RenderError("ffmpeg not found: install ffmpeg or set FFMPEG in .env")
    if r.returncode != 0 or not final.is_file():
        raise RenderError(f"ffmpeg failed: {r.stderr.strip()[-300:]}")
    return {"final": "final.mp4", "source": "source/", "captions": "source/captions.ass", "cut": "source/cut.json"}
