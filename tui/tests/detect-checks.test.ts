/**
 * The verification plan: explicit config wins, the package manager follows the lockfile, a test script beats a tests directory, and a build-only project still gets a goal check.
 *
 * WHAT IT IS
 *  `detectChecks(cwd, env)` is called once per case on a temporary workspace written for that
 *  case: a `package.json` with or without scripts, a `package-lock.json`/`bun.lock`, a `tests/`
 *  directory, or nothing at all. The plan it returns is the same plan server.ts hands to the
 *  worker, so these cases are the contract for what gets verified and what is `cheap`.
 *
 * ENTRY POINTS
 *  detectChecks from ../src/detect-checks; env is the second argument, never process.env
 *  workspace() ... a fresh mkdtemp root; afterAll() removes every root it made
 *  the plan is `[{name, cmd, cheap, scope}]`
 *
 * INVARIANTS
 *  - Nothing under `tui/` is written: every case owns a temp directory that afterAll deletes.
 *  - `toEqual` on the whole plan means a new field or a changed default fails here on purpose;
 *    update the expectations, not the detector, when the plan shape is the intended change.
 *  - An empty `CODE_TUI_CHECK` must yield `[]`: verification disabled is a real setting, not a
 *    fallback to detection.
 *
 * WHERE TO CHANGE
 *  A detection rule ........ src/detect-checks.ts, then a case here
 *  The plan shape ........... every `toEqual` in this file plus src/types.ts `CheckSpec`
 */
import { afterAll, expect, test } from "bun:test";
import { mkdtempSync, mkdirSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { detectChecks } from "../src/detect-checks";

const roots: string[] = [];
function workspace(): string {
  const root = mkdtempSync(join(tmpdir(), "detect-checks-"));
  roots.push(root);
  return root;
}
afterAll(() => {
  for (const root of roots) rmSync(root, { recursive: true, force: true });
});

test("CODE_TUI_CHECKS is parsed verbatim", () => {
  const plan = detectChecks(workspace(), {
    CODE_TUI_CHECKS: JSON.stringify([{ name: "lint", cmd: "npm run lint", cheap: true, scope: ["src"] }]),
  });
  expect(plan).toEqual([{ name: "lint", cmd: "npm run lint", cheap: true, scope: ["src"] }]);
});

test("an empty CODE_TUI_CHECK disables verification", () => {
  expect(detectChecks(workspace(), { CODE_TUI_CHECK: "" })).toEqual([]);
});

test("a package-lock project runs its scripts through npm", () => {
  const root = workspace();
  writeFileSync(join(root, "package.json"), JSON.stringify({ scripts: { typecheck: "tsc --noEmit" } }));
  writeFileSync(join(root, "package-lock.json"), "{}");
  expect(detectChecks(root, {})).toEqual([
    { name: "typecheck", cmd: "npm run typecheck", cheap: true, scope: [] },
  ]);
});

test("a lint script and a fixable format check join the cheap plan", () => {
  const root = workspace();
  writeFileSync(join(root, "package.json"),
    JSON.stringify({ scripts: { lint: "eslint .", "format:check": "prettier --check ." } }));
  writeFileSync(join(root, "bun.lock"), "");
  expect(detectChecks(root, {})).toEqual([
    { name: "lint", cmd: "bun run lint", cheap: true, scope: [] },
    { name: "format", cmd: "bun run format:check", cheap: true, scope: [] },
  ]);
});

test("a ruff.toml root adds a cheap lint check", () => {
  const root = workspace();
  writeFileSync(join(root, "ruff.toml"), "line-length = 100");
  expect(detectChecks(root, {})).toEqual([
    { name: "lint", cmd: "python3 -m ruff check .", cheap: true, scope: [] },
  ]);
});

test("a bun project with tests/ but no test script uses bun test", () => {
  const root = workspace();
  writeFileSync(join(root, "package.json"), JSON.stringify({ scripts: { dev: "vite" } }));
  writeFileSync(join(root, "bun.lock"), "");
  mkdirSync(join(root, "tests"));
  expect(detectChecks(root, {})).toEqual([
    { name: "test", cmd: "bun test", cheap: false, scope: [] },
  ]);
});

test("a build-only project gets build as the goal", () => {
  const root = workspace();
  writeFileSync(join(root, "package.json"), JSON.stringify({ scripts: { build: "vite build" } }));
  writeFileSync(join(root, "package-lock.json"), "{}");
  expect(detectChecks(root, {})).toEqual([
    { name: "build", cmd: "npm run build", cheap: false, scope: [] },
  ]);
});

test("a Cargo.toml root gets format, clippy and test from cargo", () => {
  const root = workspace();
  writeFileSync(join(root, "Cargo.toml"), "[package]\nname = 'demo'\nversion = '0.1.0'\n");
  expect(detectChecks(root, {})).toEqual([
    { name: "format", cmd: "cargo fmt --check", cheap: true, scope: [] },
    { name: "lint", cmd: "cargo clippy --workspace --all-targets -- -D warnings", cheap: true, scope: [] },
    { name: "test", cmd: "cargo test --workspace", cheap: false, scope: [] },
  ]);
});

test("a Rust workspace keeps its cargo plan alongside a package script plan", () => {
  const root = workspace();
  writeFileSync(join(root, "Cargo.toml"), "[workspace]\nmembers = ['crates/app']\n");
  writeFileSync(join(root, "package.json"), JSON.stringify({ scripts: { typecheck: "tsc --noEmit" } }));
  writeFileSync(join(root, "bun.lock"), "");
  expect(detectChecks(root, {})).toEqual([
    { name: "format", cmd: "cargo fmt --check", cheap: true, scope: [] },
    { name: "lint", cmd: "cargo clippy --workspace --all-targets -- -D warnings", cheap: true, scope: [] },
    { name: "test", cmd: "cargo test --workspace", cheap: false, scope: [] },
    { name: "typecheck", cmd: "bun run typecheck", cheap: true, scope: [] },
  ]);
});

test("a tui subdirectory is scoped and prefixed", () => {
  const root = workspace();
  mkdirSync(join(root, "tui"));
  writeFileSync(join(root, "tui", "package.json"), JSON.stringify({ scripts: { typecheck: "tsc --noEmit" } }));
  writeFileSync(join(root, "tui", "bun.lock"), "");
  expect(detectChecks(root, {})).toEqual([
    { name: "typecheck", cmd: "cd tui && bun run typecheck", cheap: true, scope: ["tui"] },
  ]);
});
