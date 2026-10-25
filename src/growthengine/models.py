"""Data shapes. The *Draft and AssetCopy models double as the JSON schemas the LLM must fill."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Optional

from pydantic import BaseModel, Field

Pillar = Literal["P1", "P2", "P3", "P4", "P5"]
Family = Literal["text", "thread", "carousel", "graphic", "video"]


# ---------- input ----------

@dataclass
class Cue:
    idx: int
    start_ms: int
    end_ms: int
    text: str


@dataclass
class Section:
    idx: int
    heading: str
    paragraphs: list[str]

    @property
    def text(self) -> str:
        return "\n\n".join(self.paragraphs)


@dataclass
class Doc:
    source_id: str              # stable name of the input (file name or URL), survives edits
    input_key: str              # sha256 of the content; changes when the content changes
    kind: Literal["text", "media"]
    title: str
    path: str                   # project-relative path or URL
    sections: list[Section] = field(default_factory=list)   # text inputs
    cues: list[Cue] = field(default_factory=list)            # media inputs (transcript)
    media_path: Optional[str] = None                         # absolute path of the audio/video file
    duration_ms: int = 0

    @property
    def words(self) -> int:
        if self.kind == "text":
            return sum(len(p.split()) for s in self.sections for p in s.paragraphs)
        return sum(len(c.text.split()) for c in self.cues)


# ---------- LLM schemas ----------

class MomentDraft(BaseModel):
    """One self-contained idea from a text section."""
    quote: str = Field(description="One or two sentences copied VERBATIM from the section that carry the idea")
    point: str = Field(description="The idea in one plain sentence, written for a creator")
    takeaway: str = Field(description="What the creator should do next, one sentence")
    details: list[str] = Field(description="2 to 6 short supporting points from the section, paraphrased")
    pillar: Pillar
    formats: list[Family] = Field(description="Formats this idea suits")
    score: int = Field(description="1 (weak) to 5 (strong): how useful and specific the idea is")


class MomentList(BaseModel):
    moments: list[MomentDraft]


class MediaMomentDraft(BaseModel):
    """One clip-worthy stretch of a transcript, chosen by cue numbers."""
    start_cue: int
    end_cue: int
    point: str
    takeaway: str
    details: list[str]
    pillar: Pillar
    score: int


class MediaMomentList(BaseModel):
    moments: list[MediaMomentDraft]


class Slide(BaseModel):
    title: str
    body: str


class AssetCopy(BaseModel):
    """Everything the writer produces for one asset. Fields that do not apply to the format stay empty."""
    hook: str = Field(description="First line / first 2 seconds. Specific promise or fact, max 12 words")
    body: str = Field(description="Main text after the hook (post body, carousel caption or video description)")
    posts: list[str] = Field(description="Thread only: the posts in order, first one is the hook; else empty")
    slides: list[Slide] = Field(description="Carousel: 6-10 slides, slide 1 is the hook. Graphic: exactly 1. Else empty")
    on_screen_hook: str = Field(description="Video only: hook text shown in the first 2 seconds, max 8 words; else empty")
    title: str = Field(description="YouTube Shorts only: title, max 100 characters; else empty")
    hashtags: list[str] = Field(description="Hashtags including the # sign, within the platform's range")


# ---------- stored moment ----------

@dataclass
class Moment:
    key: str
    source_id: str
    kind: Literal["text", "media"]
    quote: str
    point: str
    takeaway: str
    details: list[str]
    pillar: str
    formats: list[str]
    score: int
    section_idx: int = -1
    para_idx: int = -1
    start_ms: int = -1
    end_ms: int = -1
    unit_hash: str = ""
