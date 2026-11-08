# docs/run.md — every command, its expected result, and the fact it proves

You are the reader. This file owns invocation: the gate order, each command's expected exit code and
what it proves, and the knobs that change a run. The runtime shape those gates protect is
`architecture.md`; the eval registry and grading are `../README.md` §3; the source change behind a
failure is `../AGENTS.md` §6, and its symptom table is `../AGENTS.md` §8.

## 1. Gate order

Run in this order. A failure is a contract change, not a flaky test: fix the code or fix the
document in the same change.

| Command | Covers | Expected |
|---|---|---|
| `python3 bench/guide.py --check` | every module header has its four sections | exit 0 |
| `python3 -m ruff check .` | code: E/F/W/B against `ruff.toml` (fixtures and generated trees are excluded there) | exit 0 |
| `python3 bench/smoke.py` | imports, kernel prelude, `run()` allow/refuse, spawn gate, the exec duplicate-verb note, apply/commit, recall across restart, envelopes, search/map paging, reply/check parsing, header index, entry-point accuracy, the journal metrics, document paths and one-owner prose, the fast eval subset, `guide()`, ruff | exit 0 |
| `python3 bench/eval.py` | the 16 scripted loop contracts | exit 0 |
| `python3 -m compileall -q bench tui/python` | syntax | exit 0 |
| `cd tui && bun run typecheck` | TS types | exit 0 |
| `cd tui && bun test` | rendering/detection/selection tests | 0 fail; React `act(...)` warnings are expected noise |

Smoke is the fast signal: no key, no network, nothing written outside a temp dir. Python has no
unit-test framework, so smoke plus one real task is the whole story.

## 2. Evals

```bash
python3 bench/guide.py
python3 bench/guide.py --query checks
python3 bench/guide.py --module bench/agent/check_plan.py
python3 bench/guide.py --json --query checks
python3 bench/guide.py --check

python3 bench/eval.py                      # 16 scripted loop contracts, no key, no network
python3 bench/eval.py --list               # ids, kinds, failure tags
python3 bench/eval.py --tasks L04_bad_args,L15_autonomy
DEEPSEEK_API_KEY=... python3 bench/eval.py --live   # 14 model-backed failure cases
```

`--live` needs network and a key; the offline pass is the regression gate. Without a key, stop at
the offline gates and say so. Case ids and their failure tags are `bench/eval.py --list`; the
grading rule and the result row are `../README.md` §3.

Knobs: `--tasks`, `--model`, `--effort` (the provider's `reasoning_effort`, default `low`;
`--effort ""` omits it, `CODE_TUI_EFFORT=none` in the TUI), `--tag`, `--no-write`.
The TUI uses 300 steps / 1800s / 120s per cell; a live eval per case uses the `Budget` in its
case definition, including `max_total_tokens`.

## 3. Workspaces and live runs

```bash
python3 bench/smoke.py                 # offline contract check
python3 -m bench.snapshot freeze SRC DST   # clone + write DST.manifest.json
python3 -m bench.snapshot diff  DST        # added/modified/deleted vs the manifest
python3 bench/live.py --base bench/archive/results/ws/tui-base --tag r4 --prompt "make typecheck pass" \
        --check "bun run typecheck" --legacy r3
python3 bench/task_journal.py ~/.local/state/code-tui/workspaces/<hash> <task-id>
python3 bench/task_journal.py --compare ~/.local/state/code-tui/workspaces/<hash> A B  # an A/B
python3 bench/task_journal.py --recent 10  # newest tasks across every workspace
```

`task_journal.py <state-dir> <task-id>` prints one row per task: status, stop, steps, writes and
edits, restarts, rejections, the tool and verb mix — then the escape metrics `exec_verb_dupes`,
`python3_c`, `shell`, `obs_chars` and `first_mutation_turn` (the turn of the first mutating cell),
then the causal keys `dupe_note_turn`, `dupes_after_note`, `paged_followups` and
`obs_chars_per_turn`. The counts come from the `cell.start` records a TUI run writes, so
`sum(tools.values()) == cells`. `--compare <state-dir> A B` prints the same row for two tasks side
by side with deltas; `--recent N` scans every workspace's tasks, newest first, with no server or
key. The key list is the contract in `bench/task_journal.py`.

`live.py` is the honest end-to-end test: it clones `bench/archive/results/ws/tui-base`, drives
`tui/python/worker.py`, records `<tag>.events.jsonl`, `<tag>.prompt.txt`,
`<tag>.summary.json`, runs the check and diffs the tree.

## 4. TUI

```bash
cd tui && bun install
bun run typecheck      # tsc --noEmit; noUnusedLocals/noUnusedParameters are on
bun test               # 0 fail; React act(...) warnings are expected noise
./bin/code             # inside the target project; starts/reuses its server
./bin/code-stop        # SIGTERM the server for this directory
```

The agent's Python kernel is `python3` from `PATH` (`CODE_TUI_PYTHON` overrides); it imports
`bench.agent` from this repo, so the checkout path matters. `../tui/README.md` owns the server's
HTTP/WS surface, its env vars and the status mapping; this file owns only the commands.

## 5. Gotchas that change a run

- `bench/eval.py` has two import blocks: script mode inserts the repo root on `sys.path`;
  package mode uses relative imports. Keep both working.
- A live eval's oracle is the verdict. The model's prose is never the pass condition, and
  `autonomy=True` rejects a finish that asks a human.
- The TUI server detects the check plan **when a task starts**, so a script an earlier task added
  to `package.json` is picked up without a restart.
- `bench/archive/results/ws/tui-base` and `tui-broken` are archived frozen snapshots.
  Recreate a base with `python3 -m bench.snapshot freeze tui <dst>`; `bench/ws/` is generated
  and gitignored.
- Tests are TS only: the gate is `bun test` with 0 fail, not a fixed count. React `act(...)`
  warnings are expected noise. `bench/smoke.py` is the Python gate: its `style` group runs
  `ruff.toml` plus the "every public name has a docstring" check, and its `docs` group fails when a
  checked document names a path that no longer exists or copies a paragraph from another.
- Commit style: `area(scope): lowercase summary`, e.g.
  `bench(agent/kernel): add grep, write, edit` or `tui(tests): add log and prompt tests`.
