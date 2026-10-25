# growthengine

Turns **one long input** (a blog post, podcast, demo or webinar) into **a week of platform-ready social posts**
for LinkedIn, Instagram, YouTube Shorts, X and Threads. Each post gets a hook, caption, hashtags, one call to
action, a UTM tracking link, a final file, an editable source file and a posting slot. Nothing is exported until
it passes brand, accuracy, platform, rights and tracking checks. Output is a scheduler-ready calendar.

Built for the IncuBrix 2027 Growth Engineer assessment (Option 2). The plan is in [plan.md](plan.md).

## Run it

Needs Python 3.10+ and ffmpeg on PATH (ffmpeg is only needed for video inputs).

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows  (macOS/Linux: source .venv/bin/activate)
pip install -e .
growthengine run --input samples/I2_blog.md
```

That last line is **the run command**. Without an API key it uses the free **mock writer**, which is
deterministic and lower quality. Use it for trying the engine and for tests, never for submitted posts.
For real output, copy `.env.example` to `.env`, then set `GE_LLM=claude` and `ANTHROPIC_API_KEY=...`.

A video input, using a generated rights-clean test clip:

```bash
python tools/make_test_clip.py samples
growthengine run --input samples/test_talk.mp4
```

## What you get

```
content/                          the main output folder
  calendar.csv                    one row per Ready asset; columns = workbook tab 02_ASSETS, A to U
  scheduler_export.csv            platform, UTC + local time, text, media path, link, AI-label flag
  assets/<asset_id>/
    final.mp4 | final.pdf | final/ (PNG slides) | final.png | post.txt | thread.txt
    caption.txt                   exactly what gets posted
    source/                       editable: cut.json + captions.ass + render.cmd (video), slides.json (images)
    meta.json  checks.json        source trace, files, fonts, every check result
out/<run_id>/run_summary.json     produced, unchanged, rejected, duplicates, runtime, tokens, cost
out/rejected/<asset_id>/          anything that failed a check, with the reason in the database
logs/run-<run_id>.jsonl           one JSON line per event, errors included
```

Asset IDs look like `LI-CR-7f3a2c`: platform, format, then a hash of the idea's text. The same idea always
gets the same ID.

## Commands

| Command | What it does |
|---|---|
| `growthengine run --input <file-or-url>` | Make a week of assets (default 20, `--count N`) |
| `growthengine run --input talk.mp4 --transcript talk.vtt` | Video or audio input with its transcript (or a same-name `.vtt` next to it) |
| `growthengine run --input <x> --rights "Own recording"` | Declare who owns an input that isn't covered by `config/rights.yaml` |
| `growthengine run --resume <run_id>` | Continue a run that stopped, from where it stopped |
| `growthengine run --input <x> --rewrite` | Rewrite this input's existing assets in place (same IDs and slots, version + 1) |
| `growthengine audit --sample 20 --seed 7` | Rehearse IncuBrix's random 20-asset audit from the files on disk |
| `growthengine trace <asset_id>` | Follow one asset back to its input, location, product claims, rights and checks |
| `growthengine reject <asset_id> --reason "..."` | Human review: move an asset out of the main output |
| `growthengine record <asset_id> --url --views --er --clicks` | Log real-audience test results (they flow into `calendar.csv`) |
| `growthengine export --workbook <copy.xlsx> --out <filled.xlsx>` | Fill workbook tab 02_ASSETS (columns A to U only; formulas untouched) |
| `growthengine rerender <asset_id>` | Re-render a carousel or graphic after editing `source/slides.json` |
| `growthengine status` | Library counts by platform and format, plus recent runs with runtime and cost |

Inputs: `.md` `.txt` `.html` `.pdf` (with `pip install pypdf`), article URLs, `.vtt` `.srt`, and audio or video
(`.mp4 .mov .mkv .webm .mp3 .wav .m4a ...`). Audio or video without a transcript uses faster-whisper, if you
install it with `pip install -e .[stt]`.

## How it works

```
ingest -> moments -> plan -> write -> dedupe -> render -> check -> export
```

1. **Moments.** The writer pulls self-contained ideas from each section (or 20–55 s stretches from a
   transcript). A text idea must quote its section **verbatim**, or it is dropped. That's what makes every asset
   traceable. Results are cached per section.
2. **Plan.** Each idea feeds at most 2 assets, always on different platforms and in different formats. The mix
   follows `config/mix.yaml`, and posting slots come from `config/schedule.yaml` (stored in UTC).
3. **Write.** Claude (`claude-opus-5-5`) writes against a strict JSON schema. Brand rules and the whole input
   are a cached prefix, so each extra asset costs little. Refusals and cut-off answers reject only that one item,
   and server-side refusal fallback is on.
4. **Render.** Text posts and threads as `.txt`. Carousels and graphics as 1080×1350 PNGs (plus a PDF for
   LinkedIn). Shorts as 1080×1920 MP4s: cut at transcript boundaries, blurred-pad reframe, burned captions, a hook
   overlay for the first 2 s, and loudness normalisation.
5. **Check.** These are the brief's six checks, made concrete. Any failure means rejection:

| Brief check | What the engine verifies |
|---|---|
| On-brand and accurate | Hook ≤ 12 words; banned hype words; average sentence ≤ 18 words; emoji ≤ 2; exact "IncuBrix" spelling. Any sentence that names IncuBrix with a feature term must match `config/claims.yaml` (features **seen working**). Statistics must appear in the source |
| Platform-native | Caption, post and title limits; hashtag ranges; thread length; slide count; image size; video 1080×1920, ≤ 60 s, H.264 with audio |
| Useful | Real substance: no thin slides or posts, no generic hooks |
| Rights-safe | Input declared in `config/rights.yaml` or with `--rights`; fonts licensed (Pillow's built-in Aileron is CC0, OFL fonts go in `templates/fonts/`); no music |
| Measurable | `https` UTM link with source, medium, campaign and `utm_content = asset_id`; a target metric in the notes |
| Ready to publish | Final file, caption and scheduled slot all exist |

## No duplicates, refresh, recovery

- **Repeat run** on the same input: 0 new assets, 0 model calls, $0.
- **Refresh** with an edited input (`I2_blog_v2.md` is treated as the same source as `I2_blog.md`): only new or
  changed sections are read again. New ideas get new assets; existing assets keep their IDs and slots.
- **Near-duplicates:** a hook or body too similar to an existing asset on the same platform is rejected and
  counted.
- **Failure and recovery:** `growthengine run --input samples/I2_blog.md --fault llm_timeout@write:3` stops the
  run at the 3rd writer call and prints the resume command. `growthengine run --resume <run_id>` finishes it.
  The result is identical to a clean run, and the fault doesn't fire twice.

## Tests

```bash
pip install -e .[dev]
pytest -q
```

34 tests, no API key needed. They cover:
- units (IDs, ingest, compose, every check);
- the calendar columns and dropdown values against the official workbook;
- end-to-end runs with the mock writer;
- repeat, refresh, duplicates, fault + resume (compared with a clean run), audit tampering, human reject and
  workbook fill (skipped when the confidential workbook isn't present);
- the Claude writer against a stub client (request shape, cache prefix, fallback, cost, refusals, errors);
- vertical video rendering (skipped without ffmpeg);
- a scan for key-like strings.

CI runs the same on every push (`.github/workflows/ci.yml`).

## Runtime and cost

| Run (this laptop: i5-13420H, no GPU) | Input | Assets | Runtime | Direct cost |
|---|---|---|---|---|
| Mock, text | `samples/I2_blog.md` (586 words) | 20 | 0.05 min | $0 |
| Mock, video | `samples/test_talk.mp4` (1.8 min) | 8 | 0.93 min | $0 |
| Mock, repeat | same blog | 0 new | < 0.01 min | $0 |
| Claude | *to be measured with a real key* | | | from `run_summary.json` |

Cost is computed from token usage × `config/prices.yaml` (Opus 5.5: $4 / $20 per million input / output
tokens, cache reads $0.20; check before relying on it). Every run summary reports the measured figure.

## Configuration

| File | Holds |
|---|---|
| `config/brand.yaml` | Voice rules, banned words, CTAs per link mode, pillars + hashtags, design colours and fonts |
| `config/claims.yaml` | Product claims you have **seen working**, each with an evidence path. Empty until Day 1 |
| `config/platforms.yaml` | Platform and format limits; names match the workbook dropdowns |
| `config/mix.yaml` | The weekly mix, ideas-per-asset cap, target metrics |
| `config/schedule.yaml` | Weekly posting slots and time zone |
| `config/rights.yaml` | Declared inputs, licensed fonts, music (none) |
| `config/utm.yaml`, `config/prices.yaml` | Tracking-link convention, model prices |
| `.env` (from `.env.example`) | Writer, API key, model, effort, folders, fault injection. Never committed |

## Limitations (honest)

- The mock writer is only for testing. Submitted assets are written by Claude and reviewed by a person
  (`growthengine reject` for anything weak).
- `claims.yaml` starts empty. Until features are verified in the product, the copy doesn't describe IncuBrix's
  features at all.
- `final_file_url` and `source_file_url` are paths relative to the content folder. Set `GE_CONTENT_BASE_URL` if
  the folder is hosted somewhere with path-style links.
- The scheduler export is a generic CSV. Map it to a specific tool's bulk-upload format once IncuBrix picks one.
- Audio-only inputs make text formats only; shorts need a video stream.
- Instagram and YouTube Shorts captions can't carry clickable links, so the CTA points to the bio or channel link.

AI use and manual work are disclosed in [AI_USE.md](AI_USE.md). Sources are in [SOURCES.md](SOURCES.md), and
licences in [LICENSES.md](LICENSES.md).
