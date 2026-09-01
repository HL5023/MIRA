"""Pi integration: real coding-agent tools merged into Mira.

Gives Mira genuine capabilities like the Pi coding agent has:
  - real web search that returns result summaries (not just opening a browser)
  - filesystem / code search (grep)
These are registered as Mira tools so she can call them through the tool loop.

The ./pi/ folder at the repo root holds the Pi agent's own data (memory, skills,
sessions) and is left untouched. This module is Mira's bridge to pi-style power.
"""

import base64
import html as html_lib
import re
import urllib.parse
import urllib.request
from pathlib import Path


# ── Web search (self-contained, no API key) ───────────────────────────────

_HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}


def _strip_html(text: str) -> str:
    """Remove HTML tags and collapse whitespace."""
    text = re.sub(r"<[^>]+>", "", text)
    return " ".join(text.split())


def _fetch(url: str, timeout: int = 15) -> str:
    req = urllib.request.Request(url, headers=_HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="ignore")


def web_search(query: str, num_results: int = 5) -> str:
    """Perform a real web search and return top result summaries (via Bing)."""
    if num_results < 1:
        num_results = 1
    if num_results > 8:
        num_results = 8
    url = "https://www.bing.com/search?q=" + urllib.parse.quote(query)
    try:
        html = _fetch(url)
    except Exception as e:
        return f"web search failed: {e}"
    return _parse_bing_results(html, num_results)


def _clean_bing_url(url: str) -> str:
    """Decode a Bing redirect link into the real URL."""
    url = html_lib.unescape(url)
    m = re.search(r"[?&]u=([^&]+)", url)
    if m:
        val = m.group(1)
        # The real URL is a base64 string with a (variable-length) cache-buster prefix
        for cand in (val, val[1:], val[2:], val[3:], val[4:]):
            try:
                padded = cand + "=" * (-len(cand) % 4)
                decoded = base64.urlsafe_b64decode(padded).decode("utf-8")
                if decoded.startswith(("http://", "https://", "www.")):
                    return decoded
            except Exception:
                continue
    return url


def _parse_bing_results(html: str, num_results: int) -> str:
    """Parse Bing search result HTML into a readable summary."""
    lines = []
    blocks = re.split(r'<li class="b_algo"', html)
    for block in blocks[1:]:
        title_m = re.search(r'<h2[^>]*>\s*<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>', block, re.DOTALL)
        if not title_m:
            continue
        url = _clean_bing_url(title_m.group(1))
        title = _strip_html(title_m.group(2))
        snippet_m = re.search(r'<p[^>]*>(.*?)</p>', block, re.DOTALL)
        snippet = _strip_html(snippet_m.group(1)) if snippet_m else ""
        lines.append(f"• {title}\n  {url}\n  {snippet}")
        if len(lines) >= num_results:
            break
    if not lines:
        return "no search results found"
    return "\n\n".join(lines)


def read_website(url: str, max_chars: int = 4000) -> str:
    """Fetch a webpage and return its readable text."""
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    try:
        html = _fetch(url)
    except Exception as e:
        return f"error fetching {url}: {e}"
    try:
        from html.parser import HTMLParser

        class TextExtractor(HTMLParser):
            def __init__(self):
                super().__init__()
                self.texts = []
                self.skip = False

            def handle_starttag(self, tag, attrs):
                if tag in ("script", "style", "nav", "footer", "header"):
                    self.skip = True

            def handle_endtag(self, tag):
                if tag in ("script", "style", "nav", "footer", "header"):
                    self.skip = False

            def handle_data(self, data):
                if not self.skip:
                    self.texts.append(data)

        parser = TextExtractor()
        parser.feed(html)
        text = " ".join(" ".join(parser.texts).split())
        if not text.strip():
            return "page loaded but no readable text found"
        return text[:max_chars] + "..." if len(text) > max_chars else text
    except Exception as e:
        return f"fetched {url} but could not parse text: {e}"


# ── Filesystem / code search ──────────────────────────────────────────────


def search_files(query: str, directory: str = "", max_results: int = 20, file_filter: str = "") -> str:
    """Grep-style search for a text pattern in files under a directory."""
    root = Path(directory).expanduser() if directory else Path.cwd()
    if not root.exists():
        return f"directory not found: {root}"
    try:
        compiled = re.compile(query, re.IGNORECASE)
    except re.error as e:
        return f"invalid search pattern: {e}"

    results = []
    globs = [file_filter] if file_filter else None
    try:
        iterator = root.rglob(file_filter) if file_filter else root.rglob("*")
        for path in iterator:
            if path.is_file() and not path.name.startswith(".") and ".git" not in path.parts:
                # skip binaries
                try:
                    content = path.read_text(encoding="utf-8", errors="ignore")
                except Exception:
                    continue
                for num, line in enumerate(content.splitlines(), 1):
                    if compiled.search(line):
                        results.append(f"{path}:{num}: {line.strip()[:120]}")
                        if len(results) >= max_results:
                            break
            if len(results) >= max_results:
                break
    except Exception as e:
        return f"search failed: {e}"

    if not results:
        return f"(no matches for '{query}' in {root})"
    return "\n".join(results)


# ── Conversation history search (like pi's session search) ───────────────


def search_chat_history(query: str, max_results: int = 10) -> str:
    """Search Mira's own past chat conversations for a message/pattern."""
    try:
        import json as _json
        log = Path("memory/interactions.jsonl")
        if not log.exists():
            return "(no chat history yet)"
        compiled = re.compile(query, re.IGNORECASE)
        hits = []
        for line in log.read_text(encoding="utf-8", errors="ignore").splitlines():
            if not line:
                continue
            try:
                entry = _json.loads(line)
            except Exception:
                continue
            msg = entry.get("message", "")[:200]
            ts = entry.get("timestamp", "")[:16]
            role = entry.get("role", "")
            if compiled.search(entry.get("message", "")):
                hits.append(f"[{ts}] {role}: {msg}")
                if len(hits) >= max_results:
                    break
        return "\n".join(hits) if hits else f"(no chat history matches '{query}')"
    except Exception as e:
        return f"chat history search failed: {e}"


# ── Hermes memory search (the pi agent's durable memory files) ────────────


HERMES_MEMORY_DIR = Path("pi/agent/pi-hermes-memory")
HERMES_MEMORY_FILES = ["MEMORY.md", "USER.md", "failures.md"]


def search_hermes_memory(query: str, max_results: int = 10) -> str:
    """Search the pi agent's (hermes') durable memory markdown files.
    Lets Mira consult the same long-term memory the coding agent keeps."""
    try:
        if not HERMES_MEMORY_DIR.exists():
            return "(hermes memory folder not found)"
        compiled = re.compile(query, re.IGNORECASE)
        hits = []
        for name in HERMES_MEMORY_FILES:
            path = HERMES_MEMORY_DIR / name
            if not path.exists():
                continue
            for num, line in enumerate(path.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
                if compiled.search(line):
                    hits.append(f"{name}:{num}: {line.strip()[:150]}")
                    if len(hits) >= max_results:
                        break
            if len(hits) >= max_results:
                break
        return "\n".join(hits) if hits else f"(no hermes memory matches '{query}')"
    except Exception as e:
        return f"hermes memory search failed: {e}"
