#!/bin/sh
# live.sh: one real end-to-end task against the real model, in a throwaway workspace.
#
# The contract: a task that cannot write a file and pass its own check plan is a broken engine.
# This is the one gate that costs money, so it is one small task with the cheap model.
set -eu
: "${DEEPSEEK_API_KEY:?set DEEPSEEK_API_KEY to run the live check}"
BIN=${BIN:-target/release/cact}
MODEL=${MODEL:-deepseek-flash}

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
git init -q "$work"
printf '# demo\n' > "$work/README.md"

printf '%s\n' "Create fib.py with a memoized fib(n) and fib_test.py with pytest tests for fib(0), fib(1) and fib(10). Finish with final_answer once the check plan passes." > "$work/prompt.txt"

"$BIN" run \
  --workspace "$work" \
  --prompt-file "$work/prompt.txt" \
  --model "$MODEL" \
  --max-steps 20 \
  --max-wall 600 \
  --checks '[{"name":"test","cmd":"python3 -m pytest -q"}]'
code=$?

if [ ! -f "$work/fib.py" ]; then
  echo "live: the worker did not create fib.py" >&2
  exit 1
fi
(cd "$work" && python3 -m pytest -q >/dev/null)
echo "live: ok (exit $code, fib.py present, pytest green)"
