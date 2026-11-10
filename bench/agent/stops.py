"""stops: the stop vocabulary: names a RunResult, turn record or trace summary may carry.
WHAT IT IS
  One constant per way a run or one call can end, plus STOPS, the tuple that pins the set.
  Nothing executes; tool_loop.py re-exports every name.
ENTRY POINTS
  DONE, VERIFIED, STEP_LIMIT, WALL_LIMIT, STAGED, GOAL_UNMET, TRUNCATED, BAD_ARGS,
  LLM_FAILED, TOKEN_LIMIT, SKEPTIC_UNMET, GREEN, NEEDS_INPUT ... one string per stop reason
  STOPS ... every stop name, in reader order
INVARIANTS
  a new stop reason = this module + STOPS in the same edit; smoke_checks/turns.py pins the set.
  a stop is data: CallRecord.stop / turn record / trace summary; `notice` carries readable text.
  GREEN = the one call-level name (post-plan gate refusal); STAGED, GOAL_UNMET, SKEPTIC_UNMET,
  NEEDS_INPUT continue the run.
WHERE TO CHANGE
  new stop reason ......... this module, then finish_flow.py or tool_loop.ToolLoop.run
  vocabulary readers ...... records.py, tool_loop.ToolLoop.run, bench/task_journal.py, tui/src
"""
from __future__ import annotations

DONE = "DONE"                    # the model finished; its answer (or prose) is final
VERIFIED = "VERIFIED"            # goal_stops: a mutating cell passed the goal
STEP_LIMIT = "STEP_LIMIT"        # budget.max_steps exhausted
WALL_LIMIT = "WALL_LIMIT"        # budget.max_wall_s exhausted
STAGED = "STAGED"                # turn staged an answer; the run continues
GOAL_UNMET = "GOAL_UNMET"        # a finish was rejected; the run continues
TRUNCATED = "TRUNCATED"          # the reply was cut off; nothing ran
BAD_ARGS = "BAD_ARGS"            # the call arguments were unusable; nothing ran
LLM_FAILED = "LLM_FAILED"        # the primary model call raised LLMError; the run stops
TOKEN_LIMIT = "TOKEN_LIMIT"      # Budget.max_total_tokens exhausted; the run stops
SKEPTIC_UNMET = "SKEPTIC_UNMET"  # the skeptic found a gap; the run continues
GREEN = "GREEN"                  # the check plan passed; further gate calls are skipped
NEEDS_INPUT = "NEEDS_INPUT"      # an unattended finish asked a human; the run continues
STOPS = (DONE, VERIFIED, STEP_LIMIT, WALL_LIMIT, STAGED, GOAL_UNMET, TRUNCATED, BAD_ARGS,
         LLM_FAILED, TOKEN_LIMIT, SKEPTIC_UNMET, NEEDS_INPUT, GREEN)
