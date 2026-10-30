# Demo video scripts

The brief asks for two **unedited** videos: (a) a product walkthrough of up to 5 minutes, and (b) a technical demo
of up to 8 minutes showing one normal run, a refresh on new input, and recovery from one simple failure. Record
each in one take (OBS or the Windows Game Bar). Upload to YouTube as Unlisted, or to Drive with "Anyone with the
link", and paste the links into `00_START!B14` and `B15`.

## (b) Technical demo, 8 minutes max

**Before you press record** (PowerShell, in `E:\Raga Varshan\incubrix-growth-engine`):

```powershell
Remove-Item -Recurse -Force data, content, out, logs -ErrorAction SilentlyContinue   # start from an empty library
.venv\Scripts\python tools\make_test_clip.py samples                               # rights-clean test video
Copy-Item samples\I2_blog_v2.md "$env:TEMP\I2_blog_v2.md"                            # keep v2 ready for the refresh
```

Set the terminal font large (Ctrl + mouse wheel), and close other windows and notifications.

| Time | Do | Say (short) |
|---|---|---|
| 0:00 | Show the README on GitHub | "One long input in, a week of platform-ready posts out. One run command; tests run on every push." |
| 0:40 | `.venv\Scripts\growthengine run --input samples\I2_blog.md` | **Normal run.** "It pulls ideas from each section, plans 20 posts across 5 platforms, writes them, renders them and checks every one before export." Point at produced / rejected / runtime / cost in the summary |
| 1:40 | Open `content\calendar.csv`, then a carousel folder (`final\slide_01.png`) and a `post.txt` | "Same columns as the workbook. Each asset has a final file, an editable source, a UTM link and a slot." |
| 2:30 | `.venv\Scripts\growthengine trace <an asset id from the CSV>` | "Every post traces back to the exact paragraph it came from, plus its claims, rights and checks." |
| 3:10 | `.venv\Scripts\growthengine run --input samples\I2_blog.md` | **Repeat.** "Same input again: 0 new, 0 model calls, $0. No duplicates." |
| 3:40 | `.venv\Scripts\growthengine run --input samples\I2_blog_v2.md` | **Refresh on new input.** "v2 adds one section. Only that section is read again: 2 new posts, the other 20 keep their IDs and slots." |
| 4:30 | `.venv\Scripts\growthengine run --input samples\test_talk.mp4 --count 8` | "Video input: cut at transcript boundaries, 9:16, burned captions, a hook in the first 2 seconds." Open one `final.mp4` |
| 5:40 | `.venv\Scripts\growthengine run --input samples\I2_blog.md --source-id demo_fail --fault llm_timeout@write:3` | **Failure.** "I inject a timeout at the third writer call. The run stops, logs the error, and prints how to resume." Show exit message |
| 6:20 | Copy the printed command: `.venv\Scripts\growthengine run --resume <run_id>` | **Recovery.** "It continues from the failed step, finishes the rest, and the fault doesn't fire twice." |
| 7:00 | `.venv\Scripts\growthengine audit --sample 20 --seed 7`, then `.venv\Scripts\growthengine status` | "This rehearses your random 20-asset audit from the files on disk. Status shows runtime and cost per run." |
| 7:40 | Stop | "Code, tests and run evidence are in the repository." |

The failure step uses `--source-id demo_fail`, so the blog counts as a new source and gets fresh posts to write;
otherwise there would be nothing left to fail on.

If you have a Claude API key in `.env` (`GE_LLM=claude`), the same commands use the real writer, and the summary
shows real tokens and cost. Mention which writer the video uses.

## (a) Product walkthrough, 5 minutes max

Record yourself using IncuBrix with the access from your invitation email. Narrate what you're doing; this footage
is also your evidence for `01_INSIGHT`.

| Time | Show |
|---|---|
| 0:00 | Sign in, first screen, what the product says it does |
| 0:40 | Upload a short recording of your own (no other people's faces or voices) |
| 1:30 | Transcript editing: delete a sentence, show the video change |
| 2:20 | Clip extraction and word-by-word captions on the result |
| 3:20 | Scheduling/publishing screen (connect only your own test account) |
| 4:10 | Anything that didn't work or is marked "coming soon": say so plainly, with the time |
| 4:40 | Your one-line take: what would make creators share this |

Write each feature's result (Seen working / Partial / Unavailable, with the time) in `evidence/product/log.md`
straight after recording. Those rows become the five observations and `config/claims.yaml`.
