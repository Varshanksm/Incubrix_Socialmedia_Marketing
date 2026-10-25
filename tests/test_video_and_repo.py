import importlib.util
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from conftest import ROOT, run

HAS_FFMPEG = shutil.which("ffmpeg") and shutil.which("ffprobe")


def _make_clip(out: Path):
    spec = importlib.util.spec_from_file_location("make_test_clip", ROOT / "tools" / "make_test_clip.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.make(out)


@pytest.mark.video
@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg/ffprobe not on PATH")
def test_video_input_renders_vertical_shorts(settings, project):
    video, vtt = _make_clip(project / "samples")
    r = run(settings, "samples/test_talk.mp4", count=8)
    assert r["status"] == "done" and r["produced"] >= 4
    shorts = sorted((settings.content_dir / "assets").glob("*-SV-*"))
    assert shorts, "no short videos rendered"
    for d in shorts:
        info = json.loads(subprocess.run(
            ["ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", str(d / "final.mp4")],
            capture_output=True, text=True).stdout)
        v = next(s for s in info["streams"] if s["codec_type"] == "video")
        assert (v["width"], v["height"]) == (1080, 1920)
        assert float(info["format"]["duration"]) <= 60.5
        assert any(s["codec_type"] == "audio" for s in info["streams"])
        for f in ("captions.ass", "cut.json", "render.cmd"):
            assert (d / "source" / f).is_file()


def test_no_secrets_and_env_is_ignored():
    assert ".env" in (ROOT / ".gitignore").read_text(encoding="utf-8").split()
    key_like = re.compile("sk-" + "ant-[A-Za-z0-9_-]{20,}")
    skip = {".venv", "data", "out", "logs", "content", "brief", ".git", "__pycache__", ".pytest_cache"}
    for p in ROOT.rglob("*"):
        if p.is_dir() or set(p.relative_to(ROOT).parts) & skip or p.name == ".env":
            continue
        if p.suffix.lower() in {".py", ".md", ".yaml", ".yml", ".toml", ".txt", ".json", ".csv", ".example", ""}:
            assert not key_like.search(p.read_text(encoding="utf-8", errors="ignore")), f"key-like string in {p}"
