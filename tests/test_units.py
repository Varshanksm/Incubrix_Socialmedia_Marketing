import csv
from pathlib import Path

import pytest

from growthengine.compose import clean_tags, compose, utm_link
from growthengine.export import CALENDAR_COLUMNS
from growthengine.ids import asset_id, moment_key
from growthengine.ingest import InputError, parse_markdown, parse_subtitles, source_id_for, load
from growthengine.models import AssetCopy, Slide
from growthengine import checks

FIXTURES = Path(__file__).parent / "fixtures"


def copy(**kw):
    base = dict(hook="Cut your clips from the middle of the episode", body="Body text " * 20, posts=[], slides=[],
                on_screen_hook="", title="", hashtags=["#PodcastTips", "#CreatorTips", "#VideoPodcast"])
    base.update(kw)
    return AssetCopy(**base)


# ---------- ids ----------

def test_moment_key_ignores_case_whitespace_and_punctuation():
    assert moment_key("Start with the moments,  not the minutes.") == moment_key("start with the MOMENTS not the minutes")
    assert moment_key("one idea") != moment_key("another idea")


def test_asset_id_grows_only_on_collision():
    k = "abcdef123456"
    assert asset_id("LI", "TX", k) == "LI-TX-abcdef"
    assert asset_id("LI", "TX", k, taken={"LI-TX-abcdef"}) == "LI-TX-abcdef1"


# ---------- ingest ----------

def test_source_id_survives_versions():
    assert source_id_for("samples/I2_blog_v2.md") == source_id_for("samples/I2_blog.md") == "i2_blog"
    assert source_id_for("https://example.com/blog/my-post/") == "my-post"


def test_markdown_sections_comments_and_lists():
    title, secs = parse_markdown("# Title\n<!-- note -->\n## One\nFirst para.\n\n- item a\n- item b\n\n## Two\n> quoted text\n")
    assert title == "Title"
    assert [s.heading for s in secs] == ["One", "Two"]
    assert secs[0].paragraphs == ["First para.", "item a. item b."]
    assert secs[1].paragraphs == ["quoted text"]


def test_subtitles_parse_vtt_and_srt():
    vtt = parse_subtitles("WEBVTT\n\n00:00:01.000 --> 00:00:03.500\nHello <v Sam>there</v>\n\n00:01:02.000 --> 00:01:04.000\nAgain\n")
    assert [(c.start_ms, c.end_ms, c.text) for c in vtt] == [(1000, 3500, "Hello there"), (62000, 64000, "Again")]
    srt = parse_subtitles("1\n00:00:01,000 --> 00:00:02,000\nHi\n")
    assert srt[0].start_ms == 1000 and srt[0].text == "Hi"


def test_bad_inputs_fail_with_clear_errors(settings, project):
    (project / "x.docx").write_text("x")
    with pytest.raises(InputError, match="unsupported input type"):
        load("x.docx", settings)
    (project / "short.md").write_text("# T\n\nToo short.")
    with pytest.raises(InputError, match="too little text"):
        load("short.md", settings)
    with pytest.raises(InputError, match="not found"):
        load("missing.md", settings)


# ---------- compose ----------

def test_utm_link_has_all_params_and_asset_id(settings):
    link = utm_link(settings.config, "LinkedIn", "LI-TX-abc123")
    assert link.startswith("https://www.incubrix.com/?")
    for part in ("utm_source=linkedin", "utm_medium=social", "utm_campaign=ge2_launch", "utm_content=li-tx-abc123"):
        assert part in link


def test_hashtags_for_other_platforms_are_dropped():
    assert clean_tags(["#LinkedInTips", "ReelsTips", "#Hooks", "#hooks"], "Instagram") == ["#ReelsTips", "#Hooks"]


def test_thread_puts_cta_in_last_post(settings):
    c = copy(posts=["Post one is here.", "Post two is here.", "Post three is here.", "Post four is here."])
    out = compose(settings.config, c, "X", "Thread", "https://www.incubrix.com/?utm_content=x")
    assert "https://www.incubrix.com/?utm_content=x" in out["posts"][-1]
    assert all(len(p) <= 280 for p in out["posts"])


def test_threads_caption_fits_limit(settings):
    out = compose(settings.config, copy(body="A sentence that goes on. " * 60), "Threads", "Text post", "https://x.y/")
    assert len(out["caption"]) <= 500


# ---------- checks ----------

def _brand(settings, c, platform="LinkedIn", fmt="Text post"):
    composed = compose(settings.config, c, platform, fmt, "https://www.incubrix.com/?utm_content=a")
    fcfg = settings.config.formats[fmt]
    return {r["check"]: r for r in checks.brand_checks(settings.config, c.model_dump(), composed, fcfg["family"], fcfg)}, composed


def test_banned_words_and_casing_fail(settings):
    res, _ = _brand(settings, copy(body="This tool is a game-changer for Incubrix fans. " * 3))
    assert not res["brand.banned_words"]["ok"]
    assert not res["brand.name_casing"]["ok"]


def test_long_hook_fails(settings):
    res, _ = _brand(settings, copy(hook="This hook is far too long to work as the first line of any post at all"))
    assert not res["brand.hook_length"]["ok"]


def test_unverified_product_claim_is_rejected_and_verified_one_passes(settings):
    c = copy(body="IncuBrix pulls clips from your episode in minutes. Try it.")
    composed = compose(settings.config, c, "LinkedIn", "Text post", "https://x.y/")
    assert not checks.claims_check(settings.config, c.model_dump(), composed)["ok"]
    settings.config.claims["verified"] = [{"id": "clips", "match": ["pulls clips from"], "text": "...",
                                           "evidence": "evidence/x.png"}]
    assert checks.claims_check(settings.config, c.model_dump(), composed)["ok"]


def test_unsourced_statistic_is_rejected(settings):
    c = copy(body="This saves 50% of your editing time.")
    composed = compose(settings.config, c, "LinkedIn", "Text post", "https://x.y/")
    assert not checks.numbers_check(c.model_dump(), composed, {"quote": "", "point": "", "details": []})["ok"]
    assert checks.numbers_check(c.model_dump(), composed, {"quote": "It saves 50% of time", "point": "",
                                                           "details": []})["ok"]


def test_measurable_needs_matching_utm_content():
    good = "https://www.incubrix.com/?utm_source=x&utm_medium=social&utm_campaign=c&utm_content=x-td-abc"
    assert all(r["ok"] for r in checks.measurable_checks(good, "X-TD-abc", "target"))
    assert not checks.measurable_checks(good, "X-TD-zzz", "target")[0]["ok"]


def test_rights_require_declared_input_and_licensed_font(settings):
    ok = checks.rights_checks(settings.config, "samples/I2_blog.md", "", ["pillow:Aileron"], False)
    assert all(r["ok"] for r in ok)
    bad = checks.rights_checks(settings.config, "downloads/someone_elses.mp4", "", ["system:Arial"], True)
    assert not bad[0]["ok"] and not bad[1]["ok"]
    noted = checks.rights_checks(settings.config, "downloads/live.md", "Provided by IncuBrix for the live review", [], False)
    assert noted[0]["ok"]


# ---------- export schema ----------

def test_calendar_columns_match_the_workbook():
    with (FIXTURES / "assets_header.csv").open(encoding="utf-8") as f:
        header = next(csv.reader(f))
    assert CALENDAR_COLUMNS == header


def test_config_names_match_workbook_dropdowns(settings):
    import json
    dv = json.loads((FIXTURES / "assets_dropdowns.json").read_text(encoding="utf-8"))
    platforms = dv["B5:B154"].strip('"').split(",")
    formats = dv["C5:C154"].strip('"').split(",")
    assert set(settings.config.platforms) == set(platforms)
    assert set(settings.config.formats) == set(formats)
    for e in settings.config.mix["week"]:
        assert e["platform"] in platforms and e["format"] in formats
