//! obs: the session's numbered observation store (`_OBS[n]`), the paging contract's backing.
//!
//! WHAT IT IS
//!   Every cell result and every oversized verb body is pushed here; the card names the handle,
//!   `page(handle, offset, limit, field)` reads a slice back. No I/O: the store is in memory.
//!
//! INVARIANTS
//!   an index is never reused, so a handle from an earlier card still resolves after a restart of
//!   the kernel (the store lives in the parent, not the child).
//!   `page` of an unknown handle is a structured refusal (unknown_handle), never a panic.

use serde::Serialize;

/// One stored observation: the three fields a card may name.
#[derive(Debug, Clone, Default, Serialize)]
pub struct Entry {
    pub stdout: String,
    pub stderr: String,
    pub result: String,
}

/// The numbered store behind `_OBS[n]`.
#[derive(Default)]
pub struct Store {
    entries: Vec<Entry>,
}

impl Store {
    /// An empty store.
    pub fn new() -> Store {
        Store { entries: Vec::new() }
    }

    /// Append one entry and return its index (the `n` in `_OBS[n]`).
    pub fn push(&mut self, entry: Entry) -> usize {
        self.entries.push(entry);
        self.entries.len() - 1
    }

    /// Resolve a `_OBS[n]` handle to its index.
    pub fn index(handle: &str) -> Option<usize> {
        let inner = handle.trim().strip_prefix("_OBS[")?.strip_suffix(']')?;
        inner.trim().parse::<usize>().ok()
    }

    /// One slice of one field: `field` is stdout (default), stderr or result.
    pub fn page(&self, handle: &str, offset: usize, limit: usize, field: &str) -> Result<(String, usize, usize), String> {
        let index = Store::index(handle).ok_or_else(|| format!("not a handle: {handle}"))?;
        let entry = self.entries.get(index).ok_or_else(|| format!("no entry {handle}; {}-1 is the newest", self.entries.len()))?;
        let text = match field {
            "stderr" => &entry.stderr,
            "result" => &entry.result,
            _ => &entry.stdout,
        };
        let chars: Vec<char> = text.chars().collect();
        let total = chars.len();
        let start = offset.min(total);
        let take = if limit == 0 { total - start } else { limit.min(total - start) };
        Ok((chars[start..start + take].iter().collect(), total, start))
    }
}
