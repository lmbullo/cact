# how it works — an agent is a control loop, not a person

This file is the argument behind the code: what cact optimises for, which human-shaped affordances
it removes on purpose, and what replaces each one. It is not a tutorial (`docs/how-to.md` is) or a
runtime map (`docs/architecture.md` is). Read it before changing the action space, the prompt
policy or the finish protocol, because every mechanism below is load-bearing.

## Two claims

1. **An agent is a control loop with a job.** Model reply in, typed call out, structured
   observation back, until the goal is verified. Everything outside that cycle is cost: it spends
   tokens, adds a turn and gives the model a place to be plausible instead of correct.
2. **Human-shaped affordances are the most expensive kind of cost.** A persona, a chat history
   read as memory, a request for approval, a status update written for a reader — each one feels
   like an agent working and is, mechanically, an unverified claim in the context window.

The design question is therefore not how to make the agent feel present. It is what is the
smallest set of typed things it must be able to do, and what must the environment do so it does
not have to be trusted.

## What was removed, and what took its place

| Human-shaped thing | Why it went | What replaced it |
|---|---|---|
| persona and first person | costs tokens; invites prose the loop cannot grade | a system policy that states the job, the gates and the stop rules |
| chat history as memory | grows without bound and drifts | bounded observations, `_OBS` handles, the session journal, the check plan |
| asking a human | a fire-and-forget run has no human to answer | a default chosen and reported, or a structured refusal the loop can act on |
| "looks good to me" | not evidence | the check plan and the prompt's imperative contract |
| a progress narration | unverifiable and unfalsifiable | state cards the loop derives from the call, not from the reply |
| an apology or a retry story | does not change the tree | one structured error with `code`, `message` and `use` |
| a permission prompt | stalls an unattended run | a mutation gate that refuses the wrong shape of call and names the right one |

Two consequences are deliberate. The report is data, not a courtesy. And the agent is never asked
to be interesting, only to leave the workspace in a state a verifier accepts.

## The loop, concretely

One turn: the model replies; a reply with tool calls is dispatched; each call is shaped to the
verb's real signature (`shape.rs`) before it runs; the verb answers with one typed observation;
the loop appends that observation as one state card and drops nothing else.

- **Calls are typed, so a wrong call is repairable.** `spec.rs` holds the provider specs and the
  one-line description the model reads, so there is no second catalog to drift. A malformed call
  is not an exception: it is `ok:false`, `bad_arguments`, the signature and the offending
  keyword, which the model can fix in one turn.
- **Observations are bounded, so context is spent on the task.** A typed result is one envelope
  (`ok`, `op`, `kind`, `data`, `page`, `handle`, `notes`, `error`) rendered as a state card. Big
  output goes to an `_OBS[n]` handle and is read back with `page`; the card budget cuts what one
  turn may deliver and the cut names the exact resume call. A repeated read returns a pointer
  unless the caller passes `repeat=true`. This is context hygiene, not a read cap.
- **The tool is designed for the job.** `exec` takes argv and cwd, not a command string; `search`
  takes `include` and `context`, not a pipeline; `edit` takes a list and is atomic. When the common
  path is a typed verb, the shell stops being the catch-all.
- **Mutation is journalled.** `write`, `edit`, `splice`, `delete`, `mkdir` and `apply` are the only
  tracked way to change a file; a program that rewrites a file with `sed -i`, `rm` or a redirect is
  refused with a `use` hint that points at the verb. After a run, "what changed" is a fact rather
  than a reconstruction.

## Verification is not persuasion

The agent never grades itself. `final_answer` is rejected until the check plan passes and every
explicit imperative in the task is supported by evidence the run actually produced: a commit, a
branch, a push, a command line, a changed path, a green check. `contract.rs` parses those
imperatives, including the negated ones (`do not commit` needs zero commits), and `Evidence`
records only a successful call or a recorded program line. Prose is never evidence.

The green gate applies the same idea to time: once the plan passes, `checks` answers from the
cached verdict and a gate re-run is refused while a cell or a mutation stays open, so the agent
stops re-verifying and cannot quietly start over. Any change to a file reopens the plan.

## Autonomy without a human in the seat

A production run is unattended: nobody answers a question, reads the failing code or repairs the
same session. That is a contract, not a hope.

- A finish that asks a human is rejected. The agent may report that it is blocked, but the report
  is a result the caller reads, not a turn that waits for an answer.
- Where a decision is genuinely under-specified, the agent picks the defensible default and states
  it in the report. "Choose and report" is in the policy because the alternative — stop and ask —
  is a deadlock at three in the morning.
- A failed check is a repair instruction. The loop keeps the turn and the budget, not the
  transcript. `GOAL_UNMET` is the only stop that says the model kept finishing against a red plan.
- The budget is part of the contract: steps, wall seconds, cells and total tokens. When it runs
  out, the run stops with `STEP_LIMIT`, `WALL_LIMIT` or `TOKEN_LIMIT` and says what it did not
  finish. It does not get to summarise its way out.

## State is not a transcript

The TUI board is append-only history; each task writes a journal of events, and the task id is
generated once. Reopening the UI shows what happened, not a re-rendered conversation. A new task in
the same workspace receives a short `[workspace history]` prefix — the last four rows, each with
the time, the one-line summary and the files changed, plus the open items (`deferred`, `untested`)
— because that is the part a human would have remembered and the part that changes behaviour.
`history.rs` bounds every field so the prefix cannot crowd out the task.

The handoff is structured for the same reason: `final_answer` takes `summary`, `changed`, `gates`,
`deferred` and `untested`, and the next task inherits those fields instead of re-deriving them from
a paragraph. `read` and `page` treat the context window as a cache with a handle table, not a log;
durable facts go in a journal or a file, and variables in the cell die with the child.

## Where the human belongs

The human writes the task, decides what "done" means (the checks) and reviews the diff. The agent
needs a workspace, a bounded action space, a verifier and a budget, and then needs to be left
alone. When a run goes wrong, the useful question is which of those four was wrong.

## Non-goals

- No conversational product, no assistant persona, no simulated emotion, no emoji status theatre.
- No multi-agent society. One process, one engine run, one kernel child per task.
- No benchmark-chasing: a change that only moves a score without a mechanism behind it is noise.
  The project keeps no cost record it cannot reproduce offline.
- No "human in the loop" prompts inside a run. The loop is closed before the run starts.
