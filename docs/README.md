# docs — pick one file by trigger, then edit

These files serve an agent that has to change the code without supervision; a human reads the
same text. Nothing here is onboarding: no history, no pitch, no claim a command cannot
demonstrate. Every row names a file that exists. A trigger with no owner is a missing gate, not a
missing document.

| Trigger | File | Owns |
|---|---|---|
| before any edit | `../AGENTS.md` | precedence, the typed action space, invariants, change recipes, failure modes |
| what the product is | `../README.md` | identity, entry points, install, gates, status |
| the engine module map | `../rust/README.md` | what each Rust module is, the build, the run commands |
| TUI internals or its API | `../tui/README.md` | wire contract: state root, HTTP/WS, events, statuses, env |
| why it is built this way | `how-it-works.md` | the design argument: the control loop, what replaced each human-shaped affordance, verification, non-goals |
| a task recipe | `how-to.md` | run, verify, extend, debug — one job per section, with the command and its normal result |
| about to run something | `run.md` | the gate ladder, each command's expected result and proof, the knobs |
| need the runtime shape | `architecture.md` | tree kinds, one cell end to end, the turn machine, paging, the check plan |

## Rules

- One fact, one owner. If two files state the same rule, delete one; the remaining file links to
  the other. Paraphrased duplication is a review defect.
- A backticked path must exist. Renaming a file is a docs change too.
- A module's contract is its `//!` header, which has four sections (`WHAT IT IS`, `ENTRY POINTS`,
  `INVARIANTS`, `WHERE TO CHANGE`). Read it with the engine's `guide` verb; do not restate it here.
- Documentation is prose, not marketing: the command and its expected result, not an adjective.
