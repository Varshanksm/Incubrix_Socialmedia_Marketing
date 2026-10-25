"""Font resolution. OFL fonts in templates/fonts/ win; otherwise Pillow's built-in Aileron (CC0) is used for
both images and burned video captions, so a fresh checkout renders with a rights-safe font."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PIL import ImageFont


@dataclass
class FontSet:
    heading_id: str          # rights id, matched against config/rights.yaml fonts
    body_id: str
    heading_path: Path | None
    body_path: Path | None
    video_dir: Path          # folder handed to libass (fontsdir)
    video_family: str
    video_id: str

    def heading(self, size: int) -> ImageFont.FreeTypeFont:
        return _font(self.heading_path, size)

    def body(self, size: int) -> ImageFont.FreeTypeFont:
        return _font(self.body_path, size)

    @property
    def faux_bold(self) -> bool:
        return self.heading_path is None


def _font(path: Path | None, size: int):
    if path is not None:
        return ImageFont.truetype(str(path), size)
    return ImageFont.load_default(size=size)


def resolve(settings) -> FontSet:
    design = settings.config.brand.get("design", {})
    fonts = design.get("fonts", {})
    root = settings.root

    def pick(key: str):
        p = fonts.get(key)
        if p and (root / p).is_file():
            return root / p, Path(p).as_posix()
        return None, "pillow:Aileron"

    hp, hid = pick("heading")
    bp, bid = pick("body")
    family = design.get("video_font_family", "Inter")
    fdir = root / "templates" / "fonts"
    has_family = fdir.is_dir() and any(family.lower() in f.name.lower() for f in fdir.glob("*.[ot]tf"))
    if has_family:
        vdir, vfam, vid = fdir, family, f"templates/fonts/{family}"
    else:
        vdir = settings.data_dir / "fonts"
        vdir.mkdir(parents=True, exist_ok=True)
        ttf = vdir / "Aileron-Regular.ttf"
        if not ttf.is_file():
            ttf.write_bytes(ImageFont.load_default(size=40).font_bytes)
        vfam, vid = "Aileron", "pillow:Aileron"
    return FontSet(hid, bid, hp, bp, vdir, vfam, vid)
