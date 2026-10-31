"""Trace: the JSONL run journal, and a human/agent-readable rendering of it.

WHAT IT IS
  One JSON object per line: one record per turn, plus a final `{"summary": true, ...}` record.
  `bench/traces/<task>.<mode>.jsonl` is written by this class from every loop.

ENTRY POINTS
  Trace(path) -> .write(record) / .close()
  render(path, reasoning_chars, env_chars) -> str   `err`/`call`/`get`/`text` lines, then the
                                                    `== stop=... steps=...` summary

INVARIANTS
  - The file is opened with "w": a trace is a new run, never appended to. `bench/traces/` is
    committed evidence — add new runs, never rewrite old ones.
  - Records are written and flushed per turn, so a killed run still leaves readable evidence.
  - `worker.py` substitutes its own `EventTrace` with the same `write/close` interface; keep
    that duck type intact.

WHERE TO CHANGE
  Record shape .................. the loops (they build the dict); this file only writes it
  Rendering for humans/agents ... render()
"""
from __future__ import annotations

import json
from pathlib import Path

class Trace:
    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._fh = open(self.path, "w", encoding="utf-8")

    def write(self, record: dict) -> None:
        self._fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        self._fh.flush()

    def close(self) -> None:
        self._fh.close()

def _clip(text: str, n: int) -> str:
    text = (text or "").replace("\n", "\\n")
    return text if len(text) <= n else text[:n] + f"…(+{len(text) - n})"

def render(path: str | Path, reasoning_chars: int = 420, env_chars: int = 380) -> str:
    out: list[str] = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        rec = json.loads(line)
        if rec.get("summary"):
            out.append(
                f"  == stop={rec['stop']} steps={rec['steps']} llm={rec.get('llm_calls', '?')} cells={rec['calls']} "
                f"errors={rec['errors']} repeats={rec['repeats']} "
                f"tokens={rec['usage'].get('total_tokens', 0)} wall={rec['wall_ms']}ms"
            )
            out.append(f"  == final: {_clip(rec.get('final', ''), 400)}")
            continue

        out.append(
            f"  turn {rec['turn']:>2}  {rec['latency_ms']:>5}ms  finish={rec.get('finish')}"
        )
        if rec.get("reasoning"):
            out.append(f"    think: {_clip(rec['reasoning'], reasoning_chars)}")
        if rec.get("code") is not None:
            out.append(f"    code : {_clip(rec['code'], env_chars)}")
            obs = rec.get("observation") or {}
            bits = [f"exit={obs.get('exit')}", f"{obs.get('duration_ms')}ms"]
            if obs.get("restarted"):
                bits.append("RESTARTED")
            if obs.get("result") is not None:
                bits.append(f"result={_clip(str(obs['result']), 120)}")
            out.append("    obs  : " + " ".join(bits))
            if obs.get("stdout"):
                out.append(f"    out  : {_clip(obs['stdout'], env_chars)}")
            if obs.get("stderr"):
                out.append(f"    err  : {_clip(obs['stderr'], env_chars)}")
        if rec.get("content"):
            out.append(f"    say  : {_clip(rec['content'], reasoning_chars)}")
        for call, result in zip(rec.get("tool_calls") or [], rec.get("results") or []):
            fn = call.get("function") or {}
            out.append(f"    call : {fn.get('name')}({_clip(fn.get('arguments', ''), 200)})")
            s = result.get("summary") or {}
            if s.get("ok"):
                bits = [f"kind={s.get('kind')}"]
                if s.get("path"):
                    bits.append(f"path={s['path']}")
                if s.get("shown"):
                    bits.append(f"shown={s['shown']}")
                if s.get("total_lines") is not None:
                    bits.append(f"total_lines={s['total_lines']}")
                if s.get("count") is not None:
                    bits.append(f"entries={s['count']}")
                if s.get("lines_matched") is not None:
                    bits.append(f"lines={s['lines_matched']}")
                if s.get("expression") is not None:
                    bits.append(f"expr={s['expression']} -> {s.get('value')}")
                if s.get("occurrences") is not None:
                    bits.append(f"occ={s['occurrences']}")
                if s.get("truncated"):
                    bits.append("truncated=True")
                out.append("    get  : " + " ".join(bits))
            else:
                out.append(
                    f"    ERR  : {s.get('code')}: {_clip(str(s.get('message')), 200)}"
                    f" | hint: {_clip(str(s.get('hint')), 200)}"
                )
            if s.get("note"):
                out.append(f"    note : {_clip(str(s['note']), 240)}")
            env = result.get("env") or {}
            if env.get("kind") == "file" and env.get("text"):
                out.append(f"    text : {_clip(env['text'], env_chars)}")
            if env.get("kind") == "dir":
                names = [f"{e['name']}{'/' if e['type'] == 'dir' else ''}" for e in env.get("entries", [])]
                out.append(f"    dir  : {_clip(', '.join(names), env_chars)}")
    return "\n".join(out)
