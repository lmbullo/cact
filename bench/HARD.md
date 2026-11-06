# HARD.md — evidence for the typed envelope (read before simplifying the tool surface)

You are the reader. This is a decision record, not a tutorial: it exists so an agent changing
the action space does not delete a mechanism that is carrying the benchmark. Run the commands
before and after such a change; do not trust the numbers as current forever.

Short version: `read`, `mutation` and `edge` are saturated, so they cannot measure the envelope.
The `hard` suite can, and it says the envelope does not make the model smarter — it makes the
same work cheaper, more reliable, and finishable under a latency budget.

## 1. Why the suite exists

Every ablation in `bench/traces/sweep/` for the easy suites lands within one task of every
other, including a bare loop. Those suites cannot tell you whether state cards, the journal, the
check plan and batched commits are worth their complexity. `hard` answers that, and `--mode
bare` is the control: one `python` tool, no helpers, no check plan.

| task | what it asks | oracle |
|---|---|---|
| `H1_spec` | `SPEC.md` is the contract; the visible suite is green but incomplete | held-out tests |
| `H2_bughunt` | ten modules with latent bugs behind a green visible suite | held-out tests |
| `H3_bigcsv` | four aggregates over 200k rows | exact answer |
| `H4_refactor` | rename an API and a field across sixteen call-site modules, delete the old name | held-out tests |

Held-out tests live in `bench/hard_hidden/`, outside the workspace, and run through
`bench/hard_runner.py`. The agent sees pass/fail and failing test names (the `[check]` line with
`--health on`) but cannot read or edit them. `H3_bigcsv` regenerates its CSV on import; it is
gitignored.

```bash
DEEPSEEK_API_KEY=... python3 bench/run.py --suite hard --mode state --tasks all --steps 40
DEEPSEEK_API_KEY=... python3 bench/run.py --suite hard --mode bare  --tasks all --steps 40
```

## 2. Measured baseline

deepseek-flash, effort low, 2 runs per mode, temperature 0.7:

| mode | pass | steps | prompt+completion tokens | cell errors | wall |
|---|---|---|---|---|---|
| `state` (envelope) | 8/8 | 30 | 55,265 | 1 | 79.6s |
| `bare` (control) | 8/8 | 76 | 303,978 | 15 | 203.9s |

Per task, the gap concentrates where the envelope has something to say:

| task | state steps | bare steps | why |
|---|---|---|---|
| `H1_spec` | 3, 3 | 9, 8 | per-cell `changed` and the check line stop the model re-reading its own work |
| `H2_bughunt` | 5, 4 | 17, 9 | one batched `edit`/`apply` plus failing-test names beats ten discover-edit cycles |
| `H3_bigcsv` | 3, 4 | 5, 3 | no envelope advantage; both write one program |
| `H4_refactor` | 4, 4 | 11, 14 | one batched `edit(edits=[...])`/`apply` edits sixteen modules in one journalled cell |

With a 15s per-task wall budget the gap becomes a completion gap:

| mode | pass | steps | tokens | errors | finished |
|---|---|---|---|---|---|
| `state` | 4/4 | 13 | 19,677 | 0 | 4/4 (`VERIFIED`/`DONE`) |
| `bare` | 4/4 | 27 | 78,494 | 5 | 1/4 (three `WALL_LIMIT`) |

The control's workspace was already correct when the wall limit hit on `H1_spec`, `H2_bughunt`
and `H4_refactor`, so the grader still counts them as passes. A product that requires finishing
inside the budget would score the control 1/4 and the envelope 4/4. That is the honest claim:
**same work, fewer steps, fewer tokens, fewer errors, more finishes under latency.**

## 3. What earned its keep

Keep these; each is measured, and removing one regresses the suite.

- **Per-cell `changed`/`delta`** from the tree snapshot. This used to be session-cumulative by
  mistake, which made `[state] changed:` lie and re-triggered health checks. Fixing it cut
  `H1_spec` from 7-9 steps to 3 and `E6_repair` from 9 to 3.
- **Batched, journalled mutation** (typed `edit(edits=[...])`/`apply`, `commit()` from a python
  cell). The control has to rediscover that it can edit many files in one cell; the rules tell
  the envelope, and `diff()`/`undo()` cover the batch.
- **Held-out verification** (the check plan / `[check]` line). The control stops when the visible
  tests are green; the envelope is rejected until the real oracle passes.
- **A staged finish** instead of the `UNREVIEWED` rejection: output is shown, the answer is held,
  and the next no-tool reply accepts it. This removed a whole class of wasted turns.
- **Reliable `open(path, "w").write(text)` journalling.** The model writes this way even when
  told to use `write()`, and a deferred commit used to land in the next cell.
- **Process-group journalling for `exec`/`run()` children.** A timed-out cell kills the kernel's
  group, but programs started by `exec`/`run()` are in their own sessions; they used to survive
  and hold ports. Each group leader is journalled and reaped on stop/restart. Contract test:
  `bench/smoke.py`.
- **`recall()` over `_OBS`.** The `[session state]` fold drops old messages, but full cell output
  stays in the kernel (and survives a restart); the model can search it instead of re-running a
  command.

## 4. What did not earn its keep

Do not reintroduce these; each was tried and measured.

- **The shell-verb wall.** Refusing `cat`/`grep`/`sed` while allowing `open().read()` was
  incoherent. `exec` (`argv`, `commands=[[...]]`, `shell=`) and `run()` now allow reads, searches,
  tests and read-only git, and refuse only programs/verbs that mutate the workspace (`tee`, `cp`,
  `mv`, `rm`, `sed -i`, `git apply`, `>`), because those make `diff()`/`undo()` lie. Mutations
  targeting a scratch root (`/tmp`, `/var/tmp`, `/dev/shm`, `$TMPDIR`) are allowed, so a temp
  build (`rm -rf /tmp/base`) is not blocked; `rm -rf /home` still is. The same classifier gates
  direct spawning, so a program cannot route around it with `subprocess`. Cell errors on the edge
  suite fell from 8 to 0.
- **Refusing `exec(["bash", "-lc", ...])` without a destination.** A bare hard refusal was tested
  and reverted: the model moved into `python` cells (31-36 calls across the docs runs) and the
  run got slower (64-86 turns) than the typed path (63 turns). The refusal stays, but names the
  destination: `bash -lc` in argv is `shell_via_exec`, pointing at `exec(shell=<string>)`, so the
  intent survives and there is still one process tool. Same lesson as the shell-verb wall: move
  the common path with a nudge; do not pretend to a capability boundary the kernel cannot hold.
- **Read-coverage gating at finish.** Removed after the 142-turn docs run hit five `UNREAD`
  rejections and the 83-turn guide run never engaged the gate: it only saw `read()`, while the
  model read through `run("sed ...")` and `Path.read_text()`. A partial `read()` could block a
  finish that a shell read would not. The `[read]` ledger stays as guidance; the finish no longer
  depends on which API was used.

## 5. Reproducing and extending

Per-run summaries are in `bench/traces/hard/` (`state-r*.json`, `bare-r*.json`,
`*-wall15.json`, `*-wall20.json`); per-turn journals are `bench/traces/H*.{state,bare}.jsonl`.
Traces are append-only: add a file, never rewrite one.

When you change the action space, the check plan, or the observation shape:

1. `python3 bench/smoke.py` — the offline contract gate.
2. Run both modes on `hard` with the same `--steps`, temperature and effort; record new files
   under `traces/hard/`.
3. Compare steps, tokens, cell errors and finishes, not just pass/fail. This suite is saturated
   on pass/fail for this model; the signal is cost and completion.
4. Update the tables here only with a run you can name by file.
