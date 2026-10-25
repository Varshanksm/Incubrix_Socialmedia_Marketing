"""Which idea goes to which platform/format, and when it is posted.

Allocation is deterministic: moments are visited round-robin (best first, new ones first on a refresh), and
each takes the mix entry that is furthest below its target share. A moment feeds at most N assets, always on
different platforms and in different formats."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from growthengine.models import Moment

DAYS = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}


@dataclass
class Entry:
    platform: str
    fmt: str
    count: int
    needs_media: bool


@dataclass
class Planned:
    moment: Moment
    platform: str
    fmt: str


def mix_entries(cfg, has_video: bool) -> list[Entry]:
    out = []
    for e in cfg.mix["week"]:
        needs = e.get("needs") == "media"
        if needs and not has_video:
            continue
        out.append(Entry(e["platform"], e["format"], int(e["count"]), needs))
    return out


def compatible(cfg, m: Moment, e: Entry, has_video: bool) -> bool:
    fam = cfg.formats[e.fmt]["family"]
    if fam == "video":
        return has_video and m.kind == "media" and m.start_ms >= 0
    return fam in m.formats or m.kind == "media"


def allocate(cfg, moments: list[Moment], *, has_video: bool, uses: dict[str, list[tuple[str, str]]],
             entry_counts: dict[tuple[str, str], int], n_new: int, priority: set[str]) -> list[Planned]:
    entries = mix_entries(cfg, has_video)
    if not entries or n_new <= 0:
        return []
    cap = int(cfg.mix.get("max_assets_per_moment", 2))
    total_share = sum(e.count for e in entries)
    counts = {(e.platform, e.fmt): entry_counts.get((e.platform, e.fmt), 0) for e in entries}
    used = {m.key: list(uses.get(m.key, [])) for m in moments}
    order = sorted(moments, key=lambda m: (m.key not in priority, -m.score, m.section_idx, m.start_ms, m.key))

    planned: list[Planned] = []
    progress = True
    while len(planned) < n_new and progress:
        progress = False
        for m in order:
            if len(planned) >= n_new:
                break
            mine = used[m.key]
            if len(mine) >= cap:
                continue
            total = sum(counts.values()) + 1
            best, best_def = None, None
            for i, e in enumerate(entries):
                if not compatible(cfg, m, e, has_video):
                    continue
                if any(p == e.platform or f == e.fmt for p, f in mine):
                    continue
                deficit = e.count / total_share * total - counts[(e.platform, e.fmt)]
                if best is None or deficit > best_def + 1e-9:
                    best, best_def = e, deficit
            if best is None:
                continue
            planned.append(Planned(m, best.platform, best.fmt))
            mine.append((best.platform, best.fmt))
            counts[(best.platform, best.fmt)] += 1
            progress = True
    return planned


# ---------------- scheduling ----------------

def next_monday(today: date | None = None) -> date:
    today = today or date.today()
    return today + timedelta(days=(7 - today.weekday()) % 7 or 7)


def _slots(cfg, platform: str) -> list[tuple[int, time]]:
    out = []
    for s in cfg.schedule["slots"].get(platform, []):
        day, hm = s.split()
        h, m = hm.split(":")
        out.append((DAYS[day.lower()[:3]], time(int(h), int(m))))
    return sorted(out)


def to_utc_iso(dt_local: datetime) -> str:
    return dt_local.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def next_slot(cfg, platform: str, week_start: date, taken: set[str]) -> tuple[str, str]:
    """(utc_iso, local_label) of the first free weekly slot for the platform from week_start on."""
    tz = ZoneInfo(cfg.schedule.get("timezone", "UTC"))
    slots = _slots(cfg, platform) or [(0, time(9, 0))]
    for week in range(0, 104):
        for wd, t in slots:
            d = week_start + timedelta(days=7 * week + wd)
            local = datetime.combine(d, t, tzinfo=tz)
            iso = to_utc_iso(local)
            if iso not in taken:
                return iso, local.strftime("%a %d %b %Y %H:%M ") + local.tzname()
    raise RuntimeError(f"no free slot for {platform} in two years")
