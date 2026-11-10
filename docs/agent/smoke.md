# smoke
generated_by=bench/agent/pages.py
source_headers=bench/agent/headers.py
source_symbols=bench/index.py
subsystem=smoke
page=docs/agent/smoke.md
modules=20
module=bench/smoke_checks/__init__.py symbols=-
entry=python3 bench/smoke.py  the only entry, it decides the order;  <module>.run(check, ...)  one | group; `check` is bench.smoke_support.check
module=bench/smoke_checks/docs.py symbols=run
entry=run(check), DOCS                  group entry after `index`; the documents that are checked | _agent_docs()                     agent-page drift, coverage and line-budget report | _references(text)                 backticked spans that look like a repository path | _resolves(ref, doc_dir, paths)    whether a reference names something in the tree | _paragraphs(text), _copies(docs)  comparison unit; paragraph pairs two documents share
module=bench/smoke_checks/env.py symbols=run
entry=run(check)   called by bench/smoke.py before the kernel groups
module=bench/smoke_checks/evals.py symbols=run
entry=run(check)   called by bench/smoke.py::main
module=bench/smoke_checks/files.py symbols=run
entry=run(check, root, kernel)   called by bench/smoke.py::main with the shared kernel
module=bench/smoke_checks/gates.py symbols=run
entry=run(check, root, kernel)   called by bench/smoke.py::main after verbs
module=bench/smoke_checks/handoff.py symbols=run
entry=run(check)   called by bench/smoke.py::main after gates
module=bench/smoke_checks/helpers.py symbols=run
entry=run(check)   called by bench/smoke.py::main after suites
module=bench/smoke_checks/index.py symbols=run
entry=run(check)                    called by bench/smoke.py::main after helpers | _declared_names()             every name the Python source declares, embedded sources included | _entry_point_gaps(rows, names)  (module, callable) pairs a header names but nothing declares
module=bench/smoke_checks/limits.py symbols=run
entry=run(check)   called by bench/smoke.py after the turn-machine group
module=bench/smoke_checks/plans.py symbols=run
entry=run(check) -> Path   called by bench/smoke.py::main; returns the shared temp workspace
module=bench/smoke_checks/protocol.py symbols=run
entry=run(check, root)   called by bench/smoke.py::main after plans (it needs the temp root)
module=bench/smoke_checks/repl.py symbols=run
entry=run(check)   called last by bench/smoke.py::main
module=bench/smoke_checks/revert.py symbols=run
entry=run(check)   called by bench/smoke.py::main after gates; builds and drops its own repo
module=bench/smoke_checks/serve.py symbols=run
entry=run(check)   called by bench/smoke.py::main after revert; builds and drops its own fixtures
module=bench/smoke_checks/structure.py symbols=run
entry=run(check)   called by bench/smoke.py after the env/index groups
module=bench/smoke_checks/style.py symbols=run
entry=run(check) -> None   called by bench/smoke.py::main after `index`
module=bench/smoke_checks/turns.py symbols=run
entry=run(check)   called by bench/smoke.py::main after the kernel groups
module=bench/smoke_checks/verbs.py symbols=run
entry=run(check, root, kernel)   called by bench/smoke.py::main after files
module=bench/smoke_checks/wiring.py symbols=run
entry=run(check) -> None   called by bench/smoke.py::main after `repl`
