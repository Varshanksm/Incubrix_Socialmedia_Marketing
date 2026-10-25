"""Turn an AssetCopy into what is actually posted: caption with one CTA, hashtags and the UTM link."""
from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from growthengine.models import AssetCopy
from growthengine.text import fit

THREAD_SEP = "\n\n---\n\n"


def utm_link(cfg, platform: str, asset_id: str, campaign: str | None = None) -> str:
    u = cfg.utm
    parts = urlsplit(u["base_url"])
    q = dict(parse_qsl(parts.query))
    q.update({
        "utm_source": cfg.platforms[platform]["utm_source"],
        "utm_medium": u.get("medium", "social"),
        "utm_campaign": campaign or u.get("campaign", "ge2_launch"),
        "utm_content": asset_id.lower(),
    })
    return urlunsplit((parts.scheme, parts.netloc, parts.path or "/", urlencode(q), ""))


PLATFORM_WORDS = {"linkedin": "LinkedIn", "instagram": "Instagram", "reels": "Instagram", "youtube": "YouTube Shorts",
                  "shorts": "YouTube Shorts", "tiktok": "TikTok", "threads": "Threads", "twitter": "X"}


def clean_tags(tags: list[str], platform: str | None = None) -> list[str]:
    """#Tag format, no duplicates, and no hashtag that names a different platform (#LinkedInTips on Instagram)."""
    out = []
    for t in tags:
        t = "#" + "".join(ch for ch in t.lstrip("#") if ch.isalnum() or ch == "_")
        low = t.lower()
        if platform and any(w in low and p != platform for w, p in PLATFORM_WORDS.items()):
            continue
        if len(t) > 1 and low not in {x.lower() for x in out}:
            out.append(t)
    return out


def cta_text(cfg, platform: str, link: str) -> str:
    mode = cfg.platforms[platform].get("link_mode", "in_post")
    return cfg.brand["cta"][mode].format(link=link)


def compose(cfg, copy: AssetCopy, platform: str, fmt: str, link: str) -> dict:
    """Returns {caption, cta, posts (threads), hashtags}."""
    pcfg, fcfg = cfg.platforms[platform], cfg.formats[fmt]
    fam = fcfg["family"]
    tags = clean_tags(copy.hashtags, platform)[: pcfg.get("hashtags", [0, 5])[1]]
    tag_line = " ".join(tags)
    cta = cta_text(cfg, platform, link)

    if fam == "thread":
        limit = pcfg.get("post_max", 280)
        posts = [fit(p.strip(), limit) for p in copy.posts if p.strip()]
        last = posts[-1] if posts else copy.hook
        tail = f"\n\n{cta}" + (f" {tag_line}" if tag_line else "")
        posts[-1:] = [fit(last, limit - len(tail)) + tail]
        return {"caption": THREAD_SEP.join(posts), "cta": cta, "posts": posts, "hashtags": tags}

    head = copy.hook.strip()
    body = copy.body.strip()
    if body.lower().startswith(head.lower().rstrip(".!?")):
        body = body[len(head):].lstrip(" .!?\n")
    fixed = len(head) + len(cta) + len(tag_line) + 8
    body = fit(body, max(0, pcfg.get("caption_max", 2200) - fixed))
    caption = "\n\n".join(x for x in (head, body, cta, tag_line) if x)
    return {"caption": caption, "cta": cta, "posts": [], "hashtags": tags}
