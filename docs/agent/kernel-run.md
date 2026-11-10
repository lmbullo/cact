# kernel-run
generated_by=bench/agent/pages.py
source_headers=bench/agent/headers.py
source_symbols=bench/index.py
subsystem=kernel-run
page=docs/agent/kernel-run.md
modules=8
module=bench/agent/kernel/run_prelude_exec.py symbols=-
entry=_EXEC   source text; inside: exec_argv
module=bench/agent/kernel/run_prelude_gate.py symbols=-
entry=_GATE   source text; inside: _segments, _TMP_ROOTS, _abs_outside, _mutator_targets_outside, _writes_stdout, | _mutating_command, _norm_command, _spawn_argv, _spawn_command, _gate_check, _audit_spawn
module=bench/agent/kernel/run_prelude_git.py symbols=-
entry=_GIT   source text; inside: _git_page, git_tool
module=bench/agent/kernel/run_prelude_notes.py symbols=-
entry=_NOTES   source text; inside: _plan_program, _spec_names, _check_note, _status_note, _typed_dupe
module=bench/agent/kernel/run_prelude_serve.py symbols=-
entry=_SERVE   source text; inside: _SERVE_ACTS, _SERVE_TIMEOUT, _serve_state, _serve_get, _serve_read_json, | _serve_alive, _serve_ppid, _serve_hosts_worker, _serve_facts, _serve_text, _serve_result, | _serve_refuse, _serve_log_tail, _serve_which_bun
module=bench/agent/kernel/run_prelude_serve_ctl.py symbols=-
entry=_SERVE_CTL   source text; inside: _serve_start, _serve_stop, serve
module=bench/agent/kernel/run_prelude_setup.py symbols=-
entry=_SETUP   source text; inside: _proc_start, _journal_orphan, _SpawnBlocked, _RUN_SHELL_MUTATORS, | _RUN_GIT_MUTATORS, _HINT, _VERB_DUPE, _DUPE_OPAQUE, _CHECK_SPECS, _SHELL_SPLIT
module=bench/agent/kernel/run_prelude_shell.py symbols=-
entry=_SHELL   source text; inside: _run_shell, _exec_shell_string
