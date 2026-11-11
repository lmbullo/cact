//! selfcheck: the offline contract check: no key, no network, one scratch tree per case.
//!
//! WHAT IT IS
//!   The Rust port's `bench/smoke.py`: it exercises the typed verbs, the journal, paging, the
//!   check plan and the exec gate against a scratch tree, prints one row per case, and exits 1 if
//!   any case failed. Each case gets its own directory, so one case cannot seed another.
//!
//! INVARIANTS
//!   every case asserts a real value from a real call (no fixture of our own shape).
//!   the scratch tree lives under the system temp dir and is removed on the way out.
//!   a case that cannot run is a failed row with its reason, never a silent pass.

use crate::checks;
use crate::tools::{self, Ctx};
use serde_json::json;
use std::path::Path;

/// One case's outcome: name, ok, and the detail line a failure shows.
struct Row {
    name: String,
    ok: bool,
    detail: String,
}

/// Run every offline case and return the process exit code.
pub fn run() -> i32 {
    let root = std::env::temp_dir().join(format!("cact-selfcheck-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&root);
    if std::fs::create_dir_all(&root).is_err() {
        println!("selfcheck: cannot create {}", root.display());
        return 1;
    }
    let mut n = 0usize;
    let mut rows: Vec<Row> = Vec::new();

    rows.push(case("write creates and reports", &root, &mut n, |dir| {
        let ctx = Ctx::new(dir, Vec::new(), true, 32_000);
        let env = tools::call(&ctx, "write", &json!({"path": "src/a.txt", "content": "alpha\nbeta\ngamma\n"}));
        check(env.ok, "write not ok")?;
        check(dir.join("src/a.txt").is_file(), "file not created")?;
        check(env.changed == vec!["src/a.txt".to_string()], "journal did not record the path")
    }));

    rows.push(case("read pages lines exactly", &root, &mut n, |dir| {
        let ctx = Ctx::new(dir, Vec::new(), true, 32_000);
        let _ = tools::call(&ctx, "write", &json!({"path": "a.txt", "content": "alpha\nbeta\ngamma\n"}));
        let env = tools::call(&ctx, "read", &json!({"path": "a.txt", "start": 2, "end": 3}));
        check(env.ok, "read not ok")?;
        let page = env.page.as_ref().ok_or("no page")?;
        check(page.total == 3 && page.offset == 1 && page.shown == 2, "page numbers wrong")?;
        let body = env.body.clone().unwrap_or_default();
        check(body.contains("beta") && !body.contains("alpha"), "wrong window")?;
        let again = tools::call(&ctx, "read", &json!({"path": "a.txt", "start": 2, "end": 3}));
        check(again.body.clone().unwrap_or_default().contains("already delivered"), "repeat is not a pointer")?;
        let forced = tools::call(&ctx, "read", &json!({"path": "a.txt", "start": 2, "end": 3, "repeat": true}));
        check(forced.body.clone().unwrap_or_default().contains("beta"), "repeat=true did not re-deliver")
    }));

    rows.push(case("edit is atomic and refuses a bad second edit", &root, &mut n, |dir| {
        let ctx = Ctx::new(dir, Vec::new(), true, 32_000);
        let _ = tools::call(&ctx, "write", &json!({"path": "a.txt", "content": "alpha\nbeta\n"}));
        let bad = tools::call(&ctx, "edit", &json!({"edits": [
            {"path": "a.txt", "old": "alpha", "new": "ALPHA"},
            {"path": "a.txt", "old": "not-there", "new": "x"}
        ]}));
        check(!bad.ok, "bad edit was accepted")?;
        check(bad.error.as_ref().map(|e| e.code.as_str()) == Some("no_match"), "wrong refusal code")?;
        let text = std::fs::read_to_string(dir.join("a.txt")).unwrap_or_default();
        check(text.contains("alpha"), "first edit was applied despite the failure")?;
        let good = tools::call(&ctx, "edit", &json!({"edits": [{"path": "a.txt", "old": "alpha", "new": "ALPHA"}]}));
        check(good.ok && good.changed == vec!["a.txt".to_string()], "good edit did not land")
    }));

    rows.push(case("splice inserts and deletes by line", &root, &mut n, |dir| {
        let ctx = Ctx::new(dir, Vec::new(), true, 32_000);
        let _ = tools::call(&ctx, "write", &json!({"path": "a.txt", "content": "alpha\nbeta\ngamma\n"}));
        let env = tools::call(&ctx, "splice", &json!({"path": "a.txt", "start": 2, "end": 1, "text": "inserted"}));
        check(env.ok, "insert refused")?;
        let text = std::fs::read_to_string(dir.join("a.txt")).unwrap_or_default();
        check(text.lines().nth(1) == Some("inserted"), "line 2 is not the inserted line")?;
        let undo = tools::call(&ctx, "splice", &json!({"path": "a.txt", "start": 2, "end": 2, "text": ""}));
        check(undo.ok, "delete refused")?;
        let text = std::fs::read_to_string(dir.join("a.txt")).unwrap_or_default();
        check(text.lines().nth(1) == Some("beta"), "line 2 is not beta after the delete")
    }));

    rows.push(case("apply lands a unified diff", &root, &mut n, |dir| {
        let ctx = Ctx::new(dir, Vec::new(), true, 32_000);
        let _ = tools::call(&ctx, "write", &json!({"path": "a.txt", "content": "ALPHA\nbeta\n"}));
        let env = tools::call(&ctx, "apply", &json!({"patch": "--- a/a.txt\n+++ b/a.txt\n@@ -1,1 +1,1 @@\n-ALPHA\n+alpha\n"}));
        check(env.ok, "apply not ok")?;
        let text = std::fs::read_to_string(dir.join("a.txt")).unwrap_or_default();
        check(text.starts_with("alpha"), "diff did not apply")
    }));

    rows.push(case("map/search/outline answer from the tree", &root, &mut n, |dir| {
        let ctx = Ctx::new(dir, Vec::new(), true, 32_000);
        let _ = tools::call(&ctx, "write", &json!({"path": "src/b.py", "content": "def alpha():\n    return 1\n\nclass Beta:\n    pass\n"}));
        let map = tools::call(&ctx, "map", &json!({"path": "src"}));
        check(map.body.clone().unwrap_or_default().contains("src/b.py (5 lines)"), "map row wrong")?;
        let found = tools::call(&ctx, "search", &json!({"pattern": "def \\w+", "path": "src"}));
        check(found.body.clone().unwrap_or_default().contains("src/b.py:1:"), "search hit missing")?;
        let out = tools::call(&ctx, "outline", &json!({"path": "src/b.py"}));
        let body = out.body.clone().unwrap_or_default();
        check(body.contains("alpha") && body.contains("Beta"), "outline missed a symbol")
    }));

    rows.push(case("session_diff and status read the journal", &root, &mut n, |dir| {
        let ctx = Ctx::new(dir, Vec::new(), true, 32_000);
        let _ = tools::call(&ctx, "write", &json!({"path": "src/a.txt", "content": "alpha\n"}));
        let diff = tools::call(&ctx, "session_diff", &json!({}));
        check(diff.body.clone().unwrap_or_default().contains("+alpha"), "diff body missing")?;
        let status = tools::call(&ctx, "status", &json!({}));
        let body = status.body.clone().unwrap_or_default();
        check(body.contains("src/a.txt"), "status did not name the journal path")
    }));

    rows.push(case("exec refuses a workspace mutation and runs a scratch one", &root, &mut n, |dir| {
        let ctx = Ctx::new(dir, Vec::new(), true, 32_000);
        let _ = tools::call(&ctx, "write", &json!({"path": "a.txt", "content": "x\n"}));
        let refused = tools::call(&ctx, "exec", &json!({"argv": ["tee", "a.txt"]}));
        check(!refused.ok && refused.error.as_ref().map(|e| e.code.as_str()) == Some("wrong_call"), "tee was not gated")?;
        let redirect = tools::call(&ctx, "exec", &json!({"shell": "echo hi > inside.txt"}));
        check(!redirect.ok, "a workspace redirect was allowed")?;
        let allowed = tools::call(&ctx, "exec", &json!({"shell": "echo ok > /tmp/cact-selfcheck-scratch.txt && cat /tmp/cact-selfcheck-scratch.txt"}));
        check(allowed.ok, "scratch write refused")?;
        check(allowed.body.clone().unwrap_or_default().contains("ok"), "scratch output missing")?;
        let _ = std::fs::remove_file("/tmp/cact-selfcheck-scratch.txt");
        let read_only = tools::call(&ctx, "exec", &json!({"argv": ["printf", "hello"]}));
        check(read_only.ok && read_only.body.clone().unwrap_or_default().contains("hello"), "read-only argv refused")
    }));

    rows.push(case("checks runs the plan and reports the first failure", &root, &mut n, |dir| {
        let specs = checks::parse(r#"[{"name": "good", "cmd": "true"}, {"name": "bad", "cmd": "false"}]"#);
        let ctx = Ctx::new(dir, specs, true, 32_000);
        let env = tools::call(&ctx, "checks", &json!({}));
        check(env.ok, "checks returned a refusal")?;
        let body = env.body.clone().unwrap_or_default();
        check(body.contains("good: ok"), "the passing row is missing")?;
        check(body.contains("bad: failed"), "the failing row is missing")?;
        let rows = env.data.as_ref().and_then(|d| d.get("rows")).and_then(|v| v.as_array()).cloned().unwrap_or_default();
        check(rows.len() == 2 && rows[1]["ok"] == json!(false), "row shape wrong")
    }));

    rows.push(case("plan and task keep the durable state", &root, &mut n, |dir| {
        let ctx = Ctx::new(dir, Vec::new(), true, 32_000);
        let _ = tools::call(&ctx, "plan", &json!({"items": ["read the contract", "change the symbol", "run the checks"]}));
        let _ = tools::call(&ctx, "plan", &json!({"done": [1]}));
        let task = tools::call(&ctx, "task", &json!({}));
        let body = task.body.clone().unwrap_or_default();
        check(body.contains("x1. read the contract"), "plan item 1 is not marked done")?;
        check(body.contains("open: change the symbol"), "open items wrong")
    }));

    rows.push(case("page reads an _OBS handle and refuses an unknown one", &root, &mut n, |dir| {
        let ctx = Ctx::new(dir, Vec::new(), true, 32_000);
        let handle = format!("_OBS[{}]", ctx.obs.borrow_mut().push(crate::obs::Entry {
            stdout: "line one\nline two\n".into(),
            ..Default::default()
        }));
        let env = tools::call(&ctx, "page", &json!({"handle": handle, "offset": 9, "limit": 3}));
        check(env.ok && env.body.clone().unwrap_or_default() == "lin", "slice wrong")?;
        let bad = tools::call(&ctx, "page", &json!({"handle": "_OBS[99]"}));
        check(!bad.ok && bad.error.as_ref().map(|e| e.code.as_str()) == Some("unknown_handle"), "unknown handle accepted")
    }));

    rows.push(case("scope refuses a name it does not grant", &root, &mut n, |dir| {
        let ctx = Ctx::new(dir, Vec::new(), true, 32_000);
        let env = tools::call(&ctx, "nope", &json!({}));
        check(!env.ok && env.error.as_ref().map(|e| e.code.as_str()) == Some("unknown_tool"), "unknown verb accepted")?;
        let scope = crate::spec::Scope::production();
        check(scope.allows("code") && scope.allows("read") && !scope.allows("serve"), "scope grants the wrong names")
    }));

    rows.push(case("delete refuses a directory and removes a file", &root, &mut n, |dir| {
        let ctx = Ctx::new(dir, Vec::new(), true, 32_000);
        let _ = tools::call(&ctx, "mkdir", &json!({"path": "src"}));
        let dir_refused = tools::call(&ctx, "delete", &json!({"path": "src"}));
        check(!dir_refused.ok && dir_refused.error.as_ref().map(|e| e.code.as_str()) == Some("is_directory"), "directory delete accepted")?;
        let _ = tools::call(&ctx, "write", &json!({"path": "src/tmp.txt", "content": "x\n"}));
        let gone = tools::call(&ctx, "delete", &json!({"path": "src/tmp.txt"}));
        check(gone.ok && !dir.join("src/tmp.txt").exists(), "file not deleted")
    }));

    rows.push(case("the kernel driver runs a persistent cell", &root, &mut n, |dir| {
        let ctx = Ctx::new(dir, Vec::new(), true, 32_000);
        let mut kernel = crate::kernel::Kernel::start(dir, 30.0);
        let first = kernel.cell(&ctx, "x = 21\nprint('plain output')\nx * 2", "code");
        check(first.error.is_none(), &format!("cell error: {:?}", first.error))?;
        check(first.stdout.contains("plain output"), "stdout not captured")?;
        check(first.result == "42", &format!("result was {:?}", first.result))?;
        let second = kernel.cell(&ctx, "x + 1", "code");
        check(second.result == "22", "the namespace did not persist")?;
        let verb = kernel.cell(&ctx, "write(path='from_cell.txt', content='hi\\n')", "code");
        check(verb.error.is_none(), "a verb call inside a cell failed")?;
        check(dir.join("from_cell.txt").is_file(), "the cell's write did not reach the workspace")?;
        let finish = kernel.cell(&ctx, "final_answer('all done')", "code");
        check(finish.final_value == Some(json!("all done")), "final_answer did not end the cell")?;
        kernel.stop();
        Ok(())
    }));

    rows.push(case("undo restores the last journaled change", &root, &mut n, |dir| {
        let ctx = Ctx::new(dir, Vec::new(), true, 32_000);
        let _ = tools::call(&ctx, "write", &json!({"path": "u.txt", "content": "one\ntwo\n"}));
        let _ = tools::call(&ctx, "write", &json!({"path": "u.txt", "content": "one\nTWO\n"}));
        let undone = tools::call(&ctx, "undo", &json!({}));
        check(undone.ok, "undo refused")?;
        let text = std::fs::read_to_string(dir.join("u.txt")).unwrap_or_default();
        check(text == "one\ntwo\n", "undo did not restore the previous body")?;
        check(ctx.changed() == vec!["u.txt".to_string()], "undo disturbed the journal paths")
    }));

    let failed = rows.iter().filter(|r| !r.ok).count();
    for row in &rows {
        println!("{} {}{}", if row.ok { "ok  " } else { "FAIL" }, row.name, if row.ok { String::new() } else { format!(": {}", row.detail) });
    }
    println!("\n{}/{} cases passed", rows.len() - failed, rows.len());
    let _ = std::fs::remove_dir_all(&root);
    if failed == 0 {
        0
    } else {
        1
    }
}

/// Run one case in its own directory, turning an `Err` into a failed row.
fn case(name: &str, root: &Path, n: &mut usize, body: impl FnOnce(&Path) -> Result<(), String>) -> Row {
    *n += 1;
    let dir = root.join(format!("case{n:02}"));
    let _ = std::fs::create_dir_all(&dir);
    match body(&dir) {
        Ok(()) => Row { name: name.into(), ok: true, detail: String::new() },
        Err(detail) => Row { name: name.into(), ok: false, detail },
    }
}

/// `check(condition, why)` is the one assertion this file needs.
fn check(condition: bool, why: &str) -> Result<(), String> {
    if condition {
        Ok(())
    } else {
        Err(why.to_string())
    }
}
