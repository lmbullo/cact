"""docs: fetch one documentation page -> clean, bounded text for the model.
WHAT IT IS
  The `docs` tool. DocsClient.fetch(url) fetches one http(s) page, strips scripts/styles/navigation,
  keeps headings and code blocks and returns compact text under a hard char budget. Parent-side
  like `delegate`: no network inside the bwrap kernel, no wider shell.
ENTRY POINTS
  DOCS_TOOL                       provider tool spec
  DocsClient(cache_dir=None, *, timeout=20, max_bytes=2_000_000, cache=True)
    .fetch(url, max_chars=12_000) -> dict  ok/kind/url/title/headings/text/chars/truncated
  extract(html, url="") -> dict   parser, exposed for tests
INVARIANTS
  scheme = http(s) only: file://, data: and every other scheme refused; no local file read, no
  workspace escape.
  size cap enforced before parsing: huge page -> truncated=True, never an unbounded download.
  fetch failure = structured dict (ok:false, error.code), never an exception into the loop.
  cache = disk, keyed by URL under bench/.cache/docs/; hit costs no network; cache=False for a
  live eval that must observe a changing page.
  parser = stdlib-only and tolerant: malformed HTML must not raise; result always has `text`
  (possibly "") so "empty page" != "fetch failed".
WHERE TO CHANGE
  stripped/kept content ..... `_Page`, `extract`
  network policy ............ `DocsClient.fetch` (scheme, timeout, byte cap)
  cache location ............ CACHE_DIR
  provider schema ........... DOCS_TOOL
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import urllib.error
import urllib.request
from html.parser import HTMLParser
from pathlib import Path

CACHE_DIR = Path(__file__).resolve().parent.parent / ".cache" / "docs"
_SKIP = {"script", "style", "noscript", "svg", "nav", "footer", "header", "form", "iframe"}
_BLOCK = {"p", "div", "section", "article", "main", "li", "tr", "h1", "h2", "h3", "h4",
          "h5", "h6", "pre", "blockquote"}
_HEAD = {"h1", "h2", "h3"}
_UA = "cact-agent/1.0 (+documentation reader)"

DOCS_TOOL = {
    "type": "function",
    "function": {
        "name": "docs",
        "description": (
            "Fetch one documentation page (http/https) and return clean text: title, headings "
            "and code blocks. Use it when an API, option or version behavior is uncertain "
            "instead of guessing from memory. One URL per call; page with max_chars on a long "
            "page. Read-only and outside the workspace."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "The http(s) URL to read."},
                "max_chars": {"type": "integer", "minimum": 1000,
                              "description": "Text budget for this page (default 12000)."},
            },
            "required": ["url"],
            "additionalProperties": False,
        },
    },
}


class _Page(HTMLParser):
    """Collect visible text, headings and code blocks from one HTML document."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.headings: list[str] = []
        self.parts: list[str] = []
        self._skip = 0
        self._head: str | None = None
        self._head_buf: list[str] = []
        self._pre = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        """Track skip regions, headings and preformatted blocks."""
        if tag in _SKIP:
            self._skip += 1
        if self._skip:
            return
        if tag == "title":
            self._head = "title"
            self._head_buf = []
        elif tag in _HEAD:
            self._head = tag
            self._head_buf = []
        if tag == "pre":
            self._pre += 1
            self.parts.append("\n```\n")
        elif tag in _BLOCK and self._pre == 0:
            self.parts.append("\n")
        elif tag == "br":
            self.parts.append("\n")
        elif tag == "li":
            self.parts.append("- ")

    def handle_endtag(self, tag: str) -> None:
        """Close skip regions, headings and code blocks; keep block separation."""
        if tag in _SKIP and self._skip:
            self._skip -= 1
            return
        if self._skip:
            return
        if tag == self._head:
            text = " ".join("".join(self._head_buf).split())
            if tag == "title":
                self.title = text
            elif text:
                self.headings.append(text)
            self._head = None
        if tag == "pre" and self._pre:
            self._pre -= 1
            self.parts.append("\n```\n")
        elif tag in _BLOCK and self._pre == 0:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        """Append visible text; headings are buffered so they can be normalized."""
        if self._skip:
            return
        if self._head is not None:
            self._head_buf.append(data)
            return
        if self._pre:
            self.parts.append(data)
        else:
            self.parts.append(re.sub(r"\s+", " ", data))


def extract(html: str, url: str = "") -> dict:
    """HTML -> {title, headings, text}; tolerant of malformed markup and empty pages."""
    parser = _Page()
    try:
        parser.feed(html or "")
        parser.close()
    except Exception:  # noqa: BLE001 - a malformed page must still return text
        pass
    raw = "".join(parser.parts)
    raw = re.sub(r"[ \t]+\n", "\n", raw)
    raw = re.sub(r"\n{3,}", "\n\n", raw).strip()
    return {"title": parser.title, "headings": parser.headings[:40], "text": raw, "url": url}


class DocsClient:
    """Fetch and cache documentation pages; the loop calls `fetch`, tests call `extract`."""

    def __init__(self, cache_dir: str | Path | None = None, *, timeout: float = 20.0,
                 max_bytes: int = 2_000_000, cache: bool = True) -> None:
        self.cache_dir = Path(cache_dir) if cache_dir else CACHE_DIR
        self.timeout = timeout
        self.max_bytes = max_bytes
        self.cache = cache
        if self.cache:
            os.makedirs(self.cache_dir, exist_ok=True)

    def _cached(self, url: str) -> dict | None:
        if not self.cache:
            return None
        path = self.cache_dir / (hashlib.sha256(url.encode()).hexdigest() + ".json")
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def _store(self, url: str, value: dict) -> None:
        if not self.cache:
            return
        path = self.cache_dir / (hashlib.sha256(url.encode()).hexdigest() + ".json")
        try:
            path.write_text(json.dumps(value), encoding="utf-8")
        except OSError:
            pass

    def fetch(self, url: str, max_chars: int = 12_000) -> dict:
        """Fetch one http(s) page, extract clean text, and truncate to `max_chars`."""
        url = str(url or "").strip()
        if not url.lower().startswith(("http://", "https://")):
            return {"ok": False, "kind": "docs", "url": url,
                    "error": {"code": "bad_url",
                              "message": "docs() accepts http(s) URLs only",
                              "use": "docs(url='https://...')"}}
        cached = self._cached(url)
        if cached is not None:
            return self._shape(cached, max_chars, cached=True)
        request = urllib.request.Request(url, headers={"User-Agent": _UA,
                                                       "Accept": "text/html,text/plain"})
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read(self.max_bytes + 1)
                charset = response.headers.get_content_charset() or "utf-8"
                final_url = response.geturl()
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            return {"ok": False, "kind": "docs", "url": url,
                    "error": {"code": "fetch_failed", "message": f"{type(exc).__name__}: {exc}",
                              "use": "try another URL or use search() on the local tree"}}
        truncated_download = len(raw) > self.max_bytes
        body = raw[: self.max_bytes].decode(charset, errors="replace")
        page = extract(body, final_url)
        value = {"url": final_url, "title": page["title"], "headings": page["headings"],
                 "text": page["text"], "download_truncated": truncated_download}
        self._store(url, value)
        return self._shape(value, max_chars, cached=False)

    @staticmethod
    def _shape(value: dict, max_chars: int, *, cached: bool) -> dict:
        text = str(value.get("text") or "")
        limit = max(1000, int(max_chars or 12_000))
        cut = text[:limit]
        return {
            "ok": True,
            "kind": "docs",
            "url": value.get("url"),
            "title": value.get("title") or "",
            "headings": value.get("headings") or [],
            "text": cut,
            "chars": len(cut),
            "total_chars": len(text),
            "truncated": len(text) > len(cut) or bool(value.get("download_truncated")),
            "cached": cached,
        }
