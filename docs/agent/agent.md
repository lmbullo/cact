# agent
generated_by=bench/agent/pages.py
source_headers=bench/agent/headers.py
source_symbols=bench/index.py
subsystem=agent
page=docs/agent/agent.md
modules=44
module=bench/agent/__init__.py symbols=-
entry=kernel/ ................ Kernel/Cell (`run` python cell, `call` typed verb); protocol.py = wire | shapes, process.py = supervisor | verbs.py / tools.py ... verb registry (schema + summary + function) / provider specs | headers.py ............ guide() + header index; shipped to a kernel cell as a verb | ToolLoop/StateLoop/ReplLoop  loop modules; config.build() selects one | Client, LLMError ...... llm.py;  Check, run_check, evaluate = check_run.py (plans in check_plan.py) | Budget, RunResult, _usage_add = loop.py;  Trace, render = trace.py
module=bench/agent/call_shape.py symbols=Repair,parse_args,repair
entry=repair(name, args, allows=None) -> Repair(name, args, note) | Repair  .name / .args / .note / .changed
module=bench/agent/check_plan.py symbols=Verifier,Verifier.has_goal,Verifier.cheap_specs,Verifier.should_run,Verifier.run,Verifier.authoritative,Verifier.goal_value,Verifier.cache,Verifier.invalidate
entry=Verifier(specs=(), cwd="", *, check=None, window=HEALTH_WINDOW, legacy=False, cheap_enabled=True) | .should_run(paths, now) -> bool (throttle + scope, reserves the window);  .run(paths) -> {cmd: Check} (sets .last, fills .cache()) | .authoritative() -> (ok, Check | None);  .cache() / .invalidate();  .goal_value() -> Check | bool | re-exported from check_spec: CheckSpec, run_plan, run_specs, run_each; one command is check_run.run_check.
module=bench/agent/check_run.py symbols=Check,Check.ok,Check.summary,Check.first_failure,Check.text,from_output,run_check,evaluate
entry=Check(cmd, state, exit_code, duration_ms, stdout, stderr, failures) | state = "passed" | "failed" | "unrunnable";  .ok, .text(limit);  .summary(limit) last count | run_check(cmd, cwd, timeout=600.0) -> Check | from_output(cmd, exit_code, stdout, stderr, duration_ms) -> Check   already-run command | evaluate(goal) -> (ok, Check | None)
module=bench/agent/check_spec.py symbols=CheckSpec,CheckSpec.applies,CheckSpec.parse,run_plan,run_specs,run_each
entry=CheckSpec(name, cmd, cheap=False, scope=())  .parse(raw) -> list[CheckSpec]; .applies(paths) | run_plan(specs, cwd, paths=None) -> (Check | None, {cmd: Check});  run_specs(...) -> combined verdict | run_each(specs, cwd, paths=None, name=None, cache=None) -> list[dict]  {name, ok, exit, | first_line, detail?} row per spec, reusing a cached green command
module=bench/agent/config.py symbols=budget,production_scope,build
entry=CONTEXT_WINDOW / MAX_OUTPUT / COMPACT_AT / OBS_CHARS / HISTORY / AUTONOMY ... constants | PROFILE / PROFILES / CODE_MODE / EFFORT / EFFORT_POLICY / INLINE_FINISH / RULES ... the measured arm | budget(steps, wall_s, output=MAX_OUTPUT) -> Budget (clamped to MAX_OUTPUT);  production_scope() -> Scope | build(client, kernel, budget, *, rules, history, obs_chars, stop, goal, goal_label, goal_stops, mode=MODE, | health=None, verifier=None, scope=None) -> StateLoop | ToolLoop | ReplLoop
module=bench/agent/custom.py symbols=CustomTool,load,verbs,specs,prelude
entry=load(path) -> list[CustomTool]  parse + validate a manifest file | verbs(tools) -> dict            {name: {function, summary, schema, mutates}} | specs(tools) -> list[dict]      provider tool specs | prelude(tools) -> str   source the child execs;  CustomTool   .name .summary .schema .source
module=bench/agent/docs.py symbols=_Page.handle_starttag,_Page.handle_endtag,_Page.handle_data,extract,DocsClient,DocsClient.fetch
entry=DOCS_TOOL                       provider tool spec | DocsClient(cache_dir=None, *, timeout=20, max_bytes=2_000_000, cache=True) | .fetch(url, max_chars=12_000) -> dict  ok/kind/url/title/headings/text/chars/truncated | extract(html, url="") -> dict   parser, exposed for tests
module=bench/agent/env.py symbols=environment,render_environment,resolve_path
entry=environment(root, task="", checks=None) -> dict   root, git_root, top, files, checks, task_paths | render_environment(env) -> str        the `[env]` card, or "" when root is missing | resolve_path(root, given) -> dict|None  None when exists; else {given, use|missing}
module=bench/agent/envelope.py symbols=page_of,normalize,parse
entry=normalize(tool, value, handle=None) -> dict;  page_of(kind, value) -> dict | None | UNITS / BULK
module=bench/agent/finish_flow.py symbols=asks_for_input,reject,finish_reply,report_cell,after_cell
entry=asks_for_input(text) -> bool; finish_reply(loop, messages, rec, content, finish_reason, finish) | -> TurnOutcome; report_cell(loop, obs, cell, record, finish, step) -> str (paired tool text) | after_cell(loop, cell, finish, step) -> None;  reject(loop, check, prefix) -> str | loop = tool_loop.ToolLoop; seam: .autonomy, .skeptic, .scope (inline_finish, clip_report), | .goal_label, .goal_stops, ._surprise, ._gated, ._goal_state(), ._skeptic_review(), .state.phase
module=bench/agent/green_gate.py symbols=GreenGate,GreenGate.invalidate,GreenGate.note_checks,GreenGate.refusal,GreenGate.suffix,exec_commands,plan_command,is_plan_command,is_foreign_gate
entry=GreenGate()  .passed / .cells / .invalidate() / .note_checks(cell) / .refusal(name) / .suffix(name) | exec_commands(args) -> list[str]; plan_command(args, specs); is_plan_command(args, specs); | is_foreign_gate(args, specs) | EXEC_REFUSAL, FOREIGN_GATE_NOTE, PASS_NOTE ... exact model-facing text
module=bench/agent/handoff.py symbols=fields,render,split
entry=render(value)   str    caller-facing text; a string value passes through unchanged | fields(value)   dict   summary/changed/gates/deferred/untested, clipped to fixed limits | split(value)    tuple  (render(value), fields(value)) for Cell.from_reply
module=bench/agent/headers.py symbols=header,in_scope,split,index,problems,render,guide
entry=python3 bench/guide.py [--query TEXT | --module PATH | --json | --check] | guide(root=None, query=None, path=None, limit=None, chars=20000, offset=0, headers=0) -> dict | index(root=None) -> list[dict];  problems(root=None);  in_scope(path) -> bool | header(path) -> (text, lines);  split(text) -> (title, sections);  render(rows, chars=20000)
module=bench/agent/limits.py symbols=Scope,Scope.allows,Scope.filter_tools,Scope.turn_tokens,Scope.clip_report,effort_for,TokenMeter,TokenMeter.add,TokenMeter.as_dict
entry=Scope(tools=None, allow_exec=True, max_calls_per_turn=8, max_turn_tokens=8192, max_report_chars=4000, effort="low", | effort_policy="fixed", inline_finish=False);  .allows(name) -> bool;  .filter_tools(specs) -> list; | .turn_tokens(budget_output) -> int;  .clip_report(text) -> str | TokenMeter().add(usage, content, tool_calls) -> dict (one per-turn row);  .as_dict() -> dict (run totals + reasoning_share) | effort_for(scope, surprise) -> str | None = fixed effort, or low only on a surprise.
module=bench/agent/llm.py symbols=LLMError,Client,Client.complete
entry=LLMError | Client(api_key, model="deepseek-flash", effort="low", base=..., timeout=180, temperature, | seed, cache=True, cache_dir=CACHE_DIR) | .complete(messages, tools=None, max_tokens=8192, effort=_UNSET) -> dict | effort overrides the client default for one call; None omits the field entirely. | -> {content, reasoning, tool_calls, usage, latency_ms, finish, local_cache}
module=bench/agent/loop.py symbols=Budget,RunResult
entry=Budget(max_steps, max_output_tokens, max_wall_s, max_total_tokens) | RunResult    what run.py, worker.py and trace.py consume | _usage_add(acc, usage)    sums provider usage incl. cache hit/miss
module=bench/agent/pages.py symbols=subsystem,render_all,main
entry=python3 -m bench.agent.pages [--write | --check] | render_all(root=None) -> dict[str, str]   relative docs path -> whole file text | subsystem(path) -> str                    owning page name, or '' when unassigned | DOCS                                      every generated file, repo-relative | PAGES                                     (subsystem, page file) in index order
module=bench/agent/records.py symbols=FinishState,FinishState.accepted,FinishState.stage,FinishState.clear,FinishState.accept,CallRecord,CallRecord.as_dict,TurnOutcome,Tally
entry=FinishState()  .stage(v) / .accept(v) / .clear() / .staged / .terminal | CallRecord(index, name, call_id)  .as_dict() | TurnOutcome(stop, final, end) | Tally()  steps, calls, errors, repeats, llm_calls
module=bench/agent/repl.py symbols=history_entry,observation,ReplLoop
entry=REPL_RULES / BARE_RULES = system prompts (BARE_RULES = minimal variant); RUN_NOTE / RUN_NOTE_MUT when run() is enabled (repl_rules.py) | observation(cell, cap) = one cell -> model-visible text (ToolLoop default);  history_entry(action) = parsed action -> transcript line | ReplLoop(client, kernel, budget, rules, stop="call"|"var", history="full"|"obs"|"drop"|"action", obs_chars, goal, | goal_label, goal_stops, wrap_up_at);  .run(...) inherited;  ._reply(...) reply -> Action -> cell -> TurnOutcome (the seam)
module=bench/agent/repl_rules.py symbols=-
entry=REPL_RULES      the full python-REPL contract (one fenced block per reply, final_answer/ANSWER) | BARE_RULES      the minimal contract (one block; set ANSWER to finish) | RUN_NOTE        appended when run() is enabled on a python-only run | RUN_NOTE_MUT    appended when exec/run() are both available
module=bench/agent/reply.py symbols=Action,parse_markup,compile_reply
entry=Action(kind, code, value, source, error, detail); kind = "code" | "answer" | "none" | compile_reply(text) -> Action        the only entry point callers need | parse_markup(text) -> Action | None  DSML/XML-ish tool calls, repaired into Python | _call_source(name, params, body)     markup call -> python source
module=bench/agent/skeptic.py symbols=Verdict,Skeptic,Skeptic.review
entry=Skeptic(client, workspace, *, max_steps=6, max_total_tokens=60_000) | .review(prompt, final, diff="", check_text="", changed=()) -> Verdict | Verdict.ok / .text / .usage   gate decision + what the parent should say
module=bench/agent/state.py symbols=PlanItem,RunState,RunState.set_plan,RunState.mark,RunState.phase_line,RunState.plan_lines,RunState.summary,plan_rows
entry=RunState(task="", env="")      .task / .env / .plan / .changed / .checks / .evidence / .open | .set_plan(items, done=None)  replace plan, optionally mark 1-based indices done | .mark(done)                  mark 1-based indices done without replacing text | .plan_lines() -> list[str]   `[plan]` lines; [] when no plan is set | .summary() -> dict           trace view (plan text + done flags) | plan_rows(items) -> list[PlanItem]   strings or {text, done} dicts -> plan items
module=bench/agent/state_card.py symbols=render_observation
entry=render_observation(cell, steps, prompt_tokens=0, check=None, output_budget=12_000, coverage=None, check_handle=None, plan=None) | -> (card text, {path: [[a, b], ...]} the card made visible);  _VERBATIM_TOOLS = typed verbs never digested.
module=bench/agent/state_fold.py symbols=cut_index,session_state_message
entry=cut_index(body, keep) -> int                  where a fold may cut body (never mid tool exchange) | session_state_message(loop, folded) -> dict   the `[session state]` message, one dict
module=bench/agent/state_loop.py symbols=StateLoop
entry=STATE_RULES = TOOL_RULES + the card contract (keep in sync with _observe); `[plan]` = durable RunState plan | from the `plan` tool; CARD_BUDGET = default per-turn card budget; verifier / last_check = the `[check]` line. | StateLoop(..., output_budget, context_budget, keep_messages); ._observe(cell, steps) -> str; ._compact(messages). | re-exported: render_observation; clip/compact_stream/clip_card/digest_card/digest/data_block; merge_ranges/gaps/ | covered/numbered_lines/numbered_lines_by_path/delivered_ranges; session_state_message/cut_index.
module=bench/agent/state_ranges.py symbols=merge_ranges,gaps,covered,numbered_lines,numbered_lines_by_path,delivered_ranges
entry=merge_ranges(ranges) -> [[a, b], ...]        overlapping/adjacent, sorted, disjoint | gaps(ranges, total) -> [[a, b], ...]          lines of 1..total the ranges miss | covered(ranges) -> int                       distinct lines in the merged ranges | numbered_lines(text); numbered_lines_by_path(text, paths)  `   12| line` numbers, split by `# path` | delivered_ranges(reads, body, digested) -> {path: [[a, b], ...]}
module=bench/agent/state_text.py symbols=clip,compact_stream,clip_card,digest_card,digest,data_block
entry=clip(text, limit) -> str;  compact_stream(text) -> str (ANSI stripped, identical runs capped at 2) | clip_card(text, budget, handle) -> (text, clipped);  digest_card(text, budget, handle, errors=False) | digest(text, budget, handle) -> str;  data_block(data) -> (shown, clipped_keys) | DATA_LINES, DATA_CHARS  per-value clip, in lines and chars
module=bench/agent/stops.py symbols=-
entry=DONE, VERIFIED, STEP_LIMIT, WALL_LIMIT, STAGED, GOAL_UNMET, TRUNCATED, BAD_ARGS, | LLM_FAILED, TOKEN_LIMIT, SKEPTIC_UNMET, GREEN, NEEDS_INPUT ... one string per stop reason | STOPS ... every stop name, in reader order
module=bench/agent/subagent.py symbols=SubagentResult,SubagentResult.ok,SubagentResult.as_text,Subagent,Subagent.run
entry=DELEGATE_TOOL                     provider tool spec the parent exposes | Subagent(client, budget, workspace, *, rules=None, tools=None) | .run(task, prompt) -> SubagentResult   answer, stop, steps, usage, error | SubagentResult.as_text()          the observation the parent receives
module=bench/agent/tool_calls.py symbols=ToolCalls
entry=ToolCalls  mixin base of tool_loop.ToolLoop | _answer_calls(messages, rec, tool_calls, finish_reason, finish, tally, seen, step) | _dispatch(fn, name, record, finish_reason, finish, tally, seen, step) -> str;  _skip_call(record)
module=bench/agent/tool_health.py symbols=ToolHealth
entry=ToolHealth  mixin base of tool_loop.ToolLoop | _checks_cell(args) -> Cell .............. `checks(name=None)`: the plan's rows, cached | _refresh_health(record, cell) ........... the cheap check after a mutation | _queue_check_obs(cell) ................. a failing check's text -> the next _OBS handle | _check_cache() -> dict; _take_extra_obs() -> list[dict]
module=bench/agent/tool_loop.py symbols=ToolLoop,ToolLoop.run
entry=ToolLoop(client, kernel, budget=, rules=, tools=, goal=, goal_label=, goal_stops=, obs_chars=, health=, verifier=, | wrap_up_at=0.8, extra_tools=, subagent=, skeptic=, autonomy=, docs=, scope=, code_mode=); | .run(task_id, prompt, trace) -> RunResult;  stop vocabulary (stops.py): DONE, VERIFIED, STEP_LIMIT, | WALL_LIMIT, STAGED, GOAL_UNMET, TRUNCATED, BAD_ARGS, LLM_FAILED, TOKEN_LIMIT, SKEPTIC_UNMET, NEEDS_INPUT | FinishState() .stage/.accept/.clear/.staged/.terminal; CallRecord(index,name,call_id); TurnOutcome(stop,final,end).
module=bench/agent/tool_specs.py symbols=model_tools
entry=model_tools(scope, kernel, tools=None, *, code_mode, extra_tools, subagent, docs) | -> (Scope, list[dict]) the scope and specs one run offers | TOOL_RULES  system prompt: one call that does the job, a typed tool for one action | PYTHON_TOOL, CODE_TOOL  the kernel cell, under the names `python` and `code` | PLAN_TOOL, TASK_TOOL  the durable plan and the durable task/phase view
module=bench/agent/tool_subtools.py symbols=ToolSubTools
entry=ToolSubTools  mixin base of tool_loop.ToolLoop | _plan(args, record) -> str;  _task(record) -> str;  _delegate(args, record, tally) -> str | _docs(args, record, tally) -> str;  _is_foreign_gate(args), _is_plan_command(args) -> bool
module=bench/agent/tool_turns.py symbols=ToolTurns
entry=ToolTurns ... mixin base of tool_loop.ToolLoop;  _open_messages(prompt) -> (messages, card) | hooks a subclass overrides: _reply, _observe, _compact, _goal_state, _on_run_start, _before_turn, _drift_count | record shape ... _turn_record, _merge_calls;  finish seam ... _finish_reply, _reject, _skeptic_review | budget nudges ... WRAP_UP_NOTE, LAST_TURN_NOTE (values in tool_loop.py), _before_turn | _push(messages, usage) = one client.complete for a summary, never raising;  _write_summary(trace, ...) = trace summary then close
module=bench/agent/tools.py symbols=-
entry=TYPE_TOOLS   typed specs, registry order | TOOL_NAMES   typed names (no "python")
module=bench/agent/trace.py symbols=Trace,Trace.write,Trace.close,render
entry=Trace(path) -> .write(record) / .close() | render(path, reasoning_chars, env_chars) -> str   err/call/get/text lines, then the | `== stop=... steps=...` summary
module=bench/agent/verbs.py symbols=-
entry=VERBS          name -> {function, summary, schema, mutates, typed?, requires_run?};  TOOL_NAMES = typed tool names | (no `python`: that is the escape hatch); look up VERBS[name] directly, no accessor layer. | verbs_read.py guide/skill/map/read/session_diff/checks/serve/revert_check/status;  verbs_search.py search/recall/page/outline; | verbs_mutate.py edit/splice/write/delete/mkdir/apply;  verbs_exec.py exec/run/git.
module=bench/agent/verbs_exec.py symbols=-
entry=VERBS_EXEC   three registry entries: function, summary, mutates, typed, schema
module=bench/agent/verbs_mutate.py symbols=-
entry=VERBS_MUTATE   six registry entries: function, summary, mutates=True, schema
module=bench/agent/verbs_read.py symbols=-
entry=VERBS_READ   nine registry entries: function, summary, mutates=False, schema
module=bench/agent/verbs_search.py symbols=-
entry=VERBS_SEARCH   four registry entries: function, summary, mutates=False, schema
