/**
 * The verification plan: explicit config wins, the package manager follows the lockfile, a
 * test script beats a tests directory, and a build-only project still gets a goal check.
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

test("bench/smoke.py is a cheap root check", () => {
  const root = workspace();
  mkdirSync(join(root, "bench"));
  writeFileSync(join(root, "bench", "smoke.py"), "");
  expect(detectChecks(root, {})).toEqual([
    { name: "smoke", cmd: "python3 bench/smoke.py", cheap: true, scope: ["bench"] },
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
