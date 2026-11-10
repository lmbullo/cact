# bench-core
generated_by=bench/agent/pages.py
source_headers=bench/agent/headers.py
source_symbols=bench/index.py
subsystem=bench-core
page=docs/agent/bench-core.md
modules=10
module=bench/__init__.py symbols=-
entry=python3 bench/eval.py [--live]       the evals: loop contracts offline, failures live | python3 bench/live.py ...            one end-to-end task through tui/python/worker.py | python3 bench/smoke.py               offline contract check | bench/evals/runner.py                registry, execution modes, result rows; loops.py / live.py | the two case lists | bench/guide.py                       the contract index: which module owns what
module=bench/eval.py symbols=-
entry=python3 bench/eval.py --list                 every case, its kind and failure tag | python3 bench/eval.py                        the offline loop pass | python3 bench/eval.py --tasks L04_bad_args   one case | python3 bench/eval.py --live                 the live failure-mode suite | python3 -m bench.eval ...                    package mode
module=bench/guide.py symbols=main
entry=python3 bench/guide.py                     one line per module (the index) | python3 bench/guide.py --query checks      ranked modules + their headers | python3 bench/guide.py --module PATH       one module's full header | python3 bench/guide.py --json [--query Q]  the same data as JSON | python3 bench/guide.py --limit N           cap the rows for --query (default 6) | python3 bench/guide.py --check             exit 1 on any header gap | main(argv=None) -> exit code; the flags are mutually exclusive except --json
module=bench/index.py symbols=build,symbols,main
entry=python3 bench/index.py [--root .] [--write | --check | --json] | build(root=".") -> dict          {"root": ..., "modules": [...]} | symbols(path, source) -> list[dict]   public classes/functions/methods, with ranges
module=bench/live.py symbols=load_key,main
entry=python3 bench/live.py --base bench/archive/results/ws/tui-base --tag r4 --prompt "..." --check "bun run typecheck" | [--checks '[{"name":"smoke","cmd":"...","cheap":true}]'] [--legacy r1|r3|both] [--sandbox local|daytona] | load_key() = DEEPSEEK_API_KEY, else ~/.config/code-tui/{env,.env};  _daytona(args, base) = the sandbox path; | _summary(args, events, final, wall, run, check) = the one summary both paths write.
module=bench/remote.py symbols=RemoteError,api_key,status,Sandbox,Sandbox.id,Sandbox.code,Sandbox.run,Sandbox.upload,Sandbox.download,Sandbox.upload_tree,Sandbox.download_tree,Sandbox.delete,create,create_snapshot,main
entry=status() -> {sdk, key, hint} | create(image=None, snapshot=None, env=None, auto_stop=30, auto_delete=120) -> Sandbox | create_snapshot(name=None, base_image=None, on_logs=None) -> {name, id, created} | Sandbox.code(src) / .run(argv_or_str, cwd, env, timeout) / .upload(bytes, path) / .download(path) | / .upload_tree(src, dest, exclude) / .download_tree(src, dest, exclude) / .delete() / .id | python3 -m bench.remote --check (SDK + key), --self-test (create/run/transfer/delete; spends)
module=bench/smoke.py symbols=main
entry=python3 bench/smoke.py   the only entry point; main() returns the exit code | bench/smoke_support.py   check(), summary(), workspace(), kernel_for() and the fakes | bench/smoke_checks/*.py  one run(...) per area; the runner decides the order
module=bench/smoke_support.py symbols=check,summary,workspace,kernel_for,FakeClient,FakeClient.complete,FakeKernel,FakeKernel.run,FakeKernel.call
entry=check(name, ok, detail); FAILED; workspace(); kernel_for(root, **kw)  started Kernel always stopped | _reply/_call/_cell        one scripted model reply, tool call, or kernel Cell | FakeClient, FakeKernel    the offline loop driver: scripted replies, scripted cells | _run_loop(...)            ToolLoop over the two fakes -> (result, client, kernel) | _paired_messages(...)     the assistant tool ids and the tool messages answering them
module=bench/snapshot.py symbols=manifest,clone,changed,main
entry=python3 -m bench.snapshot freeze SRC DST     clone, then write DST.manifest.json | python3 -m bench.snapshot clone  SRC DST     copy only (no manifest) | python3 -m bench.snapshot diff   DST         added/modified/deleted vs DST.manifest.json | manifest(root, write=True) -> dict           {"root": ..., "files": {rel: sha256[:16]}} | clone(src, dst)  copy, ignoring generated dirs, symlinking node_modules;  changed(run) -> {added, modified, deleted}
module=bench/task_journal.py symbols=load,summarize,recent,compare,main
entry=python3 bench/task_journal.py <state-dir> <task-id>   |   --compare <state-dir> <a> <b>   |   --recent [N] | load(state_dir, task_id) -> (task, turns);  summarize(state_dir, task_id) -> dict;  compare(state_dir, task_ids) -> int; | recent(state_root, limit) -> list[dict]
