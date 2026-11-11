//! llm: the model client: one chat-completions POST, retries, an on-disk reply cache.
//!
//! WHAT IT IS
//!   `Client::complete` posts the OpenAI-shaped body to `{base}/chat/completions` (DeepSeek by
//!   default) through `curl` and returns a flat reply. No streaming: one turn = one blocking call.
//!   The transport is a subprocess on purpose: no TLS/HTTP crate graph to compile, and the build
//!   stays small and cheap.
//!
//! ENTRY POINTS
//!   Client::new(api_key, model, effort, base, timeout, cache_dir)      the production client
//!   .complete(messages, tools, max_tokens, effort_override) -> Reply   one model reply
//!
//! INVARIANTS
//!   cache key = sha256(request body); file <cache>/<key>.json. A hit returns local_cache=true,
//!   latency_ms=0. `BENCH_LLM_CACHE` moves the directory; cache=None disables it.
//!   retries: 4 attempts, 1.5s x attempt, only for 429/5xx and transport errors; anything else
//!   is an `LlmError` the loop turns into stop=LLM_FAILED, never a crash.
//!   finish == "length" means the turn was truncated; the loop maps it to TRUNCATED.

use serde_json::{json, Value};
use std::path::PathBuf;

/// A primary model call failed; the loop turns it into stop="LLM_FAILED", not a crash.
#[derive(Debug, Clone)]
pub struct LlmError(pub String);

impl std::fmt::Display for LlmError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "{}", self.0)
    }
}

/// One tool call the model asked for: id, name, and the raw JSON argument string.
#[derive(Debug, Clone)]
pub struct ToolCall {
    pub id: String,
    pub name: String,
    pub args: String,
}

/// Provider usage for one turn; a missing field is 0, never an estimate.
#[derive(Debug, Clone, Default)]
pub struct Usage {
    pub prompt: u64,
    pub completion: u64,
    pub reasoning: u64,
    pub total: u64,
    pub cache_hit_tokens: u64,
    pub cache_miss_tokens: u64,
}

/// One model reply, flattened for the loop.
#[derive(Debug, Clone, Default)]
pub struct Reply {
    pub content: String,
    pub reasoning: String,
    pub tool_calls: Vec<ToolCall>,
    pub usage: Usage,
    pub latency_ms: u128,
    pub finish: String,
    pub local_cache: bool,
}

/// Whether `effort` should be sent for a call.
pub enum Effort {
    /// Send this level.
    Level(String),
    /// Omit `reasoning_effort` entirely.
    Omit,
}

/// One chat-completions endpoint with retries and a disk reply cache.
pub struct Client {
    pub api_key: String,
    pub model: String,
    pub effort: Option<String>,
    pub base: String,
    pub timeout: f64,
    pub cache_dir: Option<PathBuf>,
}

impl Client {
    /// A client for one endpoint; `cache_dir` of `None` disables the reply cache.
    pub fn new(api_key: &str, model: &str, effort: Option<&str>, base: &str, timeout: f64, cache_dir: Option<PathBuf>) -> Client {
        Client {
            api_key: api_key.to_string(),
            model: model.to_string(),
            effort: effort.map(|s| s.to_string()),
            base: base.trim_end_matches('/').to_string(),
            timeout,
            cache_dir,
        }
    }

    /// The client's configured effort as a level: `None` means the field is omitted.
    pub fn effort_level(&self) -> Effort {
        match &self.effort {
            Some(level) => Effort::Level(level.clone()),
            None => Effort::Omit,
        }
    }

    /// The default cache directory: `$BENCH_LLM_CACHE`, else `<root>/.cache/llm`.
    pub fn default_cache(root: &std::path::Path) -> PathBuf {
        std::env::var("BENCH_LLM_CACHE")
            .map(PathBuf::from)
            .unwrap_or_else(|_| root.join(".cache").join("llm"))
    }

    /// One model reply, from the cache when the exact request was seen before.
    pub fn complete(
        &self,
        messages: &[Value],
        tools: Option<&[Value]>,
        max_tokens: usize,
        effort: Effort,
    ) -> Result<Reply, LlmError> {
        let level: Option<String> = match effort {
            Effort::Level(s) => Some(s),
            Effort::Omit => None,
        };
        let mut body = json!({"model": self.model, "messages": messages, "max_tokens": max_tokens});
        if let Some(level) = level {
            body["reasoning_effort"] = json!(level);
        }
        if let Some(tools) = tools {
            if !tools.is_empty() {
                body["tools"] = json!(tools);
                body["tool_choice"] = json!("auto");
            }
        }
        let data = body.to_string();
        let key = crate::util::digest(&data);
        if let Some(dir) = &self.cache_dir {
            let path = dir.join(format!("{key}.json"));
            if let Ok(text) = std::fs::read_to_string(&path) {
                if let Ok(mut reply) = parse_reply(&text) {
                    reply.local_cache = true;
                    reply.latency_ms = 0;
                    return Ok(reply);
                }
            }
        }
        let mut last = String::new();
        for attempt in 1..=4u32 {
            match self.post(&data) {
                Ok(reply) => {
                    if let Some(dir) = &self.cache_dir {
                        let _ = std::fs::create_dir_all(dir);
                        let _ = std::fs::write(dir.join(format!("{key}.json")), raw_of(&reply));
                    }
                    return Ok(reply);
                }
                Err(err) => {
                    last = err.0.clone();
                    if !retryable(&last) || attempt == 4 {
                        break;
                    }
                    std::thread::sleep(std::time::Duration::from_millis(1500 * attempt as u64));
                }
            }
        }
        Err(LlmError(last))
    }

    /// One HTTP POST through curl; the body lands in a scratch file, the code on stdout.
    fn post(&self, data: &str) -> Result<Reply, LlmError> {
        let stamp = format!("{}-{}", std::process::id(), now_nanos());
        let body_path = std::env::temp_dir().join(format!("cact-llm-{stamp}.json"));
        let url = format!("{}/chat/completions", self.base);
        let out = crate::util::run_argv(
            &[
                "curl".into(),
                "-sS".into(),
                "--max-time".into(),
                format!("{:.0}", self.timeout),
                "-o".into(),
                body_path.to_string_lossy().to_string(),
                "-w".into(),
                "%{http_code}".into(),
                "-H".into(),
                "Content-Type: application/json".into(),
                "-H".into(),
                format!("Authorization: Bearer {}", self.api_key),
                "-X".into(),
                "POST".into(),
                "--data-binary".into(),
                data.to_string(),
                url,
            ],
            &std::env::temp_dir(),
            &std::collections::BTreeMap::new(),
            None,
            self.timeout + 30.0,
        )
        .map_err(|e| LlmError(format!("transport: {e}")))?;
        let text = std::fs::read_to_string(&body_path).unwrap_or_default();
        let _ = std::fs::remove_file(&body_path);
        let status: u32 = out.stdout.trim().parse().unwrap_or(0);
        if out.timed_out || status == 0 {
            return Err(LlmError(format!("transport: {} {}", if out.timed_out { "timeout" } else { "no response" }, text.trim())));
        }
        if !(200..300).contains(&status) {
            return Err(LlmError(format!("http {status}: {}", crate::util::clip(text.trim(), 600))));
        }
        parse_reply(&text).map_err(|e| LlmError(format!("bad reply: {e}: {}", crate::util::clip(&text, 400))))
    }
}

/// True for the failures worth another attempt: 429/5xx and transport errors.
fn retryable(message: &str) -> bool {
    let lower = message.to_lowercase();
    if lower.starts_with("transport") {
        return true;
    }
    if let Some(rest) = lower.strip_prefix("http ") {
        let code: u32 = rest.split(':').next().unwrap_or("0").trim().parse().unwrap_or(0);
        return code == 429 || code >= 500;
    }
    false
}

/// Parse the provider's JSON into a flat `Reply`.
fn parse_reply(text: &str) -> Result<Reply, String> {
    let value: Value = serde_json::from_str(text).map_err(|e| e.to_string())?;
    let choice = value["choices"].get(0).ok_or("no choices")?;
    let message = &choice["message"];
    let content = message["content"].as_str().unwrap_or("").to_string();
    let reasoning = message["reasoning_content"]
        .as_str()
        .or_else(|| message["reasoning"].as_str())
        .unwrap_or("")
        .to_string();
    let mut calls = Vec::new();
    if let Some(list) = message["tool_calls"].as_array() {
        for call in list {
            let args = match &call["function"]["arguments"] {
                Value::String(s) => s.clone(),
                other => other.to_string(),
            };
            calls.push(ToolCall {
                id: call["id"].as_str().unwrap_or("").to_string(),
                name: call["function"]["name"].as_str().unwrap_or("").to_string(),
                args,
            });
        }
    }
    let u = &value["usage"];
    let usage = Usage {
        prompt: u["prompt_tokens"].as_u64().unwrap_or(0),
        completion: u["completion_tokens"].as_u64().unwrap_or(0),
        reasoning: u["completion_tokens_details"]["reasoning_tokens"].as_u64().unwrap_or(0),
        total: u["total_tokens"].as_u64().unwrap_or(0),
        cache_hit_tokens: u["prompt_cache_hit_tokens"].as_u64().unwrap_or(0),
        cache_miss_tokens: u["prompt_cache_miss_tokens"].as_u64().unwrap_or(0),
    };
    Ok(Reply {
        content,
        reasoning,
        tool_calls: calls,
        usage,
        latency_ms: 0,
        finish: choice["finish_reason"].as_str().unwrap_or("").to_string(),
        local_cache: false,
    })
}

/// The cache body: one JSON object carrying the reply fields plus the original JSON.
fn raw_of(reply: &Reply) -> String {
    json!({
        "choices": [{"message": {
            "content": reply.content,
            "reasoning_content": reply.reasoning,
            "tool_calls": reply.tool_calls.iter().map(|c| json!({
                "id": c.id, "type": "function",
                "function": {"name": c.name, "arguments": c.args}
            })).collect::<Vec<_>>()
        }, "finish_reason": reply.finish}],
        "usage": {
            "prompt_tokens": reply.usage.prompt,
            "completion_tokens": reply.usage.completion,
            "total_tokens": reply.usage.total,
            "completion_tokens_details": {"reasoning_tokens": reply.usage.reasoning},
            "prompt_cache_hit_tokens": reply.usage.cache_hit_tokens,
            "prompt_cache_miss_tokens": reply.usage.cache_miss_tokens
        }
    })
    .to_string()
}

/// Nanoseconds since the epoch, for a scratch-file name.
fn now_nanos() -> u128 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_nanos())
        .unwrap_or(0)
}
