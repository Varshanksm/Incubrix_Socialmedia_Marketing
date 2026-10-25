"""Deterministic, free writer. Good enough to exercise the whole pipeline and pass the checks; the submitted
assets are written with the Claude writer and reviewed by a human."""
from __future__ import annotations

from growthengine.ids import normalize
from growthengine.llm import Faults, Usage
from growthengine.models import AssetCopy, Cue, MediaMomentDraft, MomentDraft, Slide
from growthengine.text import fit, sentences, trim_words, words


class MockWriter:
    name = "mock"

    def __init__(self, settings, prices: dict, faults: Faults):
        self.settings = settings
        self.model = "mock"
        self.prices = prices
        self.faults = faults
        self.usage = Usage()
        self.pillars = settings.config.brand["pillars"]

    def prepare(self, doc) -> None:   # same interface as ClaudeWriter
        self.doc = doc

    # ---------- moments ----------
    def _pillar(self, text: str) -> str:
        t = " " + normalize(text) + " "
        scores = {pid: sum(t.count(f" {normalize(k)} ") for k in p["keywords"]) for pid, p in self.pillars.items()}
        best = max(scores.items(), key=lambda kv: (kv[1], -int(kv[0][1:])))
        return best[0] if best[1] > 0 else "P1"

    def extract_text_moments(self, section) -> list[MomentDraft]:
        self.faults.check("moments")
        self.usage.add(self.prices)
        paras = [(i, p) for i, p in enumerate(section.paragraphs) if len(words(p)) >= 20]
        paras.sort(key=lambda ip: -len(words(ip[1])))
        out = []
        for i, p in sorted(paras[:3]):
            ss = sentences(p)
            quote = ss[0] if len(words(ss[0])) >= 8 or len(ss) == 1 else " ".join(ss[:2])
            rest = [s for s in ss if s not in quote]
            takeaway = rest[-1] if rest else "Try this on your next episode."
            details = [s for s in rest if s != takeaway]
            if len(details) < 2 and i + 1 < len(section.paragraphs):   # borrow from the next paragraph only
                details += [s for s in sentences(section.paragraphs[i + 1]) if s != takeaway]
            out.append(MomentDraft(
                quote=quote, point=ss[0], takeaway=takeaway, details=details[:6] or [ss[0]],
                pillar=self._pillar(section.heading + " " + p),
                formats=["text", "thread", "carousel", "graphic"],
                score=min(5, 2 + len(words(p)) // 40)))
        return out

    def extract_media_moments(self, cues: list[Cue]) -> list[MediaMomentDraft]:
        self.faults.check("moments")
        self.usage.add(self.prices)
        out, i = [], 0
        while i < len(cues):
            j = i
            while j + 1 < len(cues) and cues[j].end_ms - cues[i].start_ms < 25_000:
                j += 1
            while j > i and cues[j].end_ms - cues[i].start_ms > 55_000:
                j -= 1
            span = cues[j].end_ms - cues[i].start_ms
            text = " ".join(c.text for c in cues[i:j + 1])
            if span >= 12_000 and len(words(text)) >= 25:
                ss = sentences(text)
                out.append(MediaMomentDraft(
                    start_cue=cues[i].idx, end_cue=cues[j].idx, point=ss[0],
                    takeaway=ss[-1] if len(ss) > 1 else ss[0], details=(ss[1:6] or ss[:1]),
                    pillar=self._pillar(text), score=min(5, 2 + len(words(text)) // 50)))
            i = j + 1
        return out

    # ---------- copy ----------
    def write(self, m, platform: str, fmt: str, pcfg: dict, fcfg: dict) -> AssetCopy:
        self.faults.check("write")
        self.usage.add(self.prices)
        fam = fcfg["family"]
        hook = trim_words(m.point.rstrip(".!?"), 12)
        lo, hi = pcfg.get("hashtags", [0, 3])
        # The whole pillar list; compose() drops other-platform tags and trims to the platform's maximum.
        tags = list(self.pillars.get(m.pillar, self.pillars["P1"])["hashtags"]) if hi else []
        if len(tags) < lo:
            tags += self.pillars["P1"]["hashtags"]
        empty = dict(posts=[], slides=[], on_screen_hook="", title="")

        if fam == "text":
            bullets = "\n".join(f"→ {d}" for d in m.details[:4])
            body = f"{m.point}\n\n{bullets}\n\n{m.takeaway}"
            if len(words(body)) < fcfg.get("min_words", 40):
                body = f"{body}\n\n{m.quote}"
            return AssetCopy(hook=hook, body=body, hashtags=tags, **empty)
        if fam == "thread":
            limit = pcfg.get("post_max", 280) - 40
            posts = [fit(s, limit) for s in [m.point, *m.details[:4], m.takeaway]]
            posts = list(dict.fromkeys(p for p in posts if p))[: fcfg["posts"][1]]
            while len(posts) < fcfg["posts"][0]:
                posts.append(fit(m.quote, limit))
            return AssetCopy(hook=hook, body=m.point, hashtags=tags,
                             **{**empty, "posts": posts})
        if fam == "carousel":
            slides = [Slide(title=hook, body="Swipe for the steps.")]
            slides.append(Slide(title="The idea", body=m.point))
            slides += [Slide(title=f"Step {n}", body=d) for n, d in enumerate(m.details[:6], 1)]
            slides.append(Slide(title="Do this next", body=m.takeaway))
            while len(slides) < fcfg["slides"][0] - 1:
                slides.insert(-1, Slide(title="Remember", body=m.quote))
            slides = slides[: fcfg["slides"][1] - 1]
            return AssetCopy(hook=hook, body=f"{m.point} {m.takeaway}", hashtags=tags,
                             **{**empty, "slides": slides})
        if fam == "graphic":
            return AssetCopy(hook=hook, body=f"{m.point} {m.takeaway}", hashtags=tags,
                             **{**empty, "slides": [Slide(title=hook, body=fit(m.takeaway, 140))]})
        # video
        title = fit(m.point, 100) if platform == "YouTube Shorts" else ""
        return AssetCopy(hook=hook, body=f"{m.point} {m.takeaway}", hashtags=tags,
                         **{**empty, "on_screen_hook": trim_words(hook, fcfg.get("hook_max_words", 8)),
                            "title": title})
