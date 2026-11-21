"""Synthetic Tavily Research fixtures in the real SSE format. Tests only, never the app."""

import json
from types import SimpleNamespace
from unittest.mock import Mock

from agent import ResearchSession

SOURCES = [{"url": f"https://example.org/research/{i}", "title": f"Test evidence {i}",
            "favicon": "https://example.org/favicon.ico"} for i in (1, 2, 3)]


def sse(delta: dict) -> bytes:
    payload = {"id": "chunk", "object": "chat.completion.chunk", "model": "mini", "created": 0,
               "choices": [{"delta": {"role": "assistant", **delta}}]}
    return f"event: chat.completion.chunk\ndata: {json.dumps(payload)}\n\n".encode()


def tool(kind: str, name: str, arguments: str, **extra) -> bytes:
    return sse({"tool_calls": {"type": kind, kind: [{"id": f"{name}-id", "name": name, "arguments": arguments, **extra}]}})


def report_markdown(question: str) -> str:
    lines = ["# Test fixture: evidence-backed research", "",
             "An overview paragraph with **bold** text supported by evidence [1].", "",
             "## Key findings", "",
             "- First finding with two citations [1], [2].",
             "- Second finding from an independent source [3].", ""]
    if "compare" in question.lower():
        lines += ["## Comparison", "", "| Option | Strength |", "|---|---|",
                  "| Python | Broad ecosystem [1] |", "| JavaScript | Asynchronous I/O [2] |",
                  "| Go | Built-in concurrency [3] |", ""]
    lines += ["## Conclusion", "", "Synthetic fixture used for automated testing; not a live result [2].", "",
              "## Sources", ""] + [f"[{i}] {source['url']}" for i, source in enumerate(SOURCES, 1)]
    return "\n".join(lines)


def stream_chunks(question: str, chunk_size: int = 53) -> list[bytes]:
    markdown = report_markdown(question)
    events = [
        tool("tool_call", "Planning", "Initializing research plan..."),
        tool("tool_call", "WebSearch", "Executing 2 search queries", queries=[question, f"Primary sources {question}"]),
        tool("tool_response", "WebSearch", "Completed search", sources=SOURCES[:2]),
        tool("tool_response", "Planning", "Research plan initialized"),
        tool("tool_call", "WebSearch", "Executing 1 search query", queries=["Independent evaluation evidence"]),
        tool("tool_response", "WebSearch", "Completed search", sources=SOURCES[2:]),
        tool("tool_call", "Generating", "Generating final report..."),
        tool("tool_response", "Generating", "Final report generated"),
        *[sse({"content": markdown[i:i + 90]}) for i in range(0, len(markdown), 90)],
        sse({"sources": SOURCES}),
        b"event: done\n\n",
    ]
    raw = b"".join(events)
    # Awkward boundaries prove the SSE parser buffers partial events correctly.
    return [raw[i:i + chunk_size] for i in range(0, len(raw), chunk_size)]


def make_case(question="What is artificial intelligence?"):
    def research(**kwargs):
        if kwargs.get("stream"):
            return iter(stream_chunks(kwargs["input"]))
        return {"request_id": "req-test", "status": "pending", "input": kwargs["input"], "model": kwargs.get("model")}

    completed = {"request_id": "req-test", "status": "completed", "content": report_markdown(question),
                 "sources": SOURCES, "usage": {"credits": 12}}
    tavily = SimpleNamespace(research=Mock(side_effect=research), get_research=Mock(return_value=completed),
                             session=SimpleNamespace(close=Mock()))
    session = ResearchSession(tavily=tavily, model="mini")
    return session, {"markdown": report_markdown(question), "sources": SOURCES, "completed": completed}
