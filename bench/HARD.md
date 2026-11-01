# Does the envelope pay?

`bench/read`, `mutation` and `edge` are saturated: every ablation in `traces/sweep/` lands
within one task of every other, including a bare loop. They cannot tell you whether the
kernel's envelope (state cards, journal, check plan, batched commits) is worth its
complexity. The `hard` suite exists to answer that question, and `--mode bare` is the
control: one `python` tool, no helpers, no check plan, no read gate.

## The suite

| task | what it asks | oracle |
|---|---|---|
| `H1_spec` | `SPEC.md` is the contract; the visible suite is green but incomplete | held-out tests |
| `H2_bughunt` | ten modules with latent bugs behind a green visible suite | held-out tests |
| `H3_bigcsv` | four aggregates over 200k rows | exact answer |
| `H4_refactor` | rename an API and a field across sixteen call-site modules, delete the old name | held-out tests |

Held-out tests live in `bench/hard_hidden/`, outside the workspace, and run through
`bench/hard_runner.py`. The agent sees pass/fail and failing test names (the `[check]` line
when `--health on`) but cannot read or edit them. `H3_bigcsv` regenerates its CSV on import
and is gitignored.

```bash
DEEPSEEK_API_KEY=... python3 bench/run.py --suite hard --mode state --tasks all --steps 40
DEEPSEEK_API_KEY=... python3 bench/run.py --suite hard --mode bare  --tasks all --steps 40
```

## Measured (deepseek-flash, effort low, 2 runs per mode, temperature 0.7)

| mode | pass | steps | prompt+completion tokens | cell errors | wall |
|---|---|---|---|---|---|
| `state` (envelope) | 8/8 | 30 | 55,265 | 1 | 79.6s |
| `bare` (control) | 8/8 | 76 | 303,978 | 15 | 203.9s |

Per task, the gap concentrates where the envelope has something to say:

| task | state steps | bare steps | why |
|---|---|---|---|
| `H1_spec` | 3, 3 | 9, 8 | per-cell `changed` and the check line stop the model from re-reading its own work |
| `H2_bughunt` | 5, 4 | 17, 9 | one batched `commit` plus the failing-test names beats ten discover-edit cycles |
| `H3_bigcsv` | 3, 4 | 5, 3 | no envelope advantage; both write one program |
| `H4_refactor` | 4, 4 | 11, 14 | `commit({path: text})` edits sixteen modules in one journalled cell |

With a 15s per-task wall budget the gap becomes a completion gap:

| mode | pass | steps | tokens | errors | finished |
|---|---|---|---|---|---|
| `state` | 4/4 | 13 | 19,677 | 0 | 4/4 (`VERIFIED`/`DONE`) |
| `bare` | 4/4 | 27 | 78,494 | 5 | 1/4 (three `WALL_LIMIT`) |

The control's workspace was already correct when the wall limit hit on `H1_spec`,
`H2_bughunt` and `H4_refactor`, so the grader still counts them as passes. A product that
requires the agent to *finish* inside the budget would score the control 1/4 and the
envelope 4/4. This is the honest claim: **the envelope does not make the model smarter, it
makes the same work cheaper, more reliable and finishable under a latency budget.**
Pass/fail is already saturated for this model; the measured difference is steps, tokens,
errors and whether the loop closes.

## What earned its keep, and what did not

Earned:

- **Per-cell `changed`/`delta`** from the tree snapshot. This used to be session-cumulative
  by mistake, which made `[state] changed:` lie and re-triggered health checks. Fixing it
  cut `H1_spec` from 7-9 steps to 3 and `E6_repair` from 9 to 3.
- **Batched, journalled mutation** (`commit`/`apply`). The control has to rediscover that it
  can edit many files in one cell; the rules tell the envelope, and `diff()`/`undo()` cover
  the batch.
- **Held-out verification** (`check` plan / `[check]` line). The control stops when the
  visible tests are green; the envelope is rejected until the real oracle passes.
- **A staged finish** instead of the `UNREVIEWED` rejection: output is shown, the answer is
  held, and the next no-tool reply accepts it. This removed a whole class of wasted turns.
- **Reliable `open(path, "w").write(text)` journalling**. The model writes this way even when
  told to use `write()`, and a deferred commit used to land in the next cell.
- **Process-group journalling for `run()`**. A cell that times out kills the kernel's group,
  but `run()` children are in their own sessions; they used to survive and hold ports. Each
  group leader is now journaled and reaped on stop/restart. Contract test: `bench/smoke.py`.
- **`recall()` over `_OBS`**. The `[session state]` fold drops old messages, but the full
  cell output stays in the kernel; the model can search it instead of re-running a command.

Did not earn its keep:

- **The shell-verb wall.** Refusing `cat`/`grep`/`sed` while allowing `open().read()` was
  incoherent. `run()` now allows reads, searches, tests and read-only git, and refuses only
  verbs that mutate the workspace (`tee`, `cp`, `mv`, `rm`, `sed -i`, `git apply`, `>`),
  because those would make `diff()`/`undo()` lie. Mutations targeting a scratch root
  (`/tmp`, `/var/tmp`, `/dev/shm`, `$TMPDIR`) are allowed, so a temp build
  (`rm -rf /tmp/base`) is not blocked; `rm -rf /home` still is. Cell
  errors on the edge suite fell from 8 to 0.
- **Read-coverage gating at finish.** Removed after the 142-turn docs run hit five `UNREAD`
  rejections and the 83-turn guide run never engaged the gate at all: it only saw `read()`,
  while the model read through `run("sed ...")` and `Path.read_text()`. A partial `read()`
  could therefore block a finish that a shell read of the same file would not. The `[read]`
  ledger stays (it is useful guidance); the finish no longer depends on which API was used.

## Reproducing the comparison

The per-run summaries are in `bench/traces/hard/` (`state-r*.json`, `bare-r*.json`,
`*-wall15.json`, `*-wall20.json`) and the per-turn journals are
`bench/traces/H*.{state,bare}.jsonl`.
