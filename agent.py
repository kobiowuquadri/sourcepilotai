"""Research workflow backed exclusively by Tavily's Research API.

Tavily plans the research, runs the web searches, analyzes sources, and writes
the cited report. This module streams Tavily's real progress events, falls back
to Tavily's asynchronous task + polling workflow if the stream drops, and turns
the Markdown report into safe, structured blocks with verifiable citations.
"""

from __future__ import annotations

import ipaddress
import json
import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import requests
from tavily import TavilyClient
from tavily.errors import (BadRequestError, ForbiddenError, InvalidAPIKeyError,
                           UsageLimitExceededError)

MAX_QUESTION_LENGTH = 1500
MODELS = ("mini", "pro", "auto")
DEFAULT_MODEL = "mini"
RESEARCH_DEADLINE = 300      # Whole workflow budget, in seconds.
STREAM_IDLE_TIMEOUT = 150    # Longest silence allowed between stream chunks.
POLL_INTERVAL = 3
MAX_POLL_FAILURES = 4


class ResearchError(Exception):
    """A public, safe error, with no provider message or credentials attached."""

    def __init__(self, message: str, code: str = "research_failed", status: int = 502):
        super().__init__(message)
        self.code, self.status = code, status


class StreamInterrupted(Exception):
    """The live stream broke before the report was complete."""


def validate_question(value) -> str:
    if not isinstance(value, str):
        raise ResearchError("Enter a research question to get started.", "invalid_input", 400)
    question = " ".join(re.sub(r"[\x00-\x1f\x7f]", " ", value).split())
    if not 3 <= len(question) <= MAX_QUESTION_LENGTH:
        raise ResearchError(
            f"Enter a question between 3 and {MAX_QUESTION_LENGTH:,} characters.",
            "invalid_input", 400,
        )
    return question


def check_configuration() -> None:
    if not os.getenv("TAVILY_API_KEY", "").strip():
        raise ResearchError(
            "Research is not configured yet. Add TAVILY_API_KEY to the server's .env file, "
            "then restart the server.", "not_configured", 503,
        )


def research_model() -> str:
    model = os.getenv("TAVILY_RESEARCH_MODEL", "").strip().lower()
    return model if model in MODELS else DEFAULT_MODEL


def provider_error(error: Exception) -> ResearchError:
    """Classify Tavily errors without ever forwarding the provider's exception text."""
    status = getattr(getattr(error, "response", None), "status_code", None)
    if isinstance(error, UsageLimitExceededError) or status == 429:
        return ResearchError("Tavily has reached its rate or usage limit. Please try again later.", "rate_limited", 429)
    if isinstance(error, InvalidAPIKeyError) or status == 401:
        return ResearchError("Tavily could not authenticate. Check TAVILY_API_KEY on the server.", "provider_auth", 503)
    if isinstance(error, ForbiddenError) or status in (403, 432, 433):
        return ResearchError("Tavily declined the request. The API key may lack Research access or the account's "
                             "credits are used up.", "provider_auth", 503)
    if isinstance(error, BadRequestError) or status == 400:
        return ResearchError("Tavily could not accept this question. Try rephrasing it.", "invalid_input", 400)
    if isinstance(error, (TimeoutError, requests.Timeout)):
        return ResearchError("Tavily took too long to respond. Please try again.", "timeout", 504)
    return ResearchError("Tavily is unavailable right now. Please try again shortly.", "provider_error", 502)


def transient(error: Exception) -> bool:
    """Network drops, timeouts, and 5xx responses are worth recovering from."""
    if isinstance(error, (UsageLimitExceededError, InvalidAPIKeyError, ForbiddenError, BadRequestError)):
        return False
    status = getattr(getattr(error, "response", None), "status_code", None)
    if status is not None:
        return status >= 500
    return isinstance(error, (requests.RequestException, TimeoutError, ConnectionError, StreamInterrupted))


@dataclass
class ResearchSession:
    tavily: object
    model: str = DEFAULT_MODEL
    started: float = field(default_factory=time.monotonic)
    queries: list[str] = field(default_factory=list)
    sources: dict = field(default_factory=dict)  # canonical URL -> {title, url, domain}
    warnings: list[str] = field(default_factory=list)
    mode: str = "stream"
    credits: int | None = None

    def elapsed(self) -> float:
        return time.monotonic() - self.started

    def remaining(self) -> float:
        remaining = RESEARCH_DEADLINE - self.elapsed()
        if remaining < 1:
            raise ResearchError("Research reached its time limit. Try a more focused question.", "timeout", 504)
        return remaining

    def close(self):
        # Tavily's synchronous SDK owns a requests session.
        session = getattr(self.tavily, "session", None)
        if session is not None:
            session.close()


def create_session() -> ResearchSession:
    check_configuration()
    try:
        client = TavilyClient(api_key=os.environ["TAVILY_API_KEY"].strip())
    except Exception:
        raise ResearchError("Could not initialize Tavily. Check server configuration.", "not_configured", 503) from None
    return ResearchSession(tavily=client, model=research_model())


# --- URLs and sources -------------------------------------------------------

def safe_url(url: str) -> bool:
    if not isinstance(url, str) or len(url) > 2048 or re.search(r"[\s\\\x00-\x1f]", url):
        return False
    try:
        parts = urlsplit(url)
        host = parts.hostname or ""
        if parts.scheme not in ("http", "https") or not host or parts.username or parts.password:
            return False
        if host.lower() == "localhost" or host.endswith((".localhost", ".local")) or "." not in host:
            return False
        try:
            return ipaddress.ip_address(host).is_global
        except ValueError:
            return True
    except ValueError:
        return False


def canonical_url(url: str) -> str:
    """Only used to match/deduplicate. The exact Tavily URL remains the citation URL."""
    parts = urlsplit(url)
    query = [(k, v) for k, v in parse_qsl(parts.query)
             if not k.lower().startswith("utm_") and k.lower() not in ("fbclid", "gclid")]
    return urlunsplit(("https", (parts.hostname or "").lower().removeprefix("www."),
                       parts.path.rstrip("/"), urlencode(sorted(query)), ""))


def domain_of(url: str) -> str:
    return (urlsplit(url).hostname or "").lower().removeprefix("www.")


def clean_text(value, limit: int) -> str:
    return " ".join(value.split())[:limit] if isinstance(value, str) else ""


def add_sources(session: ResearchSession, rows) -> bool:
    """Record sources Tavily reports. Returns True when new sources were added."""
    added = False
    for row in rows if isinstance(rows, list) else []:
        url = row.get("url") if isinstance(row, dict) else None
        if not safe_url(url):
            continue
        key = canonical_url(url)
        if key in session.sources:
            continue
        session.sources[key] = {"url": url, "domain": domain_of(url),
                                "title": clean_text(row.get("title"), 240) or domain_of(url)}
        added = True
    return added


def public_sources(session: ResearchSession) -> list[dict]:
    return [{"id": i + 1, **source} for i, source in enumerate(session.sources.values())]


# --- Report parsing ---------------------------------------------------------

REFERENCE_HEADING = re.compile(r"^\s*(?:#{1,6}\s*)?\**\s*(sources|references|citations|bibliography|works cited)\s*:?\s*\**\s*:?\s*$", re.I)
REFERENCE_LINE = re.compile(r"^\s*(?:[-*+]\s*)?\[(\d{1,3})\]\s*(.+?)\s*$")
URL_IN_TEXT = re.compile(r"https?://[^\s<>()\[\]\"']+")
INLINE = re.compile(
    r"(?P<strong>\*\*(?P<strong_text>.+?)\*\*|__(?P<strong_text2>.+?)__)"
    r"|(?P<code>`(?P<code_text>[^`]+)`)"
    r"|(?P<link>\[(?P<link_text>[^\]]+)\]\((?P<link_url>[^)\s]+)\))"
    r"|(?P<cite>\[(?P<cite_ids>\d{1,3}(?:\s*[,–-]\s*\d{1,3})*)\])"
    r"|(?P<em>(?<![\w*])\*(?=\S)(?P<em_text>[^*]+?)(?<=\S)\*(?![\w*]))"
)


def citation_ids(text: str) -> list[int]:
    ids = []
    for part in re.split(r"\s*,\s*", text):
        bounds = [int(n) for n in re.split(r"\s*[–-]\s*", part) if n]
        if len(bounds) == 2 and 0 < bounds[1] - bounds[0] <= 20:
            ids.extend(range(bounds[0], bounds[1] + 1))
        else:
            ids.extend(bounds)
    return list(dict.fromkeys(ids))


def inline(text: str) -> list[dict]:
    """Tokenize inline Markdown into plain-text segments. No HTML is produced."""
    segments, position = [], 0

    def push_text(value):
        if value:
            if segments and segments[-1]["t"] == "text":
                segments[-1]["v"] += value
            else:
                segments.append({"t": "text", "v": value})

    for match in INLINE.finditer(text):
        push_text(text[position:match.start()])
        position = match.end()
        if match["strong"]:
            segments.append({"t": "strong", "c": inline(match["strong_text"] or match["strong_text2"])})
        elif match["code"]:
            segments.append({"t": "code", "v": match["code_text"]})
        elif match["link"]:
            url = match["link_url"]
            if safe_url(url):
                segments.append({"t": "link", "v": match["link_text"], "url": url})
            else:
                push_text(match["link_text"])
        elif match["cite"]:
            segments.append({"t": "cite", "ids": citation_ids(match["cite_ids"])})
        elif match["em"]:
            segments.append({"t": "em", "c": inline(match["em_text"])})
    push_text(text[position:])
    return segments


def split_references(lines: list[str]) -> tuple[list[str], dict[int, dict]]:
    """Separate the trailing numbered reference list from the report body."""
    start = None
    for index in range(len(lines) - 1, -1, -1):
        if REFERENCE_HEADING.match(lines[index]):
            start = index
            break
    references = {}
    if start is None:
        # Some reports end with a bare numbered URL list and no heading.
        index = len(lines)
        while index > 0 and (not lines[index - 1].strip() or REFERENCE_LINE.match(lines[index - 1])):
            index -= 1
        if any(REFERENCE_LINE.match(line) and URL_IN_TEXT.search(line) for line in lines[index:]):
            start = index
    if start is None:
        return lines, references
    for line in lines[start:]:
        match = REFERENCE_LINE.match(line)
        url_match = URL_IN_TEXT.search(line)
        if not match or not url_match:
            continue
        url = url_match.group(0).rstrip(".,;:")
        label = match.group(2).replace(url_match.group(0), " ")
        label = re.sub(r"\[([^\]]*)\]\(\s*\)", r"\1", label)
        label = clean_text(re.sub(r"[\s\-–—:|,()<>*_]+$|^[\s\-–—:|,()<>*_]+", "", label), 240)
        references[int(match.group(1))] = {"url": url, "label": label}
    return lines[:start], references


def table_cells(line: str) -> list[str]:
    line = line.strip()
    if line.startswith("|"):
        line = line[1:]
    if line.endswith("|") and not line.endswith("\\|"):
        line = line[:-1]
    return [cell.strip().replace("\\|", "|") for cell in re.split(r"(?<!\\)\|", line)]


def parse_blocks(lines: list[str]) -> list[dict]:
    blocks, paragraph, index = [], [], 0

    def flush():
        if paragraph:
            blocks.append({"type": "paragraph", "content": inline(" ".join(paragraph))})
            paragraph.clear()

    while index < len(lines):
        line = lines[index]
        stripped = line.strip()
        heading = re.match(r"^(#{1,6})\s+(.+?)\s*#*\s*$", stripped)
        list_item = re.match(r"^(\s*)([-*+]|\d{1,3}[.)])\s+(.+)$", line)
        if not stripped:
            flush()
        elif stripped.startswith("```"):
            flush()
            code, index = [], index + 1
            while index < len(lines) and not lines[index].strip().startswith("```"):
                code.append(lines[index])
                index += 1
            blocks.append({"type": "code", "text": "\n".join(code)})
        elif heading:
            flush()
            blocks.append({"type": "heading", "level": min(max(len(heading.group(1)), 2), 4),
                           "content": inline(heading.group(2))})
        elif re.fullmatch(r"(?:[-*_]\s*){3,}", stripped):
            flush()
        elif stripped.startswith("|") and index + 1 < len(lines) and re.fullmatch(r"\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?", lines[index + 1].strip()):
            flush()
            header = table_cells(stripped)
            rows, index = [], index + 2
            while index < len(lines) and lines[index].strip().startswith("|"):
                cells = table_cells(lines[index])
                rows.append([inline(cell) for cell in (cells + [""] * len(header))[:len(header)]])
                index += 1
            blocks.append({"type": "table", "header": [inline(cell) for cell in header], "rows": rows})
            continue
        elif list_item:
            flush()
            ordered = list_item.group(2)[0].isdigit()
            items = []
            while index < len(lines):
                item = re.match(r"^(\s*)([-*+]|\d{1,3}[.)])\s+(.+)$", lines[index])
                if item and item.group(2)[0].isdigit() == ordered:
                    level = min(len(item.group(1).replace("\t", "    ")) // 2, 2)
                    items.append({"level": level, "content": inline(item.group(3))})
                elif item:
                    break
                elif lines[index].strip() and items and lines[index].startswith((" ", "\t")):
                    items[-1]["content"] += inline(" " + lines[index].strip())
                else:
                    break
                index += 1
            blocks.append({"type": "list", "ordered": ordered, "items": items})
            continue
        elif stripped.startswith(">"):
            flush()
            quote = []
            while index < len(lines) and lines[index].strip().startswith(">"):
                quote.append(lines[index].strip().lstrip(">").strip())
                index += 1
            blocks.append({"type": "quote", "content": inline(" ".join(quote))})
            continue
        else:
            paragraph.append(stripped)
        index += 1
    flush()
    return blocks


def walk_citations(value):
    if isinstance(value, dict):
        if value.get("t") == "cite":
            yield from value["ids"]
        for child in value.values():
            yield from walk_citations(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk_citations(child)


def build_report(question: str, content: str, session: ResearchSession) -> dict:
    if not isinstance(content, str) or len(content.strip()) < 40:
        raise ResearchError("Tavily returned an empty report. Please try again or rephrase your question.", "invalid_response")
    lines = content.replace("\r\n", "\n").replace("\r", "\n").strip().split("\n")
    title = question
    first = next((i for i, line in enumerate(lines) if line.strip()), None)
    if first is not None and re.match(r"^#\s+\S", lines[first].strip()):
        title = clean_text(re.sub(r"[*_`]", "", lines[first].strip()[1:]), 180) or question
        lines = lines[first + 1:]
    body_lines, references = split_references(lines)
    sources = []
    for number, reference in sorted(references.items()):
        if not safe_url(reference["url"]):
            continue
        known = session.sources.get(canonical_url(reference["url"]))
        label = reference["label"]
        title_text = (known or {}).get("title") or (label if label and not URL_IN_TEXT.search(label) else "") or domain_of(reference["url"])
        sources.append({"id": number, "url": reference["url"], "domain": domain_of(reference["url"]),
                        "title": title_text, "retrieved": known is not None})
    blocks = parse_blocks(body_lines)
    if not any(block["type"] in ("paragraph", "list", "table") for block in blocks):
        raise ResearchError("Tavily returned an incomplete report. Please try again.", "invalid_response")
    cited = set(walk_citations(blocks))
    valid_ids = {source["id"] for source in sources}
    if not sources and session.sources:
        # No numbered reference list could be read, so in-text numbers cannot be
        # matched safely. List every source Tavily consulted, unnumbered.
        sources = [{"id": None, **source, "retrieved": True} for source in session.sources.values()]
        session.warnings.append("Citation numbers could not be matched to the reference list. "
                                "All sources Tavily consulted are listed instead.")
    elif cited - valid_ids:
        session.warnings.append(f"{len(cited - valid_ids)} citation number(s) had no matching reference and are shown as plain text.")
    return {
        "title": title,
        "question": question,
        "blocks": blocks,
        "markdown": "\n".join(body_lines).strip(),
        "sources": sources,
        "limitations": list(session.warnings),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "research": {
            "provider": "Tavily Research API", "model": session.model, "mode": session.mode,
            "queries": session.queries, "search_count": len(session.queries),
            "consulted_source_count": len(session.sources), "cited_source_count": len(valid_ids & cited) or len(sources),
            "elapsed_seconds": round(session.elapsed(), 1), "credits": session.credits,
        },
    }


# --- Tavily workflows -------------------------------------------------------

def progress(stage: str, message: str, **data) -> dict:
    return {"type": "progress", "stage": stage, "message": message, **data}


def sse_events(chunks):
    """Parse Server-Sent Events from Tavily's byte stream."""
    buffer = ""
    for chunk in chunks:
        buffer += chunk.decode("utf-8", errors="replace") if isinstance(chunk, bytes) else str(chunk)
        buffer = buffer.replace("\r\n", "\n")
        if len(buffer) > 5_000_000:
            raise StreamInterrupted("Stream event too large")
        while "\n\n" in buffer:
            raw, buffer = buffer.split("\n\n", 1)
            name, data = "message", []
            for line in raw.split("\n"):
                if line.startswith("event:"):
                    name = line[6:].strip()
                elif line.startswith("data:"):
                    data.append(line[5:].lstrip())
            if name or data:
                yield name, "\n".join(data)
    if buffer.strip():
        yield "message", buffer.strip().removeprefix("data:").strip()


TOOL_STAGES = {"planning": "planning", "websearch": "searching", "search": "searching",
               "extract": "reviewing", "crawl": "reviewing", "generating": "analyzing"}


def stream_research(question: str, session: ResearchSession):
    """Yield UI events from Tavily's live stream. Returns the report Markdown."""
    try:
        chunks = session.tavily.research(input=question, model=session.model, stream=True,
                                         citation_format="numbered", timeout=STREAM_IDLE_TIMEOUT)
    except Exception as error:
        if transient(error):
            raise StreamInterrupted() from None
        raise provider_error(error) from None
    content, finished, writing = [], False, False
    try:
        for name, data in sse_events(chunks):
            session.remaining()
            if name == "done" or data == "[DONE]":
                finished = True
                break
            if name == "error":
                raise ResearchError("Tavily could not complete this research. Please try again.", "provider_error")
            try:
                payload = json.loads(data)
            except ValueError:
                continue
            if not isinstance(payload, dict):
                continue
            if payload.get("error"):
                raise ResearchError("Tavily could not complete this research. Please try again.", "provider_error")
            choices = payload.get("choices") or [{}]
            delta = choices[0].get("delta", {}) if isinstance(choices[0], dict) else {}
            calls = delta.get("tool_calls")
            if isinstance(calls, dict):
                kind = calls.get("type")
                for call in calls.get(kind) or []:
                    if not isinstance(call, dict):
                        continue
                    tool = clean_text(call.get("name"), 60)
                    stage = TOOL_STAGES.get(tool.lower().replace("_", ""), "searching")
                    detail = clean_text(call.get("arguments"), 200)
                    if kind == "tool_call":
                        queries = [clean_text(q, 400) for q in call.get("queries") or [] if isinstance(q, str) and q.strip()]
                        session.queries.extend(q for q in queries if q not in session.queries)
                        if stage == "planning":
                            yield progress("planning", "Tavily is planning the research.")
                        elif stage == "analyzing":
                            yield progress("analyzing", "Analyzing the evidence and drafting the report.",
                                           count=len(session.sources))
                        elif queries:
                            yield progress("searching", f"Searching the web with {len(queries)} "
                                           f"quer{'y' if len(queries) == 1 else 'ies'}.", queries=queries)
                        else:
                            yield progress(stage, detail or f"Running {tool or 'a research step'}.")
                    elif kind == "tool_response":
                        if add_sources(session, call.get("sources")):
                            yield {"type": "sources", "sources": public_sources(session), "count": len(session.sources)}
                            yield progress("reviewing", f"Reviewing {len(session.sources)} sources found so far.")
            if add_sources(session, delta.get("sources")):
                yield {"type": "sources", "sources": public_sources(session), "count": len(session.sources)}
            text = delta.get("content")
            if isinstance(text, str) and text:
                if not writing:
                    writing = True
                    yield progress("preparing", "Writing your cited report.")
                content.append(text)
    except ResearchError:
        raise
    except Exception as error:
        if transient(error):
            raise StreamInterrupted() from None
        raise provider_error(error) from None
    finally:
        close = getattr(chunks, "close", None)
        if close:
            close()
    report = "".join(content)
    if not finished and len(report.strip()) < 40:
        raise StreamInterrupted()
    if not finished:
        session.warnings.append("Tavily's stream ended without a completion signal; the report may be incomplete.")
    return report


def poll_research(question: str, session: ResearchSession):
    """Tavily's asynchronous workflow: create a task, then poll until it finishes."""
    session.mode = "poll"
    try:
        task = session.tavily.research(input=question, model=session.model,
                                       citation_format="numbered", timeout=30)
    except Exception as error:
        raise provider_error(error) from None
    request_id = task.get("request_id") if isinstance(task, dict) else None
    if not isinstance(request_id, str) or not request_id:
        raise ResearchError("Tavily did not return a research task. Please try again.", "invalid_response")
    yield progress("planning", "Research task queued with Tavily.")
    failures, last_status, last_update = 0, None, 0.0
    while True:
        time.sleep(min(POLL_INTERVAL, session.remaining()))
        session.remaining()
        try:
            result = session.tavily.get_research(request_id, include_usage=True)
            failures = 0
        except Exception as error:
            failures += 1
            if not transient(error) and getattr(getattr(error, "response", None), "status_code", None):
                raise provider_error(error) from None
            if failures >= MAX_POLL_FAILURES:
                raise ResearchError("Lost contact with Tavily while research was running. Please try again.",
                                    "provider_error") from None
            continue
        status = result.get("status") if isinstance(result, dict) else None
        if status == "completed":
            add_sources(session, result.get("sources"))
            if session.sources:
                yield {"type": "sources", "sources": public_sources(session), "count": len(session.sources)}
            usage = result.get("usage")
            if isinstance(usage, dict) and isinstance(usage.get("credits"), (int, float)):
                session.credits = int(usage["credits"])
            yield progress("preparing", "Tavily finished. Preparing your cited report.")
            content = result.get("content")
            if isinstance(content, dict):
                content = json.dumps(content, ensure_ascii=False, indent=2)
            return content
        if status == "failed":
            raise ResearchError("Tavily could not complete this research. Please try again or rephrase.", "provider_error")
        if status != last_status or time.monotonic() - last_update > 15:
            last_status, last_update = status, time.monotonic()
            elapsed = round(session.elapsed())
            if status == "in_progress":
                yield progress("searching", f"Tavily is searching and analyzing sources ({elapsed}s elapsed).")
            else:
                yield progress("planning", f"Waiting for Tavily to start the research ({elapsed}s elapsed).")


def run_research(question: str, session: ResearchSession | None = None):
    """Yield real workflow events and a final result. No simulated progress."""
    question = validate_question(question)
    owned = session is None
    session = session or create_session()
    try:
        yield progress("understanding", "Sending your question to Tavily Research.")
        try:
            content = yield from stream_research(question, session)
        except StreamInterrupted:
            session.remaining()
            session.warnings.append("The live progress stream was interrupted, so the research was rerun as a "
                                    "background Tavily task.")
            yield {"type": "notice", "message": "Live updates were interrupted. Continuing with a background "
                                                "Tavily research task."}
            session.queries.clear()
            session.sources.clear()
            content = yield from poll_research(question, session)
        result = build_report(question, content, session)
        yield {"type": "result", "report": result}
    finally:
        if owned:
            session.close()
