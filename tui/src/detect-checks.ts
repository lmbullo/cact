/**
 * detect-checks.ts: the verification plan for one workspace.
 *
 * WHAT IT IS
 * detectChecks(root, env) turns a project tree into the ordered CheckSpec[] the worker runs: the
 * goal (whole plan) and the cheap health subset (after a mutating cell).
 *
 * ENTRY POINTS
 * detectChecks(root, env = process.env) -> CheckSpec[]
 *
 * INVARIANTS
 * explicit configuration wins: CODE_TUI_CHECKS (JSON list), then CODE_TUI_CHECK (one command);
 * an empty CODE_TUI_CHECK disables verification.
 * otherwise ruff.toml or Cargo.toml at the root, plus lint, typecheck, format:check and test
 * (or build when neither exists) from the root and tui/package.json; lint, typecheck and format
 * are cheap, so a mutating cell cannot leave a tree the project itself would reject.
 * a Rust crate gets cargo fmt --check, cargo clippy --workspace --all-targets -- -D warnings and cargo test --workspace.
 * the package manager follows the lockfile: bun.lock/bun.lockb -> bun, package-lock.json -> npm, default bun; a tests/ directory is a bun target only for a bun project.
 * scope is the subdirectory a spec belongs to; the loop filters the cheap run by changed paths.
 *
 * WHERE TO CHANGE
 * what a plan looks like ............ the body of detectChecks
 * a new project convention .......... the package.json loop and the runner
 */
import { existsSync, readFileSync } from "node:fs";
import { join, relative } from "node:path";
import type { CheckSpec } from "./types";

/** The environment knobs this detector reads; a test passes its own instead of process.env. */
export type CheckEnv = {
  CODE_TUI_CHECKS?: string;
  CODE_TUI_CHECK?: string;
};

/** The verification plan for one workspace: CODE_TUI_CHECKS, else CODE_TUI_CHECK, else the
 * project's lint/typecheck/format/test scripts plus ruff. Empty means no goal. */
export function detectChecks(root: string, env: CheckEnv = process.env as CheckEnv): CheckSpec[] {
  const explicit = env.CODE_TUI_CHECKS;
  if (explicit !== undefined) {
    try {
      const parsed = JSON.parse(explicit) as unknown;
      if (!Array.isArray(parsed)) return [];
      return parsed
        .map((item) => item as { name?: unknown; cmd?: unknown; cheap?: unknown; scope?: unknown })
        .map((item) => ({
          name: String(item.name ?? item.cmd ?? "check"),
          cmd: String(item.cmd ?? "").trim(),
          cheap: Boolean(item.cheap),
          scope: Array.isArray(item.scope) ? item.scope.map((entry) => String(entry)) : [],
        }))
        .filter((item) => item.cmd.length > 0);
    } catch {
      return [];
    }
  }
  const single = env.CODE_TUI_CHECK;
  if (single !== undefined) {
    return single.trim() ? [{ name: "check", cmd: single.trim(), cheap: false }] : [];
  }
  const out: CheckSpec[] = [];
  const add = (name: string, cmd: string, cheap: boolean, scope: string[]) => {
    if (!out.some((item) => item.cmd === cmd)) out.push({ name, cmd, cheap, scope });
  };
  if (existsSync(join(root, "ruff.toml"))) add("lint", "python3 -m ruff check .", true, []);
  if (existsSync(join(root, "Cargo.toml"))) {
    add("format", "cargo fmt --check", true, []);
    add("lint", "cargo clippy --workspace --all-targets -- -D warnings", true, []);
    add("test", "cargo test --workspace", false, []);
  }
  const prefix = (dir: string, command: string) =>
    dir === root ? command : `cd ${relative(root, dir)} && ${command}`;
  for (const dir of [root, join(root, "tui")]) {
    let scripts: Record<string, string> = {};
    try {
      const pkg = JSON.parse(readFileSync(join(dir, "package.json"), "utf8")) as {
        scripts?: Record<string, string>;
      };
      scripts = pkg.scripts ?? {};
    } catch {
      continue;
    }
    const rel = relative(root, dir);
    const scope = rel ? [rel] : [];
    const runner =
      existsSync(join(dir, "bun.lock")) || existsSync(join(dir, "bun.lockb"))
        ? "bun"
        : existsSync(join(dir, "package-lock.json"))
          ? "npm"
          : "bun";
    const run = (script: string) => (runner === "npm" ? `npm run ${script}` : `bun run ${script}`);
    if (scripts.lint) add("lint", prefix(dir, run("lint")), true, scope);
    if (scripts.typecheck) add("typecheck", prefix(dir, run("typecheck")), true, scope);
    if (scripts["format:check"]) add("format", prefix(dir, run("format:check")), true, scope);
    if (scripts.test) add("test", prefix(dir, run("test")), false, scope);
    else if (runner === "bun" && existsSync(join(dir, "tests"))) {
      add("test", prefix(dir, "bun test"), false, scope);
    } else if (scripts.build && !scripts.typecheck) {
      add("build", prefix(dir, run("build")), false, scope);
    }
  }
  return out;
}
