# how it works — an agent is a control loop, not a person

This file is the argument behind the code: what cact optimises for, which human-shaped affordances
it deletes on purpose, and what replaces each one. It is not a tutorial (`docs/how-to.md` is) and
not a runtime map (`docs/architecture.md` is). Read it before changing the action space, the
prompt policy or the finish protocol, because every mechanism below is load-bearing.

## 1. Two claims

1. **An agent is a control loop with a job.** Model reply in, typed call out, structured
   observation back, until the goal is verified. Everything that is not part of that cycle is a
   cost: it spends tokens, adds a turn, and gives the model a place to be plausible instead of
   correct.
2. **Human-shaped affordances are the most expensive kind of cost.** A persona, a chat history
   read as memory, a request for approval, a status update written for a reader — each one
   *feels* like an agent working and is, mechanically, an unverified claim in the context window.

So the design question is never "how do we make the agent feel present?" It is "what is the
smallest set of typed things it must be able to do, and what must the environment do so it does not
have to be trusted?"

## 2. What was removed, and what took its place

| Human-shaped thing | Why it went | What it was replaced with |
|---|---|---|
| persona and first person | costs tokens; invites prose the loop cannot grade | a system policy that states the job, the gates and the stop rules |
| chat history as memory | grows without bound and drifts | bounded observations, `_OBS` handles, the session journal, compaction |
| asking a human | a fire-and-forget run has no human to answer | a default chosen and reported, or a structured refusal the loop can act on |
| "looks good to me" | not evidence | the check plan and the goal `Verifier` |
| a progress narration | unverifiable and unfalsifiable | state cards the loop derives from the call, not from the reply |
| an apology or a retry story | does not change the tree | one structured error with `code`, `message` and `use` |
| a permission prompt | stalls an unattended run | a mutation gate that refuses the wrong *shape* of call and names the right one |

Two consequences are deliberate. First, the agent may be terse to the point of seeming rude; the
report is data, not a courtesy. Second, the agent is never asked to be interesting, only to leave
the workspace in a state a verifier accepts.

## 3. The loop, concretely

One turn: the model replies; a reply with tool calls is dispatched; each call is shaped to the
verb's real signature before it runs; the kernel answers with one typed observation; the loop folds
that observation into the next prompt and drops what it can no longer use.

- **Calls are typed, so a wrong call is repairable.** The registry is the only catalog: name,
  schema, summary, `mutates`, function. `help(tool)` prints the live signature, and the provider
  schema is generated from the same entry, so there is no second description to drift. A malformed
  call is not an exception: it is `ok:false`, `bad_arguments`, the signature and the offending
  keyword, which the model can fix in one turn.
- **Observations are bounded, so context is spent on the task.** A typed result is one envelope
  (`ok`, `op`, `kind`, `data`, `page`, `handle`, `notes`, `error`) rendered as a state card. Big
  output goes to an `_OBS[n]` handle and is read back with `page`. A repeated read returns a
  pointer unless the caller asks to repeat it. This is context hygiene, not a read cap.
- **The tool is designed for the job.** `exec` takes argv and cwd, not a command string; `search`
  takes `include` and `context`, not a pipeline; `edit` takes a list and is atomic. When the common
  path is a typed verb, the shell stops being the catch-all.
- **Mutation is journalled.** `write`, `edit`, `splice`, `delete`, `mkdir` and `apply` are the only
  tracked way to change a file; a program that rewrites a file with `sed -i`, `rm` or a redirect is
  refused with a `use` hint that points at the verb. The point is not purity: it is that after a
  run, "what changed" is a fact and not a guess.

## 4. Verification is not persuasion

The agent never grades its own work in prose. Three layers do it instead:

1. **The check plan.** The workspace declares commands (`CODE_TUI_CHECK` for one,
   `CODE_TUI_CHECKS` for a JSON plan, otherwise detected from the tree). A cheap subset re-runs
   after a mutation and prints as the `[check]` line; the whole plan is the goal.
2. **The staged finish.** `final_answer` is staged, not accepted. The next reply with no tool call
   is the acceptance point, and with a goal it is rejected until the plan passes (`GOAL_UNMET`).
   A rejected finish is a repair instruction, not a question, which is why the message names the
   failing command and its first failure instead of asking the model what it wants to do.
3. **The oracle, outside the agent.** A loop case is graded by the stop, the observation and the
   oracle agreeing; a live case is graded by hidden tests the agent cannot read. A green visible
   suite is evidence about the fixture, never a pass. `bench/HARD.md` is the record of why that
   distinction had to be structural.

The green gate is the same idea applied to time: once the plan passes, further `exec` and `checks`
are refused while code cells and mutations stay open, so the agent stops re-verifying and cannot
quietly start over.

## 5. Autonomy without a human in the seat

A production run is unattended: nobody answers a question, reads the failing code, or repairs the
same session. That is a contract, not a hope.

- A finish that asks a human is rejected (`NEEDS_INPUT`). The agent may report that it is blocked,
  but the report is a result the caller reads, not a turn that waits for an answer.
- Where a decision is genuinely under-specified, the agent picks the defensible default and states
  it in the report. The instruction "choose and report" is in the policy because the alternative —
  stop and ask — is a deadlock at three in the morning.
- A failed check is a repair instruction. The loop keeps the turn and the budget, not the
  transcript.
- The budget is part of the contract: steps, wall seconds and tokens. When it runs out, the run
  stops and says what it did not finish. It does not get to summarise its way out.

## 6. State is not a transcript

The TUI board is append-only history; each task writes a journal of events, and the task id is
generated once. Reopening the UI shows what happened, not a re-rendered conversation. A new task in
the same workspace receives a short history of what previous tasks changed — files and one line
each — because that is the part a human would have remembered and the part that changes behaviour.

`read` and `page` treat the context window as a cache with a handle table, not a log. `recall`
searches this session's observations and survives a kernel restart; variables do not. That
asymmetry is intentional: durable facts go in a journal or a file, ephemeral ones die with the
process.

## 7. Why this is better at the job, not just tidier

The measurable claim is in `LOOP.md`: the same task, the same model, the same checks, with the
loop changes that removed retries and redundant verification. Half the total bill, a third fewer
steps, and a semantic audit that moved from 3/5 to 5/5 because the agent was told to read the
producer before writing the regex for an id shape.

The mechanism is boring: fewer wasted turns, fewer re-reads, fewer re-runs, one error shape. A
persona does none of that; it adds a paragraph to every turn.

## 8. Where the human belongs

The human writes the task, decides what "done" means (the checks), and reviews the diff. The agent
does not need to be liked, briefed twice, or kept company. It needs a workspace, a bounded action
space, a verifier and a budget — and then it needs to be left alone. When a run goes wrong, the
useful question is which of those four was wrong, not what the agent was feeling.

## 9. Non-goals

- No conversational product, no assistant persona, no simulated emotion, no emoji status theatre.
- No multi-agent society. Delegation exists (`delegate`, the skeptic) as a read-only child for one
  narrow job, not as a cast of characters.
- No benchmark-chasing: the evals guard failure modes the project has actually hit, and a change
  that only moves a score without a mechanism behind it is treated as noise.
- No "human in the loop" prompts inside a run. The loop is closed before the run starts.
