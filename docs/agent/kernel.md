# kernel
generated_by=bench/agent/pages.py
source_headers=bench/agent/headers.py
source_symbols=bench/index.py
subsystem=kernel
page=docs/agent/kernel.md
modules=40
module=bench/agent/kernel/__init__.py symbols=-
entry=from .kernel import Kernel, Cell, PROTOCOL      # surface for the loops and run.py | Kernel(root, timeout=15.0, writable=False, prelude="off"|"all"|"mut", | allow_run=False, allow_shell=None, supervise=True) | .start() / .run(code, check_cache=None) / .call(tool, args, check_cache=None) / .stop() | re-exported: PROTOCOL, MARKER, MAX_CODE, MAX_OUTPUT, CELL_TIMEOUT; DRIVER, PRELUDE, RUN_PRELUDE, HEADERS_SOURCE; | CellRequest, CellReply, ExtendNotice, Cell.
module=bench/agent/kernel/driver.py symbols=-
entry=DRIVER   the complete child program: _BOOT + _ARGS + _LOOP + _CELL, protocol substituted | the child wire: one JSON request per line in, one @@K@@ JSON line out.
module=bench/agent/kernel/driver_args.py symbols=-
entry=_ARGS   source text; inside: _USE_FALLBACK, _USE_BY_CODE, _error_shape, _BadArg, _scalar, | _coerce, _coerce_args
module=bench/agent/kernel/driver_boot.py symbols=-
entry=_BOOT   source text; inside: _CellTimeout, _on_alarm, _extend, _arm, _disarm, _Result, Final, | final_answer, emit
module=bench/agent/kernel/driver_cell.py symbols=-
entry=_CELL   source text; inside: the except arms, changed/delta computation, obs append, emit
module=bench/agent/kernel/driver_loop.py symbols=-
entry=_LOOP   source text; inside: the top-level `for line in sys.stdin:` dispatch
module=bench/agent/kernel/prelude.py symbols=-
entry=PRELUDE = assembled child source (KERNEL_PRELUDE), five fragments in order: prelude_setup.py (namespace, read ledgers, | path matching), prelude_journal.py (_commit, write-through open(), snapshot/diff/undo/changes), prelude_index.py (_walk, | map, outline), prelude_read.py (read, grep/search, recall), prelude_verbs.py (write/delete/mkdir/edit/splice/apply/commit). | HEADERS_SOURCE = headers.py as text (KERNEL_HEADERS), exec'd into _HEADERS where `bench` is not importable; _commit = the one journal entry.
module=bench/agent/kernel/prelude_commit.py symbols=-
entry=_COMMIT   source text; inside: commit
module=bench/agent/kernel/prelude_diff.py symbols=-
entry=_DIFF   source text; inside: session_diff, diff_tool
module=bench/agent/kernel/prelude_edit.py symbols=-
entry=_EDIT   source text; inside: _closest_lines, _replace_text, _edit_context, edit
module=bench/agent/kernel/prelude_index.py symbols=-
entry=_INDEX   the complete fragment: _SCAN + _OUTLINE_PARSE + _OUTLINE | map(...) / outline(...) live in the fragments above; importing this module does not define them.
module=bench/agent/kernel/prelude_journal.py symbols=-
entry=_JOURNAL   the complete fragment (core + snapshot + diff + status + undo);  _commit   the one | journal entry point behind every mutation.
module=bench/agent/kernel/prelude_journal_core.py symbols=-
entry=_JOURNAL_CORE   source text; inside: _HINT, _Writer, open, _resolve, _read_text, _digest, | _commit
module=bench/agent/kernel/prelude_lines.py symbols=-
entry=_LINES   source text; inside: edit_many, splice
module=bench/agent/kernel/prelude_mutate.py symbols=-
entry=_MUTATE   source text; inside: write, delete, mkdir
module=bench/agent/kernel/prelude_outline.py symbols=-
entry=_OUTLINE   source text; not imported by the kernel. Inside: outline
module=bench/agent/kernel/prelude_outline_parse.py symbols=-
entry=_OUTLINE_PARSE   source text; inside: _outline_doc, _outline_html
module=bench/agent/kernel/prelude_patch.py symbols=-
entry=_PATCH   source text; inside: _parse_patch, _patch_path, _hunk_old, _hunk_new, apply
module=bench/agent/kernel/prelude_read.py symbols=-
entry=_READ   helpers + read + results + search + recall + skill. | read/grep/search/recall/page/skill live in the fragments above, not as Python names here.
module=bench/agent/kernel/prelude_read_file.py symbols=-
entry=_READ_FILE   source text; inside: read
module=bench/agent/kernel/prelude_read_helpers.py symbols=-
entry=_READ_HELPERS   source text; inside: _section_bounds, _range_count, | _section_search_result, _coerce_paths, _int_arg
module=bench/agent/kernel/prelude_recall.py symbols=-
entry=_RECALL   source text; inside: _recall_hits, recall, recall_tool, page
module=bench/agent/kernel/prelude_revert.py symbols=-
entry=_REVERT   source text; inside: _revert_bytes, _revert_digest, _revert_git, _revert_dirty, | _revert_run, _revert_output
module=bench/agent/kernel/prelude_revert_check.py symbols=-
entry=_REVERT_CHECK   source text; inside: revert_check
module=bench/agent/kernel/prelude_scan.py symbols=-
entry=_SCAN   source text; not imported by the kernel. Inside: _walk, map
module=bench/agent/kernel/prelude_search.py symbols=-
entry=_SEARCH   source text; inside: grep, search
module=bench/agent/kernel/prelude_search_result.py symbols=-
entry=_SEARCH_RESULT   source text; inside: _count_result, _grep_result
module=bench/agent/kernel/prelude_setup.py symbols=-
entry=guide(query, path, limit, chars, offset, headers)   the one verb the header index adds | _fail(kind, code, message, use, **extra)            the one error-dict builder | _partial_failure(results)                           aggregate error for a bad-path batch | cell namespace: _ROOT, _READS_CELL, _READS_SESSION, _READ_MAX_CHARS, _READ_MAX_LINES, | _MUT, _JOURNAL, _SNAP, _JSEQ, _CELL_CHANGED, _SKIP_DIRS, _IGNORE
module=bench/agent/kernel/prelude_skill.py symbols=-
entry=_SKILL   source text; inside: skill
module=bench/agent/kernel/prelude_snapshot.py symbols=-
entry=_SNAPSHOT   source text; inside: _BASE, _BASE_READY, _LAST_STATE, _SNAP_PARTIAL, _snap_files, | _snapshot, _tree_state, _row, _changed, _delta
module=bench/agent/kernel/prelude_status.py symbols=-
entry=_STATUS   source text; inside: _not_a_repo, status
module=bench/agent/kernel/prelude_undo.py symbols=-
entry=_UNDO   source text; inside: changes, diff, undo
module=bench/agent/kernel/prelude_verbs.py symbols=-
entry=_VERBS   intro + mutate + edit + lines + patch + commit + revert + revert_check, then _snapshot().
module=bench/agent/kernel/prelude_verbs_intro.py symbols=-
entry=_VERBS_INTRO   source text; inside: verbs, help, checks, _ESCAPE_OK_EXT, _literal_newline_note
module=bench/agent/kernel/process.py symbols=Kernel
entry=Kernel(root, timeout=15.0, writable=False, prelude="off"|"all"|"mut", allow_run=False, | allow_shell=None, supervise=True, extra_verbs=None, extra_prelude="", | check_commands=None, env=None) | .start() / .run(code, check_cache=None, extra_obs=None) / .call(tool, args, ...) / .stop() | process_spawn.py SpawnMixin: _argv, start, _stop, stop, _kill_group, _restart | process_reap.py  ReapMixin: _start_id, _reap_orphans | process_cell.py  CellMixin: _protocol_error, run, call, _execute
module=bench/agent/kernel/process_cell.py symbols=CellMixin,CellMixin.run,CellMixin.call
entry=CellMixin.run(code, check_cache=None, extra_obs=None) -> Cell | CellMixin.call(tool, args=None, check_cache=None, extra_obs=None) -> Cell | CellMixin._execute(request)  wire write, @@K@@/ExtendNotice read, deadline, protocol check
module=bench/agent/kernel/process_reap.py symbols=ReapMixin
entry=ReapMixin._start_id(pid) / ._reap_orphans()
module=bench/agent/kernel/process_spawn.py symbols=SpawnMixin,SpawnMixin.start,SpawnMixin.stop
entry=SpawnMixin._argv / .start / ._stop / .stop / ._kill_group / ._restart | used by Kernel.start()/stop(); `_stop` reaps orphan groups through process_reap.ReapMixin.
module=bench/agent/kernel/protocol.py symbols=CellRequest,CellRequest.to_wire,CellReply,CellReply.from_wire,ExtendNotice,ExtendNotice.from_wire,Cell,Cell.failure,Cell.from_reply
entry=CellRequest(...).to_wire() -> dict   the one JSON line to the child; extra_obs prepends parent-side _OBS entries | CellReply.from_wire(dict)   parse a reply; version checked by process.Kernel._protocol_error on arrival | ExtendNotice.from_wire(dict)   deadline extension | Cell.failure(message, ...)   the one restart/error constructor | Cell.from_reply(reply, duration_ms=...)   the one success constructor; normalizes a typed result into Cell.envelope
module=bench/agent/kernel/run_prelude.py symbols=-
entry=RUN_PRELUDE = _SETUP + _NOTES + _GATE + _SHELL + _EXEC + _GIT + _SERVE + _SERVE_CTL (KERNEL_RUN_PRELUDE), present only | when allow_run is on; namespace: exec_argv(), git_tool(), run() (KERNEL_SHELL=1 only), serve(); gates: _mutating_command, | _HINT, _VERB_DUPE, _audit_spawn, _SpawnBlocked.
