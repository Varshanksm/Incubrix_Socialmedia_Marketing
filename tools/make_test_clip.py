"""Generate a rights-clean synthetic "talk" for tests and demos: an ffmpeg test pattern with a tone, plus a
WebVTT transcript timed at ~2.6 words per second. Usage: python tools/make_test_clip.py [out_dir]"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

LINES = [
    "Most creators cut clips from the start of the episode, and that is why their clips feel flat.",
    "The best moments usually sit in the middle, where the guest relaxes and tells a real story.",
    "So before you open the editor, read the transcript and mark three kinds of moments.",
    "Mark where someone changes their mind, where they tell a story, and where they give a clear instruction.",
    "Those moments make sense to a stranger, and that is the test every clip has to pass.",
    "Now give each clip one job. It can teach one step, show one result, or prove one point.",
    "When a clip tries to do two jobs, people leave before the second one starts.",
    "Write that job down as one sentence. That sentence becomes the hook on screen.",
    "Put the hook in the first two seconds, as text, because many people watch with the sound off.",
    "Then check every caption against the audio, because names and numbers are where captions go wrong.",
    "Keep captions short, high contrast, and away from the buttons at the bottom of the screen.",
    "Plan the whole week before you post, so your strongest moments are spread across different days.",
    "Give every post a tracking link and one goal you can measure at the end of the week.",
    "At the end of the week, keep a short list of the hooks that held attention.",
    "Use that list the next time you pick moments, and each episode gets easier to repurpose.",
    "One episode, cut with care, can carry a full week of useful posts on every platform you use.",
]


def ts(ms: int) -> str:
    h, r = divmod(ms, 3_600_000)
    m, r = divmod(r, 60_000)
    s, r = divmod(r, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}.{r:03d}"


def make(out_dir: Path, ffmpeg: str = "ffmpeg") -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    vtt, t = ["WEBVTT", ""], 500
    for line in LINES:
        dur = int(len(line.split()) / 2.6 * 1000)
        vtt += [f"{ts(t)} --> {ts(t + dur)}", line, ""]
        t += dur + 300
    total_s = t / 1000 + 1
    video, sub = out_dir / "test_talk.mp4", out_dir / "test_talk.vtt"
    sub.write_text("\n".join(vtt), encoding="utf-8")
    subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
                    "-f", "lavfi", "-i", f"testsrc2=size=1280x720:rate=30:duration={total_s:.2f}",
                    "-f", "lavfi", "-i", f"sine=frequency=220:sample_rate=48000:duration={total_s:.2f}",
                    "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
                    str(video)], check=True)
    return video, sub


if __name__ == "__main__":
    v, s = make(Path(sys.argv[1] if len(sys.argv) > 1 else "samples"))
    print(f"wrote {v} and {s}")
