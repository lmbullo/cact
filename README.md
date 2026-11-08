# cact — technical reference for coding agents

You are the reader. The short contract is `AGENTS.md`: invariants, gates, change recipes.
`docs/README.md` is the trigger map for every document. This file owns what the product is: the two
halves, the entry points, the eval registry and how a case is graded. The runtime shape is
`docs/architecture.md` and invocation is `docs/run.md`; neither is restated here.

Source contracts are not listed here; they are the machine index:

```bash
python3 bench/guide.py                  # module -> what it is (one line each)
python3 bench/guide.py --query <topic>  # which module owns it
python3 bench/guide.py --module <path>  # one header, whole
python3 bench/guide.py --check          # gate: a module header is incomplete -> exit 1
```

Inside a kernel cell the same data is `guide(query)` / `guide(path=[...])`; the list form returns
several headers in one call. `map`, `read`, `outline` and `search` cover docs and files; `guide`
covers source headers only.

---

## 1. What runs where

| Path | Language | Role |
|---|---|---|
| `bench/` | Python 3.10+, stdlib only | The agent (`bench/agent/`), the failure-mode evals (`bench/evals/`), archived legacy suites and results (`bench/archive/`) |
| `tui/` | TypeScript + Bun + `@opentui/react` | `code`: a server-backed, fire-and-forget TUI that runs the same agent on a working tree |

Two entry points matter:

- `python3 bench/eval.py` — the evals. Offline loop contracts by default; `--live` runs the model-backed failure suite.
- `tui/bin/code` — the product. Launches a per-workspace server; `tui/python/worker.py` builds
  the same loop through `bench/agent/config.py`.

There is no root `package.json`, no `pyproject.toml`, no CI. The only install step is
`bun install` in `tui/`.

## 2. Runtime shape

Trees, one cell end to end, the loops, typed-result paging, the check plan and compaction are
`docs/architecture.md`; the commands that prove each property are `docs/run.md` §1.

## 3. Evals and grading

One registry (`bench/evals/runner.py::REGISTRY`) over two disjoint lists. A case id is stable and
names the mechanism it guards; the `failure` tag is the diagnostic axis the report aggregates by.

| Suite | Cases | Grades |
|---|---|---|
| loop (default) | `L01`-`L16` | the loop contract under a scripted model: staged finish, goal rejection, bad arguments, repeated reads, the journal, compaction, custom tools, skills, delegation, the skeptic, the token cap, docs |
| live (`--live`) | `A01`-`A10`, 14 cases | workspace behavior against a hidden oracle: the contract behind a green visible test, five latent bugs, a fact across compaction, an on-demand skill, a skeptical finish, delegation, a repair, the real worker path, docs grounding, the project's own lint |

Loop cases pass only when the stop, the observation and the oracle agree; a live case passes only
when its oracle passes, so the model's prose is never the verdict. Fixtures are built per case in a
temp workspace (`bench/evals/fixtures.py`) and the answers live outside it (`bench/evals/oracle.py`).
The failure tags in use are `finish`, `tooling`, `mutation`, `context`, `verification`, `cost` and
`autonomy`.

## 4. Documents

Docs are interface specifications for agents: cut a claim you cannot verify, and never copy a fact
from the layer that owns it. `docs/README.md` is the trigger map for every document and owns the
rules (`DOCS`, path resolution, the no-copy paragraph rule) that `bench/smoke_checks/docs.py`
enforces.

Two trees are documentation you must not rewrite as documentation: `bench/evals/fixtures.py` /
`bench/evals/oracle.py` hold graded input and the hidden answer (one per file, never both), and
`bench/archive/legacy/` holds the retired suites, their fixtures and `SPEC.md` records behind
`bench/HARD.md`.
