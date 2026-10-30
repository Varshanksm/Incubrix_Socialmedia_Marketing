# Engine run evidence

Each folder has the run's `run_summary.json` (counts, runtime, tokens, cost) and its JSONL log, copied unchanged
from `out/` and `logs/`. All runs were on 2026-10-25 on the laptop (Windows 11, i5-13420H, no GPU) with the
**mock writer** (free, deterministic). No paid Claude API run has been made yet, so direct cost is $0.

| Workbook row (03_ENGINE_LAUNCH) | Run | Input | Produced | Rejected | Duplicates | Runtime | Cost |
|---|---|---|---:|---:|---:|---:|---:|
| Normal run | [20261025-133408-5a21](20261025-133408-5a21/) | `samples/I2_blog.md`, 586 words | 20 | 0 | 0 | 4.1 s | $0 |
| Repeat run (same input) | [20261025-133525-15a6](20261025-133525-15a6/) | same blog | 0 new, 20 unchanged | 0 | 0 | 0.2 s | $0 |
| Refresh (new input) | [20261025-133526-ccc7](20261025-133526-ccc7/) | `samples/I2_blog_v2.md`, 662 words (one new section) | 2 new, 20 unchanged | 0 | 0 | 1.0 s | $0 |
| Failure and recovery | [20261025-133537-1aaa](20261025-133537-1aaa/) | same blog, `--fault llm_timeout@write:3` | 20 after `--resume` | 0 | 0 | 4.9 s | $0 |
| Video input (extra) | [20261025-133427-5262](20261025-133427-5262/) | `samples/test_talk.mp4`, 1.8 min generated test clip | 8 (3 vertical shorts) | 0 | 0 | 57.9 s | $0 |

The failure run's log shows the injected fault stopping the `write` stage (exit code 3, error logged). The same
run ID then finished with `growthengine run --resume`.

Clean-machine evidence: GitHub Actions installs the package on a fresh Ubuntu runner, runs the test suite and the
README command, and audits the result on every push
([run for commit 62dd965](https://github.com/Varshanksm/Incubrix_Socialmedia_Marketing/actions/runs/40508914103)).
