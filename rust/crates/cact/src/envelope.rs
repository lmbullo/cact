//! envelope: the typed result every verb returns, and the state-card renderer's input.
//!
//! WHAT IT IS
//!   `Env` is the whole contract between a verb and the loop: ok/kind, a structured refusal,
//!   paging, structured data, notes, and the reads/changed/delta bookkeeping the card prints.
//!   `Env::text()` is the model-facing body; `card.rs` wraps it in `[s]`/`[obs]`/`[page]` lines.
//!
//! INVARIANTS
//!   every refusal carries code/message/use, never prose alone; `page.complete` means this
//!   request was served; `reads` are exact 1-based inclusive ranges actually delivered.

use serde::Serialize;
use serde_json::{Map, Value};

/// Paging truth for one answer: shown/total, the cursor, and whether the request was served.
#[derive(Debug, Clone, Default, Serialize)]
pub struct Page {
    pub shown: usize,
    pub total: usize,
    pub offset: usize,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub next: Option<usize>,
    pub unit: String,
    pub has_more: bool,
    pub truncated: bool,
    pub complete: bool,
}

impl Page {
    /// A whole answer: everything matched and was delivered.
    pub fn whole(total: usize, unit: &str) -> Page {
        Page {
            shown: total,
            total,
            offset: 0,
            next: None,
            unit: unit.into(),
            has_more: false,
            truncated: false,
            complete: true,
        }
    }

    /// A window `offset..offset+shown` of `total`.
    pub fn window(shown: usize, total: usize, offset: usize, unit: &str) -> Page {
        let has_more = offset + shown < total;
        Page {
            shown,
            total,
            offset,
            next: if has_more { Some(offset + shown) } else { None },
            unit: unit.into(),
            has_more,
            truncated: false,
            complete: true,
        }
    }
}

/// A structured refusal: a stable code, one sentence, and what the caller should do next.
#[derive(Debug, Clone, Serialize)]
pub struct Refusal {
    pub code: String,
    pub message: String,
    #[serde(rename = "use")]
    pub use_hint: String,
}

/// One verb result. `ok:false` always carries `error`.
#[derive(Debug, Clone, Default)]
pub struct Env {
    pub ok: bool,
    pub kind: String,
    pub error: Option<Refusal>,
    pub page: Option<Page>,
    pub data: Option<Map<String, Value>>,
    pub notes: Vec<String>,
    pub body: Option<String>,
    pub reads: Vec<(String, usize, usize)>,
    pub changed: Vec<String>,
    pub delta: Option<String>,
    pub duration_ms: u128,
}

impl Env {
    /// A successful answer of `kind` with no body yet.
    pub fn ok(kind: &str) -> Env {
        Env {
            ok: true,
            kind: kind.into(),
            ..Env::default()
        }
    }

    /// A refusal that carries the three fields a caller needs to recover.
    pub fn fail(
        kind: &str,
        code: &str,
        message: impl Into<String>,
        use_hint: impl Into<String>,
    ) -> Env {
        Env {
            ok: false,
            kind: kind.into(),
            error: Some(Refusal {
                code: code.into(),
                message: message.into(),
                use_hint: use_hint.into(),
            }),
            ..Env::default()
        }
    }

    /// Attach the delivered body text.
    pub fn body(mut self, text: impl Into<String>) -> Env {
        self.body = Some(text.into());
        self
    }

    /// Attach one structured field.
    pub fn field(mut self, key: &str, value: impl Into<Value>) -> Env {
        self.data
            .get_or_insert_with(Map::new)
            .insert(key.into(), value.into());
        self
    }

    /// Attach paging truth.
    pub fn paged(mut self, page: Page) -> Env {
        self.page = Some(page);
        self
    }

    /// Attach one note line (whitespace collapsed by the card).
    pub fn note(mut self, text: impl Into<String>) -> Env {
        self.notes.push(text.into());
        self
    }
}
