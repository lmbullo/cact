# docs — pick one file by trigger, then edit

You are the reader. Nothing here is onboarding: no history, no pitch, no claim a command cannot
demonstrate. Every row below names a file that exists; a trigger with no owner is a missing gate,
not a missing document.

| Trigger | File | Owns |
|---|---|---|
| before any edit | `../AGENTS.md` | precedence, the 16-verb action space, invariants, change recipes, failure modes |
| what the product is | `../README.md` | identity, entry points, install, the eval registry and grading |
| about to run something | `run.md` | the gate ladder, each command's expected result and proof, the knobs |
| need the runtime shape | `architecture.md` | tree kinds, one cell end to end, the loops, paging, the check plan |
| TUI internals or its API | `../tui/README.md` | wire contract: state root, HTTP/WS, events, statuses, env |
| simplifying the action space | `../bench/HARD.md` | measured evidence for what the typed envelope earned |
| an agent under test | `../bench/evals/fixtures.py` / `../bench/evals/oracle.py` | graded input and hidden answer, one per file — never both in one |
| replaying a retired suite | `../bench/archive/legacy/` | the archived suites, their fixtures and `SPEC.md` records |

`python3 bench/guide.py` is the module map (`bench/` plus `tui/python/`); do not restate it in
Markdown. A module's contract is its four-section header, which `bench/guide.py --check` gates.

## Rules for these files

- One fact, one owner. The table above assigns every fact; the other file gets a pointer, not a
  second copy. Paraphrased duplication is a review defect, verbatim duplication is a gate failure.
- A backticked path must exist. `../bench/smoke_checks/docs.py` walks every document in `DOCS` and
  fails on a pointer that a rename would make dead, so renaming a file is a docs change too.
- `DOCS` is the checked set: `AGENTS.md`, `README.md`, `docs/README.md`, `docs/run.md`,
  `docs/architecture.md`, `tui/README.md`, `bench/HARD.md`. Adding a document means creating it,
  adding it to `DOCS` in `../bench/smoke_checks/docs.py`, and adding a row to the table above —
  then `python3 bench/smoke.py` passes or names the dead path.
- Two documents may not share a whitespace-normalised paragraph of 200 characters or more. At
  `AGENTS.md`/`README.md` scale that means: move the paragraph, then delete it here.
- Cut a sentence you cannot verify. A gate that does not exist is not an argument.
