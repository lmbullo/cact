# evals
generated_by=bench/agent/pages.py
source_headers=bench/agent/headers.py
source_symbols=bench/index.py
subsystem=evals
page=docs/agent/evals.md
modules=8
module=bench/evals/__init__.py symbols=-
entry=python3 bench/eval.py                 offline loop contracts (the gate) | python3 bench/eval.py --live          live agent failures (needs a key) | from bench.evals.runner import REGISTRY, run | LOOP_CASES / LIVE_CASES               the two case lists
module=bench/evals/fixtures.py symbols=mutation_tree,hidden_spec_tree,bug_hunt_tree,long_tree,docs_tree,quality_tree,skill_tree
entry=mutation_tree(root)       one failing function + visible test + SPEC | hidden_spec_tree(root)    visible tests green, SPEC requires more behavior | bug_hunt_tree(root)       five latent bugs behind a green visible suite | long_tree(root, files=60) one fact plus many distractors, for compaction | skill_tree(root)          .agent/skills/release.md and a project that needs it
module=bench/evals/live.py symbols=run_live,case_spec,case_long_context,case_skill,case_skeptic,case_delegate,case_mutation,case_worker,case_docs,case_quality
entry=LIVE_CASES   the list runner registers; each is a types.Case with kind="live" | run_live(ws, client, prompt, **kwargs) -> (RunResult, ToolLoop)   the shared constructor
module=bench/evals/loops.py symbols=run_loop,case_staged_finish,case_goal_reject,case_skipped_pair,case_bad_args,case_repeat_read,case_unknown_tool,case_python_write_journal,case_compaction,case_custom_tool,case_skill,case_docs,FakeSubagent,FakeSubagent.run,case_delegate,case_subagent_real,FakeSkeptic,FakeSkeptic.review,case_skeptic_reject,case_autonomy,case_token_limit
entry=LOOP_CASES   the list runner registers; each is a types.Case | run_loop(ws, client, **kwargs) -> (RunResult, ToolLoop, Kernel)   the shared constructor
module=bench/evals/model.py symbols=tool,say,ScriptedClient,ScriptedClient.complete
entry=ScriptedClient(replies, *, responder=None, usage=None) | .complete(messages, tools=None, max_tokens=8192, effort=None) -> provider-shaped dict | .requests     every (messages, tools) the loop sent, for assertions | tool(name, arguments=None, **kwargs), say(text)   one tool_calls entry / one no-tool reply
module=bench/evals/oracle.py symbols=run_python,calc_add,calc_spec,ledger,slugify_spec,ruff_clean,answer_eq,has_text
entry=calc_spec(root) -> (ok, detail)          mul/clamp, which the visible test does not cover | calc_add(root) -> (ok, detail)           the mutation control | ledger(root) -> (ok, detail)             five hidden invariants | answer_eq(answer, expected) -> (ok, detail) | has_text(root, rel, needle) -> (ok, detail) | run_python(root, code) -> (ok, detail)   the one subprocess primitive
module=bench/evals/runner.py symbols=Report,Report.passed,Report.failed,Report.usage,Report.tokens_per_pass,Report.by_failure,Report.text,Report.write,run,main
entry=REGISTRY                         id -> Case (loop + live) | run(ids=None, live=False, *, api_key="", model="deepseek-flash", effort="low") -> Report | Report.rows/.passed/.failed/.usage/.by_failure, Report.text()/.write(tag="")  view, persist | main(argv=None) -> int           the CLI behind bench/eval.py
module=bench/evals/types.py symbols=Context,CaseResult,Case
entry=Case(id, failure, title, kind, fn, tags=()) | CaseResult(ok, detail, stop="", steps=0, usage=None, final="", errors=0, meta=None) | Context(workspace, client_factory, options)
