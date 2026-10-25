"""Prompt text. The system block (brand rules + the whole input) is identical for every call in a run, so it
is written once to the prompt cache and read back cheaply by the rest."""
from __future__ import annotations

from growthengine.models import Cue, Doc, Moment


def brand_rules(cfg) -> str:
    b = cfg.brand
    v = b["voice"]
    pillars = "\n".join(f"- {pid}: {p['name']}" for pid, p in b["pillars"].items())
    claims = cfg.claims.get("verified") or []
    if claims:
        claim_lines = "\n".join(f"- {c['text']}" for c in claims)
        claims_txt = ("Verified product claims (the ONLY things you may say about IncuBrix, close to this wording):\n"
                      + claim_lines)
    else:
        claims_txt = ("No product features are verified yet. Do NOT mention IncuBrix or describe any product "
                      "feature. Teach the idea itself; the engine adds the call to action.")
    return f"""You write social posts for {b['name']}'s audience: podcasters, YouTubers, expert-led creators,
and the editors and small teams who help them (mainly US, Canada, UK, Ireland, Australia, New Zealand, Singapore).

Voice rules (every post is checked against them automatically):
- Talk to one creator as "you". Plain US English. Average sentence at most {v['avg_sentence_max_words']} words.
- The hook is a specific promise or fact, at most {v['hook_max_words']} words. No warm-up, no clickbait.
- Never use these words or phrases: {", ".join(v['banned_words'])}.
- No hook may start with: {", ".join(v['generic_hooks'])}.
- No statistics, percentages, money figures or "x times" claims unless they appear in the source text.
- No testimonials, customer names, or invented results. At most {v['emoji_max']} emoji (prefer none).
- Spell the brand exactly "{b['name']}".
- Every post teaches, shows or proves one specific thing a creator cares about. Not generic promotion.
- Do not write a call to action or a link; the engine adds exactly one.

{claims_txt}

Content pillars:
{pillars}
"""


def doc_block(doc: Doc) -> str:
    if doc.kind == "text":
        parts = [f"SOURCE ({doc.source_id}): {doc.title}"]
        for s in doc.sections:
            parts.append(f"\n[Section {s.idx}] {s.heading}".rstrip())
            parts += [f"({i}) {p}" for i, p in enumerate(s.paragraphs)]
        return "\n".join(parts)
    return f"SOURCE TRANSCRIPT ({doc.source_id}): {doc.title}\n" + "\n".join(cue_line(c) for c in doc.cues)


def cue_line(c: Cue) -> str:
    s = c.start_ms // 1000
    return f"[{c.idx}] {s // 60:02d}:{s % 60:02d} {c.text}"


def extract_text_prompt(section) -> str:
    return f"""From Section {section.idx} ("{section.heading or 'untitled'}") of the source above, extract 1 to 3
self-contained ideas a creator could act on. For each one:
- quote: one or two sentences copied EXACTLY from Section {section.idx} (verbatim; it is checked)
- point, takeaway, details (2-6), pillar, formats, score
Skip filler, introductions and anything that only makes sense with the rest of the article."""


def extract_media_prompt(first_cue: int, last_cue: int) -> str:
    return f"""Pick 3 to 8 clip-worthy stretches between cue [{first_cue}] and cue [{last_cue}] of the transcript above.
Each stretch must:
- run 20 to 55 seconds (check the timestamps), start where a sentence starts and end where one ends
- make sense with no other context, and teach, show or prove one thing
Return start_cue and end_cue (inclusive) plus point, takeaway, details (2-6), pillar and score."""


def format_rules(platform: str, pcfg: dict, fcfg: dict) -> str:
    fam = fcfg["family"]
    if fam == "text":
        return (f"A {platform} text post. body: {fcfg.get('min_words', 40)}-220 words with short paragraphs; "
                f"the first {pcfg.get('hook_window', 140)} characters must make people click 'see more'. "
                "posts, slides, on_screen_hook, title: empty.")
    if fam == "thread":
        return (f"A {platform} thread of {fcfg['posts'][0]}-{fcfg['posts'][1]} posts, each at most "
                f"{pcfg.get('post_max', 280) - 30} characters. posts[0] is the hook. body: one-line summary. "
                "slides, on_screen_hook, title: empty.")
    if fam == "carousel":
        return (f"A {platform} carousel of {fcfg['slides'][0] - 1}-{fcfg['slides'][1] - 1} slides (the engine adds "
                "a final call-to-action slide). Slide 1 title is the hook; each slide has a title of at most 8 "
                "words and a body of 8-30 words. body: a 1-3 sentence caption. posts, on_screen_hook, title: empty.")
    if fam == "graphic":
        return (f"A single {platform} graphic: exactly 1 slide with a title of at most 10 words and a body of "
                "at most 25 words. body: a 1-3 sentence caption. posts, on_screen_hook, title: empty.")
    title = "a title of at most 100 characters" if platform == "YouTube Shorts" else "empty"
    return (f"A {platform} short video cut from the quoted stretch. on_screen_hook: at most "
            f"{fcfg.get('hook_max_words', 8)} words shown in the first 2 seconds. body: a 1-3 sentence "
            f"description. title: {title}. posts, slides: empty.")


def write_prompt(m: Moment, platform: str, fmt: str, pcfg: dict, fcfg: dict) -> str:
    lo, hi = pcfg.get("hashtags", [0, 3])
    rules = format_rules(platform, pcfg, fcfg)
    where = (f"Section {m.section_idx}, paragraph {m.para_idx}" if m.kind == "text"
             else f"{m.start_ms // 1000}s-{m.end_ms // 1000}s")
    details = "\n".join(f"- {d}" for d in m.details)
    return f"""Write one asset.

FORMAT: {rules}
HASHTAGS: {lo}-{hi} specific hashtags (with #). Zero is fine when the range starts at 0.

IDEA (from the source above, {where}):
quote: {m.quote}
point: {m.point}
takeaway: {m.takeaway}
details:
{details}

Stay faithful to the source. Make the hook specific to this idea."""
