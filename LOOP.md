# The loop bill: what the agent used to cost, what it costs now

This is a measurement record, not a contract. The machine contracts stay in `AGENTS.md`, the
module headers and `docs/`; the design argument is `docs/how-it-works.md`. Every number below comes
from a recorded run under `bench/ws/runs/` or `bench/evals/results/`.

The task is the same in every q1 row: add `shortHandle(id)` for a `t-<timestamp>-<5 hex>`
task id, cover it with a test, keep the suite green, do not commit.

## The short version

- The original default took **12 steps** and **153,707 total tokens** to finish q1.
- The current five-run mean takes **7.6 steps** and **74,732 total tokens**.
- That is **51.4% less total bill**, **36.7% fewer steps**, **44.9% less wall time**.
- Generated completion text is **33.7% lower** than the original default: 3,033 → 2,010.
- The loop now gets the *shape* right: the old semantic audit was **3/5**; the current
  audit is **5/5** on the real id shape.
- The live suite is **14/14** and costs **578,184 tokens** vs **619,359** for the first
  recorded full run. `A05_skeptic` no longer hits its token cap.
- Best single q1 run: **6 steps, 1,334 completion, 456 reasoning, 52,077 total, 19.0s**.
- A Daytona q1 run: **7 steps, 2,057 completion, 798 reasoning, 63,910 total, 24.3s**.

## The bill, side by side

| arm | steps | completion | reasoning | total | wall |
|---|---:|---:|---:|---:|---:|
| original default (`d0-default`) | 12 | 3,033 | 1,011 | 153,707 | 41.4s |
| first fast-code arm, mean of 4 (`n1`-`n4`) | 8.5 | 1,708.8 | 469 | 84,824.5 | 23.9s |
| first terse run (`q1-terse`) | 6 | 1,549 | 608 | 52,206 | 19.5s |
| current, mean of 5 local runs | 7.6 | 2,009.8 | 746.8 | 74,732.4 | 22.8s |
| current best (`z9a`) | 6 | 1,334 | 456 | 52,077 | 19.0s |
| current Daytona (`z11-daytona`) | 7 | 2,057 | 798 | 63,910 | 24.3s |

Against the original default:

```text
steps       -36.7%
completion  -33.7%
reasoning   -26.1%
total       -51.4%
wall        -44.9%
```

Against the first fast-code arm, the current loop uses **10.6% fewer steps** and **11.9% less
total bill**, but **17.6% more completion** and **59.2% more reasoning**. That is the trade:
the agent now reads the producer and writes a correct regex instead of saving a few tokens on a
wrong answer. The total bill is still lower because fewer/cheaper prompt turns dominate.

## Why the bill moved

1. **Typed calls are repaired before dispatch.** `bench/agent/call_shape.py` turns the shapes
   deepseek-flash actually emits into the typed signature: `exec(command=...)`, `exec(code=...)`,
   shell-in-argv, `read('_OBS[n]')`, single-file edit, `git add -A`, trailing JSON, and
   space-separated path strings. Those were retries; now they are one call.
2. **An exact `exec` of a plan command is `checks()`.** The model used to run `bun test` by hand,
   then `checks()`; now the audit is one cached parent-side call. This removed a full test run
   from the loop.
3. **`checks()` carries the result line.** A passing check now reports `Ran 7 tests across 5
   files`; the model no longer spends a turn re-running the suite just to see counts.
4. **The green gate stops verification, not work.** After a passing plan, `exec`/`checks` are
   refused; code cells and mutations stay open. The old gate stopped refactors halfway at the
   first green check. The new gate lets a refactor continue and closes again on a mutation.
5. **`read` accepts scratch paths.** A file written to `/tmp` can be read back in one call
   instead of producing `outside_root` and a retry.
6. **The VERIFY rule reads the producer.** A timestamp is often base36, not decimal. This is why
   the semantic audit moved from 3/5 to 5/5 without adding a task-specific oracle.
7. **Headers cost input tokens too.** The old worst header was 101 lines and the TUI client was
   82; every module is now ≤24 header lines, with `HEADER_ALLOW` empty. The loop also stopped
   shipping separate map/status turns: the environment card carries root/git/checks.

## Task completion

| signal | old loop | current loop |
|---|---:|---:|
| q1 visible check | pass | pass |
| q1 semantic audit, real base36 id | 3/5 | **5/5** |
| live suite oracles | 14/14 | **14/14** |
| live suite steps | 87 | **85** |
| live suite total tokens | 619,359 | **578,184** |
| live tokens/pass | 44,239.9 | **41,298.9** |
| `A05_skeptic` | 14 steps, TOKEN_LIMIT, 145,059 | 8 steps, DONE, 74,922 |
| `A05_skeptic` isolated | — | 6 steps, DONE, 52,129, 0 errors |

The old q1 failures were green tests that proved the wrong thing: a decimal `\d+` regex passed
the model's own decimal fixture. The current runs read `src/server.ts:426`, see
`Date.now().toString(36)`, and use a base36-capable pattern. That is the difference between
"the suite is green" and "the task is right".

All queued work completed. The Python kernel refactor turned `run_prelude.py` from **1062 lines
to 38**, with the assembled string byte-identical. The TUI split moved `client.tsx` **862 → 243**
and `server.ts` **726 → 111**; every TUI source is ≤250. The docs task ran **95 steps, 0 tool
errors** and committed `9c50c21`.

## Every q1 sample

| tag | steps | completion | reasoning | total | wall | note |
|---|---:|---:|---:|---:|---:|---|
| `d0-default` | 12 | 3,033 | 1,011 | 153,707 | 41.4s | original default |
| `n1-fast-code` | 7 | 1,643 | 534 | 68,532 | 20.6s | first fast-code |
| `n2-fast-code` | 10 | 1,847 | 471 | 103,665 | 26.9s | first fast-code |
| `n3-fast-code` | 6 | 1,403 | 341 | 52,657 | 17.4s | first fast-code |
| `n4-fast-code` | 11 | 1,942 | 530 | 114,444 | 30.6s | first fast-code |
| `q1-terse` | 6 | 1,549 | 608 | 52,206 | 19.5s | terse cards |
| `z9a` | 6 | 1,334 | 456 | 52,077 | 19.0s | current, best |
| `z9b` | 7 | 1,545 | 560 | 68,376 | 21.1s | current |
| `z10a` | 8 | 2,604 | 1,234 | 80,355 | 24.9s | current |
| `z10b` | 8 | 2,181 | 600 | 75,615 | 23.6s | current |
| `z10c` | 9 | 2,385 | 884 | 97,239 | 25.5s | current |
| `z11-daytona` | 7 | 2,057 | 798 | 63,910 | 24.3s | Daytona |
| `z12-effort-none` | 9 | 1,780 | 0 | 81,155 | 24.9s | rejected: no reasoning, more steps |
| `z8-shape` | 37 | 19,493 | 13,713 | 918,003 | 192.6s | rejected: broken custom oracle |

`z8-shape` is kept as a warning: a custom check with a wrong import path made the agent chase
the check for 37 steps. A check plan is only useful when its command is itself trustworthy.

## What is still not free

- The current mean generates **more completion text** than the first fast-code arm. That is the
  cost of producer verification; it buys the 3/5 → 5/5 semantic result. If the only goal were
  output tokens, `effort=none` gave 0 reasoning but 9 steps and 81k total, which is worse.
- The full live run had **1 rejected call** (A05). Isolated A05 was 0 errors; the remaining
  failures are text mismatches or a shorter-write refusal, not wrong tool shapes.
- Ten source files still sit above 250 lines in the shrink-only structure allowlist:
  `bench/task_journal.py` 557, `bench/smoke_checks/verbs.py` 620,
  `bench/evals/loops.py` 426, `bench/smoke_checks/helpers.py` 398,
  `bench/smoke_checks/turns.py` 393, `bench/agent/headers.py` 329,
  `bench/remote.py` 287, `bench/evals/live.py` 284,
  `bench/smoke_checks/files.py` 300, `tui/tests/log.test.tsx` 434.
  Those are the remaining refactor backlog, not loop regressions.

## Evidence

The raw rows are under `bench/ws/runs/` and `bench/evals/results/`; the q1 arms are
`d0-default`, `n1-fast-code` through `n4-fast-code`, `q1-terse`, `z9a`, `z9b`, `z10a`–`z10c`,
`z11-daytona`, and `z12-effort-none`. The live baseline is
`20261109T131922Z-live-fast-full.json`; the current full live pass is the
`14/14, 578,184` run printed after the header and TUI refactors.

The current loop is the one wired into the product: `fast-code` is the default profile in
`bench/agent/config.py` and `tui/src/server-config.ts`, at `effort=low`, inline finish and the
green-plan stop.
