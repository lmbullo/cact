//! journal: the session record of every tracked edit, its diff, and undo.
//!
//! WHAT IT IS
//!   One entry per mutating call: path, before body (None for a create) and after body (None for
//!   a delete). `diff` renders the session's unified diffs, `summary` the per-path add/del counts
//!   `status()` reports, `undo` restores the last entry.
//!
//! INVARIANTS
//!   only typed mutation records here; a process the agent runs can still write files and this
//!   journal does not cover it (the docs say so, so no card may claim otherwise).
//!   a no-op write (identical bytes) records nothing, so an empty `session_diff` means no change.

use crate::util;
use std::path::{Path, PathBuf};

/// One recorded change to one path.
#[derive(Debug, Clone)]
pub struct Entry {
    pub path: String,
    pub before: Option<String>,
    pub after: Option<String>,
    pub diff: String,
}

/// The session journal: ordered entries plus the paths it has touched.
#[derive(Default)]
pub struct Journal {
    pub entries: Vec<Entry>,
    root: PathBuf,
}

impl Journal {
    /// A journal rooted at `root` (paths are recorded relative to it).
    pub fn new(root: &Path) -> Journal {
        Journal { entries: Vec::new(), root: root.to_path_buf() }
    }

    /// Record one change; returns false when the body did not actually change.
    pub fn record(&mut self, path: &str, before: Option<String>, after: Option<String>) -> bool {
        if before == after {
            return false;
        }
        let shown = before.clone().unwrap_or_default();
        let new = after.clone().unwrap_or_default();
        let diff = util::unified_diff(path, &shown, &new);
        self.entries.push(Entry { path: path.to_string(), before, after, diff });
        true
    }

    /// Every path touched this session, in first-touch order.
    pub fn paths(&self) -> Vec<String> {
        let mut out: Vec<String> = Vec::new();
        for entry in &self.entries {
            if !out.contains(&entry.path) {
                out.push(entry.path.clone());
            }
        }
        out
    }

    /// Added/deleted line counts per path (`git status`-shaped, never diff text).
    pub fn stats(&self) -> Vec<(String, usize, usize)> {
        let mut out: Vec<(String, usize, usize)> = Vec::new();
        for entry in &self.entries {
            let before = entry.before.as_deref().unwrap_or("");
            let after = entry.after.as_deref().unwrap_or("");
            let adds = after.lines().count();
            let dels = before.lines().count();
            match out.iter_mut().find(|(p, _, _)| *p == entry.path) {
                Some(row) => {
                    row.1 += adds;
                    row.2 += dels;
                }
                None => out.push((entry.path.clone(), adds, dels)),
            }
        }
        out
    }

    /// The session's unified diffs: all of it, or the entries for one `path`.
    pub fn diff(&self, path: Option<&str>, max_chars: usize) -> String {
        let mut blocks: Vec<String> = Vec::new();
        for entry in &self.entries {
            if let Some(only) = path {
                if entry.path != only {
                    continue;
                }
            }
            if !entry.diff.is_empty() {
                blocks.push(entry.diff.clone());
            }
        }
        util::clip(&blocks.join(""), if max_chars == 0 { 24_000 } else { max_chars })
    }

    /// Restore the most recent entry's path to its before-state; returns the path undone.
    pub fn undo(&mut self) -> Option<String> {
        let entry = self.entries.pop()?;
        let full = util::resolve(&self.root, &entry.path);
        match entry.before {
            Some(text) => {
                let _ = std::fs::create_dir_all(full.parent().unwrap_or(&self.root));
                let _ = std::fs::write(&full, text);
            }
            None => {
                let _ = std::fs::remove_file(&full);
            }
        }
        Some(entry.path)
    }
}
