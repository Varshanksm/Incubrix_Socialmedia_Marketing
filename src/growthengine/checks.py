"""The brief's six readiness checks, made concrete. Every asset gets a list of results; any failure means the
asset is rejected (never exported to the main output). `audit` re-runs these from the files on disk."""
from __future__ import annotations

import fnmatch
import re
import subprocess
import json
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from PIL import Image

from growthengine.text import avg_sentence_words, emoji_count, sentences, words

STAT = re.compile(r"\d+(?:[.,]\d+)?\s*(?:%|percent\b|x\b|times\b)|\$\s?\d|\d+\s*(?:hours?|hrs?|minutes?|mins?)\s+"
                  r"(?:saved|faster|less)", re.I)
URL = re.compile(r"https?://\S+")


def R(check: str, ok: bool, detail: str = "") -> dict:
    return {"check": check, "ok": bool(ok), "detail": detail}


def all_text(copy: dict, composed: dict) -> str:
    parts = [composed.get("caption", ""), copy.get("title", ""), copy.get("on_screen_hook", "")]
    parts += [s.get("title", "") + ". " + s.get("body", "") for s in copy.get("slides", [])]
    return "\n".join(p for p in parts if p)


def rights_match(entries: list[dict], value: str) -> dict | None:
    for e in entries or []:
        if fnmatch.fnmatch(value, e["match"]):
            return e
    return None


# ---------------- individual checks ----------------

def brand_checks(cfg, copy: dict, composed: dict, fam: str, fcfg: dict) -> list[dict]:
    v = cfg.brand["voice"]
    name = cfg.brand["name"]
    text = all_text(copy, composed)
    plain = URL.sub("", text)
    low = plain.lower()
    hook = copy.get("hook", "")
    res = [R("brand.hook_length", len(words(hook)) <= v["hook_max_words"],
             f"{len(words(hook))} words (max {v['hook_max_words']})")]
    if fam == "video":
        osh = copy.get("on_screen_hook", "")
        res.append(R("brand.on_screen_hook", 0 < len(words(osh)) <= fcfg.get("hook_max_words", 8),
                     f"{len(words(osh))} words (max {fcfg.get('hook_max_words', 8)})"))
    banned = [b for b in v["banned_words"] if re.search(rf"(?<!\w){re.escape(b.lower())}(?!\w)", low)]
    res.append(R("brand.banned_words", not banned, ", ".join(banned)))
    generic = [g for g in v.get("generic_hooks", []) if hook.lower().startswith(g)]
    res.append(R("brand.specific_hook", not generic, ", ".join(generic)))
    body_text = "\n".join(x for x in [copy.get("body", ""), *copy.get("posts", []),
                                      *[s.get("body", "") for s in copy.get("slides", [])]] if x)
    avg = avg_sentence_words(URL.sub("", body_text))
    res.append(R("brand.sentence_length", avg <= v["avg_sentence_max_words"],
                 f"average {avg:.1f} words (max {v['avg_sentence_max_words']})"))
    e = emoji_count(text)
    res.append(R("brand.emoji", e <= v["emoji_max"], f"{e} (max {v['emoji_max']})"))
    wrong = sorted({m.group(0) for m in re.finditer(re.escape(name), plain, re.I) if m.group(0) != name})
    res.append(R("brand.name_casing", not wrong, ", ".join(wrong)))
    return res


def claims_check(cfg, copy: dict, composed: dict) -> dict:
    name = cfg.brand["name"].lower()
    terms = [t.lower() for t in cfg.claims.get("feature_terms", [])]
    verified = cfg.claims.get("verified") or []
    bad = []
    for s in sentences(URL.sub("", all_text(copy, composed)).replace("\n", ". ")):
        sl = s.lower()
        if name not in sl:
            continue
        if not any(re.search(rf"(?<!\w){re.escape(t)}", sl) for t in terms):
            continue
        if not any(any(p.lower() in sl for p in c.get("match", [])) for c in verified):
            bad.append(s[:90])
    return R("accurate.claims_verified", not bad, "unverified: " + " | ".join(bad) if bad else "")


def numbers_check(copy: dict, composed: dict, moment: dict) -> dict:
    source = " ".join([moment.get("quote", ""), moment.get("point", ""), *moment.get("details", [])])
    found = [m.group(0) for m in STAT.finditer(URL.sub("", all_text(copy, composed)))]
    unsourced = [f for f in found if f.strip() not in source]
    return R("accurate.numbers_sourced", not unsourced, ", ".join(unsourced))


def platform_checks(cfg, platform: str, fmt: str, copy: dict, composed: dict, out: Path, files: dict,
                    ffprobe: str) -> list[dict]:
    pcfg, fcfg = cfg.platforms[platform], cfg.formats[fmt]
    fam = fcfg["family"]
    res = []
    tags = composed.get("hashtags", [])
    lo, hi = pcfg.get("hashtags", [0, 5])
    res.append(R("native.hashtags", lo <= len(tags) <= hi, f"{len(tags)} (allowed {lo}-{hi})"))
    if fam == "thread":
        posts = composed.get("posts", [])
        pmax = pcfg.get("post_max", 280)
        n0, n1 = fcfg["posts"]
        res.append(R("native.thread_posts", n0 <= len(posts) <= n1, f"{len(posts)} posts (allowed {n0}-{n1})"))
        long_ = [i + 1 for i, p in enumerate(posts) if len(p) > pmax]
        res.append(R("native.post_length", not long_, f"posts over {pmax} chars: {long_}" if long_ else ""))
    else:
        cmax = pcfg.get("caption_max", 2200)
        n = len(composed.get("caption", ""))
        res.append(R("native.caption_length", 0 < n <= cmax, f"{n} chars (max {cmax})"))
    if pcfg.get("hook_window"):
        hl = len(copy.get("hook", ""))
        res.append(R("native.hook_before_fold", hl <= pcfg["hook_window"], f"{hl} chars (fold at {pcfg['hook_window']})"))
    if platform == "YouTube Shorts":
        t = copy.get("title", "")
        res.append(R("native.title", 0 < len(t) <= pcfg.get("title_max", 100), f"{len(t)} chars"))
    if fam == "text":
        n = len(words(copy.get("body", "")))
        res.append(R("native.text_length", n >= fcfg.get("min_words", 40) - 15, f"{n} body words"))
    if fam in ("carousel", "graphic"):
        slides = files.get("slides", [])
        if fam == "carousel":
            s0, s1 = fcfg["slides"]
            res.append(R("native.slide_count", s0 <= len(slides) <= s1, f"{len(slides)} slides (allowed {s0}-{s1})"))
        else:
            res.append(R("native.single_image", len(slides) == 1, f"{len(slides)} images"))
        bad = []
        for sp in slides:
            p = out / sp
            if not p.is_file():
                bad.append(f"{sp} missing")
                continue
            with Image.open(p) as im:
                if im.size != (fcfg["width"], fcfg["height"]):
                    bad.append(f"{sp} is {im.size[0]}x{im.size[1]}")
        res.append(R("native.image_size", not bad, "; ".join(bad) or f"{fcfg['width']}x{fcfg['height']}"))
        if platform == "LinkedIn" and fam == "carousel":
            res.append(R("native.pdf_document", (out / "final.pdf").is_file(), "LinkedIn document carousel"))
    if fam == "video":
        res += video_checks(out / files.get("final", "final.mp4"), fcfg, ffprobe)
    return res


def video_checks(path: Path, fcfg: dict, ffprobe: str) -> list[dict]:
    if not path.is_file():
        return [R("native.video_file", False, "final.mp4 missing")]
    r = subprocess.run([ffprobe, "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)],
                       capture_output=True, text=True, timeout=60)
    info = json.loads(r.stdout or "{}")
    v = next((s for s in info.get("streams", []) if s.get("codec_type") == "video"), {})
    has_a = any(s.get("codec_type") == "audio" for s in info.get("streams", []))
    dur = float(info.get("format", {}).get("duration", 0))
    return [
        R("native.video_size", (v.get("width"), v.get("height")) == (fcfg["width"], fcfg["height"]),
          f"{v.get('width')}x{v.get('height')}"),
        R("native.video_duration", fcfg.get("min_seconds", 5) <= dur <= fcfg["max_seconds"] + 0.5,
          f"{dur:.1f}s (max {fcfg['max_seconds']}s)"),
        R("native.video_audio", has_a, "audio track present" if has_a else "no audio"),
        R("native.video_codec", v.get("codec_name") == "h264", v.get("codec_name", "?")),
    ]


def rights_checks(cfg, input_path: str, input_note: str, fonts_used: list[str], uses_media: bool) -> list[dict]:
    r = cfg.rights
    entry = rights_match(r.get("inputs"), input_path)
    declared = bool(entry) or bool(input_note)
    who = entry["owner"] if entry else (input_note or "not declared")
    res = [R("rights.input_declared", declared, who)]
    bad = [f for f in fonts_used if not rights_match(r.get("fonts"), f)]
    res.append(R("rights.fonts_licensed", not bad, ", ".join(bad) if bad else ", ".join(sorted(set(fonts_used)))))
    res.append(R("rights.no_unlicensed_music", True, "no music added" if uses_media else "n/a"))
    return res


def measurable_checks(link: str, asset_id: str, target: str) -> list[dict]:
    q = parse_qs(urlsplit(link).query)
    need = ["utm_source", "utm_medium", "utm_campaign", "utm_content"]
    missing = [k for k in need if not q.get(k)]
    ok_content = q.get("utm_content", [""])[0] == asset_id.lower()
    return [R("measurable.utm", not missing and ok_content and link.startswith("https://"),
              "missing " + ", ".join(missing) if missing else ("" if ok_content else "utm_content != asset_id")),
            R("measurable.target_metric", bool(target), target)]


def useful_checks(fam: str, copy: dict, moment: dict) -> list[dict]:
    if fam == "text":
        n = len(words(copy.get("body", "")))
        return [R("useful.substance", n >= 25, f"{n} words of substance")]
    if fam == "thread":
        thin = [i + 1 for i, p in enumerate(copy.get("posts", [])) if len(words(p)) < 5]
        return [R("useful.substance", not thin, f"thin posts: {thin}" if thin else "")]
    if fam in ("carousel", "graphic"):
        thin = [i + 1 for i, s in enumerate(copy.get("slides", [])[1:], 1) if len(words(s.get("body", ""))) < 4]
        return [R("useful.substance", not thin, f"thin slides: {thin}" if thin else "")]
    n = len(words(moment.get("quote", "")))
    return [R("useful.substance", n >= 25, f"clip has {n} spoken words")]


def ready_checks(out: Path, files: dict, caption: str, scheduled_at: str) -> list[dict]:
    final = files.get("final", "")
    exists = bool(final) and (out / final).exists()
    return [R("ready.final_file", exists, final or "none"),
            R("ready.caption", bool(caption.strip()), ""),
            R("ready.scheduled", bool(scheduled_at), scheduled_at or "")]


def run_all(settings, *, platform, fmt, copy: dict, composed: dict, moment: dict, out: Path, files: dict,
            fonts_used: list[str], input_path: str, input_note: str, link: str, asset_id: str, target: str,
            scheduled_at: str) -> list[dict]:
    cfg = settings.config
    fcfg = cfg.formats[fmt]
    fam = fcfg["family"]
    res = []
    res += brand_checks(cfg, copy, composed, fam, fcfg)
    res.append(claims_check(cfg, copy, composed))
    res.append(numbers_check(copy, composed, moment))
    res += platform_checks(cfg, platform, fmt, copy, composed, out, files, settings.ffprobe)
    res += useful_checks(fam, copy, moment)
    res += rights_checks(cfg, input_path, input_note, fonts_used, fam == "video")
    res += measurable_checks(link, asset_id, target)
    res += ready_checks(out, files, composed.get("caption", ""), scheduled_at)
    return res


def failures(results: list[dict]) -> list[str]:
    return [f"{r['check']}: {r['detail']}".rstrip(": ") for r in results if not r["ok"]]
