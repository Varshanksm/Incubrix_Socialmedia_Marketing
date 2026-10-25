"""Carousels and single graphics with Pillow: 1080x1350 PNG slides (+ a PDF for LinkedIn documents).
The editable source is source/slides.json; edit it and run `growthengine rerender <asset_id>`."""
from __future__ import annotations

import json
from pathlib import Path

from PIL import Image, ImageDraw

from growthengine.render import RenderError

MARGIN = 96


def _wrap(draw, text: str, font, width: int) -> list[str]:
    lines: list[str] = []
    for para in text.split("\n"):
        line = ""
        for word in para.split():
            trial = f"{line} {word}".strip()
            if draw.textlength(trial, font=font) <= width:
                line = trial
            else:
                if line:
                    lines.append(line)
                line = word
        lines.append(line)
    return lines


def _fit_block(draw, text: str, make_font, start: int, min_size: int, width: int, max_h: int, spacing=1.25):
    size = start
    while True:
        font = make_font(size)
        lines = _wrap(draw, text, font, width)
        h = int(len(lines) * size * spacing)
        if h <= max_h or size <= min_size:
            return font, lines, size, h
        size -= 4


def _draw_lines(draw, xy, lines, font, size, fill, spacing=1.25, bold=False):
    x, y = xy
    for ln in lines:
        draw.text((x, y), ln, font=font, fill=fill, stroke_width=2 if bold else 0, stroke_fill=fill)
        y += int(size * spacing)
    return y


def draw_slide(slide: dict, n: int, total: int, fonts, design: dict, w: int, h: int, cover: bool) -> Image.Image:
    img = Image.new("RGB", (w, h), design.get("background", "#0F1720"))
    d = ImageDraw.Draw(img)
    text_c, muted, accent = design.get("text", "#F5F7FA"), design.get("muted", "#A9B4C0"), design.get("accent", "#FFB547")
    inner = w - 2 * MARGIN
    # counter + accent bar
    small = fonts.body(34)
    if total > 1:
        label = f"{n}/{total}"
        d.text((w - MARGIN - d.textlength(label, font=small), MARGIN - 20), label, font=small, fill=muted)
    d.rectangle([MARGIN, MARGIN + 40, MARGIN + 120, MARGIN + 52], fill=accent)

    top = MARGIN + 110
    avail = h - top - MARGIN - 120
    title_start, body_start = (100, 50) if cover else (84, 60)
    tfont, tlines, tsize, th = _fit_block(d, slide["title"], fonts.heading, title_start, 44, inner,
                                          int(avail * (0.75 if cover else 0.45)), 1.15)
    y = _draw_lines(d, (MARGIN, top), tlines, tfont, tsize, text_c, 1.15, bold=fonts.faux_bold)
    if slide.get("body"):
        y += 48
        bfont, blines, bsize, _ = _fit_block(d, slide["body"], fonts.body, body_start, 30, inner,
                                             h - y - MARGIN - 120)
        _draw_lines(d, (MARGIN, y), blines, bfont, bsize, muted if cover else text_c)
    if cover and total > 1:
        swipe = "Swipe  >"
        d.text((w - MARGIN - d.textlength(swipe, font=small), h - MARGIN - 40), swipe, font=small, fill=accent)
    return img


def render_slides(out: Path, slides: list[dict], fonts, design: dict, fmt_cfg: dict, as_pdf: bool) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    (out / "source").mkdir(exist_ok=True)
    w, h = fmt_cfg["width"], fmt_cfg["height"]
    (out / "source" / "slides.json").write_text(
        json.dumps({"size": [w, h], "design": design, "slides": slides}, indent=2, ensure_ascii=False),
        encoding="utf-8")
    try:
        images = [draw_slide(s, i + 1, len(slides), fonts, design, w, h, cover=(i == 0))
                  for i, s in enumerate(slides)]
    except Exception as e:  # noqa: BLE001 - one bad slide rejects only this asset
        raise RenderError(f"slide render failed: {e}") from e

    files = {"source": "source/slides.json", "slides": []}
    if len(images) == 1:
        images[0].save(out / "final.png", optimize=True)
        files["final"] = "final.png"
        files["slides"] = ["final.png"]
        return files
    fdir = out / "final"
    fdir.mkdir(exist_ok=True)
    for i, img in enumerate(images, 1):
        name = f"final/slide_{i:02d}.png"
        img.save(out / name, optimize=True)
        files["slides"].append(name)
    if as_pdf:
        images[0].save(out / "final.pdf", save_all=True, append_images=images[1:], resolution=144)
        files["final"] = "final.pdf"
    else:
        files["final"] = "final/"
    return files


def rerender(out: Path, fonts, as_pdf: bool) -> dict:
    spec = json.loads((out / "source" / "slides.json").read_text(encoding="utf-8"))
    w, h = spec["size"]
    return render_slides(out, spec["slides"], fonts, spec["design"], {"width": w, "height": h}, as_pdf)
