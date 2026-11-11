//! contract: the task prompt's explicit imperatives and the evidence a finish must show.
//!
//! WHAT IT IS
//!   `parse(prompt)` turns explicit task instructions into durable plan items; `Evidence`
//!   records what the run actually did (commits, branches, pushes, commands, changes, checks);
//!   `missing(items, evidence)` names every item a finish cannot support yet.
//!
//! ENTRY POINTS
//!   parse(prompt) -> Vec<Item>          explicit commit/branch/push/verify/change instructions
//!   Evidence::default()                 one run's observed evidence
//!   Evidence::record_call(name, args, ok), .observe_runs(&[String]), .set_checks(bool)
//!   missing(items, evidence) -> Vec<Item>
//!   commands_from_args(args) -> Vec<String>   the command lines one exec call requested
//!
//! INVARIANTS
//!   a requirement is data: each item names the evidence kind that can satisfy it.
//!   a negated instruction is itself a requirement (`do not commit` needs zero commits).
//!   no prose guessing: only a successful call or a recorded program line is evidence.
//!   a prompt with no explicit imperative produces no items and gates nothing.

use serde_json::Value;

/// One explicit task instruction and the evidence family that can satisfy it.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Kind {
    Commit,
    NoCommit,
    Branch,
    Push,
    Verify,
    Change,
}

impl Kind {
    /// The concrete evidence a reader or the model needs for this kind.
    pub fn needs(self) -> &'static str {
        match self {
            Kind::Commit => "a successful commit (git action=commit or a successful `git commit`)",
            Kind::NoCommit => "no commit in this run",
            Kind::Branch => "a branch created or switched (`git checkout -b`, `git switch -c`, or git action=branch with branch=...)",
            Kind::Push => "a successful push (git action=push or a successful `git push`)",
            Kind::Verify => "a passing check plan (checks() green)",
            Kind::Change => "at least one journalled workspace change",
        }
    }
}

/// One parsed task instruction.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Item {
    pub text: String,
    pub kind: Kind,
}

/// What this run has actually done, in the forms a finish gate can verify.
#[derive(Debug, Clone, Default)]
pub struct Evidence {
    pub commits: usize,
    pub branches: usize,
    pub pushes: usize,
    pub commands: Vec<String>,
    pub changes: usize,
    pub checks_green: bool,
    pub verification_runs: usize,
}

impl Evidence {
    /// Record one successful typed call; failed calls are not evidence.
    pub fn record_call(&mut self, name: &str, args: &Value, ok: bool) {
        if !ok {
            return;
        }
        if name == "git" {
            match args["action"].as_str().unwrap_or("") {
                "commit" => self.commits += 1,
                "push" => self.pushes += 1,
                "branch"
                    if args["branch"]
                        .as_str()
                        .map(|s| !s.trim().is_empty())
                        .unwrap_or(false) =>
                {
                    self.branches += 1;
                }
                _ => {}
            }
        }
        for command in commands_from_args(args) {
            self.observe_command(&command);
        }
    }

    /// Record every program a cell spawned, including a program spawned inside Python.
    /// `ok` means the cell/program as a whole exited cleanly; it is the verification evidence.
    pub fn observe_runs(&mut self, runs: &[String], ok: bool) {
        for command in runs {
            if ok {
                self.observe_command(command);
                if looks_like_verification(command) {
                    self.verification_runs += 1;
                }
            } else {
                self.commands.push(command.clone());
            }
        }
    }

    /// Count the git actions one command line names.
    pub fn observe_command(&mut self, command: &str) {
        let normalized = command.to_lowercase();
        self.commands.push(command.to_string());
        let words: Vec<&str> = normalized.split_whitespace().collect();
        for (index, word) in words.iter().enumerate() {
            if *word != "git" {
                continue;
            }
            let action = words[index + 1..]
                .iter()
                .find(|part| !part.starts_with('-'))
                .copied()
                .unwrap_or("");
            match action {
                "commit" => self.commits += 1,
                "push" => self.pushes += 1,
                "checkout" => {
                    if words[index + 2..]
                        .iter()
                        .any(|part| *part == "-b" || *part == "-B")
                    {
                        self.branches += 1;
                    }
                }
                "switch" => {
                    if words[index + 2..]
                        .iter()
                        .any(|part| *part == "-c" || *part == "-C")
                    {
                        self.branches += 1;
                    }
                }
                "branch" => {
                    let creates = words[index + 2..]
                        .iter()
                        .any(|part| !part.starts_with('-') && *part != "git");
                    if creates {
                        self.branches += 1;
                    }
                }
                _ => {}
            }
        }
    }

    /// Freeze the two facts the loop reads from its own state at finish time.
    pub fn snapshot(&self, changes: usize, checks_green: bool) -> Evidence {
        let mut evidence = self.clone();
        evidence.changes = changes;
        evidence.checks_green = checks_green;
        evidence
    }

    /// True when the evidence supports one requirement.
    pub fn satisfies(&self, kind: Kind) -> bool {
        match kind {
            Kind::Commit => self.commits > 0,
            Kind::NoCommit => self.commits == 0,
            Kind::Branch => self.branches > 0,
            Kind::Push => self.pushes > 0,
            Kind::Verify => self.checks_green || self.verification_runs > 0,
            Kind::Change => self.changes > 0,
        }
    }
}

/// True when a command line is a project check or test run rather than ordinary work.
fn looks_like_verification(command: &str) -> bool {
    let lower = command.to_lowercase();
    [
        "test",
        "tests",
        "pytest",
        "ruff",
        "typecheck",
        "clippy",
        "cargo check",
        "npm run build",
        "bun test",
        "go test",
        "mvn test",
        "gradle test",
    ]
    .iter()
    .any(|word| lower.contains(word))
}

/// The command lines one exec call asked for: shell strings, one argv, or each commands entry.
pub fn commands_from_args(args: &Value) -> Vec<String> {
    let mut lines: Vec<String> = Vec::new();
    if let Some(shell) = args["shell"].as_str() {
        lines.push(shell.to_string());
    }
    if let Some(argv) = args["argv"].as_array() {
        lines.push(
            argv.iter()
                .filter_map(|v| v.as_str())
                .collect::<Vec<_>>()
                .join(" "),
        );
    } else if let Some(argv) = args["argv"].as_str() {
        lines.push(argv.to_string());
    }
    if let Some(commands) = args["commands"].as_array() {
        for command in commands {
            if let Some(argv) = command.as_array() {
                lines.push(
                    argv.iter()
                        .filter_map(|v| v.as_str())
                        .collect::<Vec<_>>()
                        .join(" "),
                );
            } else if let Some(command) = command.as_str() {
                lines.push(command.to_string());
            }
        }
    }
    lines
        .into_iter()
        .filter(|line| !line.trim().is_empty())
        .collect()
}

/// Parse the task's explicit imperatives; history before `[TASK]` is not parsed.
pub fn parse(prompt: &str) -> Vec<Item> {
    let task = prompt.rsplit("[TASK]\n").next().unwrap_or(prompt);
    let mut items: Vec<Item> = Vec::new();
    for raw in task.lines() {
        let line = raw.trim().trim_start_matches(['-', '*', '#', '>', ' ']);
        let line = line.trim_start_matches(|c: char| c.is_ascii_digit() || c == '.' || c == ')');
        for sentence in split_sentences(line) {
            let text = sentence.trim();
            if text.is_empty() {
                continue;
            }
            let lower = text.to_lowercase();
            if !is_imperative(&lower) {
                continue;
            }
            let negated = is_negated(&lower);
            let mut kinds: Vec<Kind> = Vec::new();
            if has_word(&lower, "commit") {
                kinds.push(if negated {
                    Kind::NoCommit
                } else {
                    Kind::Commit
                });
            }
            if !negated
                && (has_word(&lower, "branch")
                    || lower.contains("checkout -b")
                    || lower.contains("switch -c"))
            {
                kinds.push(Kind::Branch);
            }
            if has_word(&lower, "push") && !negated {
                kinds.push(Kind::Push);
            }
            if [
                "test",
                "tests",
                "check",
                "checks",
                "verify",
                "verified",
                "pass",
                "passing",
                "green",
                "everything",
            ]
            .iter()
            .any(|word| has_word(&lower, word))
                || lower.contains("all of it")
                || lower.contains("whole suite")
            {
                kinds.push(Kind::Verify);
            }
            if [
                "add",
                "implement",
                "fix",
                "change",
                "update",
                "remove",
                "rename",
                "refactor",
                "migrate",
                "create",
                "write",
                "delete",
                "replace",
                "move",
                "port",
                "build",
            ]
            .iter()
            .any(|word| starts_with_word(&lower, word))
            {
                kinds.push(Kind::Change);
            }
            for kind in kinds {
                let text = text.chars().take(180).collect::<String>();
                if !items
                    .iter()
                    .any(|item| item.kind == kind && item.text == text)
                {
                    items.push(Item { text, kind });
                }
            }
        }
    }
    items
}

/// Every requirement the current evidence does not support.
pub fn missing(items: &[Item], evidence: &Evidence) -> Vec<Item> {
    items
        .iter()
        .filter(|item| !evidence.satisfies(item.kind))
        .cloned()
        .collect()
}

/// The lines one rejected finish shows: the unmet instruction and its required evidence.
pub fn rejection(items: &[Item]) -> String {
    let rows: Vec<String> = items
        .iter()
        .map(|item| format!("- {} (needs {})", item.text, item.kind.needs()))
        .collect();
    format!(
        "[runtime] final_answer rejected: the task prompt has constraints with no evidence yet:\n{}\nSatisfy each one and verify again; do not report it done.",
        rows.join("\n")
    )
}

/// Split one line into clauses at sentence punctuation, without splitting a path or decimal.
fn split_sentences(line: &str) -> Vec<String> {
    let mut out = Vec::new();
    let mut current = String::new();
    for ch in line.chars() {
        if ch == '.' || ch == ';' || ch == '!' || ch == '?' {
            if !current.trim().is_empty() {
                out.push(current.trim().to_string());
            }
            current.clear();
        } else {
            current.push(ch);
        }
    }
    if !current.trim().is_empty() {
        out.push(current.trim().to_string());
    }
    out
}

/// True when a clause reads as a directive rather than background prose.
fn is_imperative(lower: &str) -> bool {
    const VERBS: &[&str] = &[
        "add",
        "implement",
        "fix",
        "change",
        "update",
        "remove",
        "rename",
        "refactor",
        "migrate",
        "create",
        "write",
        "delete",
        "replace",
        "move",
        "port",
        "build",
        "commit",
        "branch",
        "push",
        "run",
        "test",
        "check",
        "verify",
        "make",
        "keep",
        "ensure",
        "remember",
        "always",
        "never",
        "do",
        "use",
    ];
    VERBS.iter().any(|verb| starts_with_word(lower, verb))
        || lower == "everything"
        || lower.contains("make sure")
        || lower.contains("must ")
        || lower.contains("need to")
        || lower.contains("remember to")
        || lower.contains("should ")
}

/// True when a clause forbids the action instead of requesting it.
fn is_negated(lower: &str) -> bool {
    lower.contains("do not")
        || lower.contains("don't")
        || lower.contains("never ")
        || lower.contains("without ")
        || lower.contains("avoid ")
        || lower.contains("no need")
}

/// True when `word` occurs as a whole word, not as a substring of a path or identifier.
fn has_word(text: &str, word: &str) -> bool {
    text.split(|c: char| !(c.is_ascii_alphanumeric() || c == '_' || c == '-'))
        .any(|part| part == word)
}

/// True when the clause starts with `word` as its first word.
fn starts_with_word(text: &str, word: &str) -> bool {
    let first = text
        .split(|c: char| !(c.is_ascii_alphanumeric() || c == '_' || c == '-'))
        .find(|part| !part.is_empty());
    first == Some(word)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn explicit_imperatives_become_items() {
        let items =
            parse("Add shortHandle(id). Commit as you go.\nBranch rust first.\nDo everything.");
        assert!(items.iter().any(|item| item.kind == Kind::Commit));
        assert!(items.iter().any(|item| item.kind == Kind::Branch));
        assert!(items.iter().any(|item| item.kind == Kind::Verify));
        assert!(items.iter().any(|item| item.kind == Kind::Change));
    }

    #[test]
    fn a_negated_instruction_is_a_requirement() {
        let items = parse("Do not commit; leave the tree dirty.");
        assert_eq!(items[0].kind, Kind::NoCommit);
        let evidence = Evidence::default();
        assert!(missing(&items, &evidence).is_empty());
        let committed = Evidence {
            commits: 1,
            ..Evidence::default()
        };
        assert_eq!(missing(&items, &committed).len(), 1);
    }

    #[test]
    fn evidence_reads_typed_and_spawned_git() {
        let mut evidence = Evidence::default();
        evidence.record_call(
            "git",
            &serde_json::json!({"action": "commit", "message": "x"}),
            true,
        );
        evidence.observe_runs(
            &[
                "git checkout -b rust".to_string(),
                "git push origin rust".to_string(),
            ],
            true,
        );
        assert!(evidence.satisfies(Kind::Commit));
        assert!(evidence.satisfies(Kind::Branch));
        assert!(evidence.satisfies(Kind::Push));
    }

    #[test]
    fn history_before_the_task_is_not_parsed() {
        let items = parse(
            "[workspace history]\nCommit everything from the old task.\n\n[TASK]\nFix the parser.",
        );
        assert!(!items.iter().any(|item| item.kind == Kind::Commit));
        assert!(items.iter().any(|item| item.kind == Kind::Change));
    }
}
