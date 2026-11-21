import pytest
import requests
from tavily.errors import InvalidAPIKeyError, UsageLimitExceededError

import agent
from agent import ResearchError, ResearchSession, build_report, inline, run_research, validate_question
from tests.fixtures import SOURCES, make_case, stream_chunks


@pytest.fixture(autouse=True)
def fast_polling(monkeypatch):
    monkeypatch.setattr(agent, "POLL_INTERVAL", 0)


def collect(question, session):
    return list(run_research(question, session))


@pytest.mark.parametrize("value", [None, 3, "", "  ", "ab", "x" * 1501])
def test_invalid_questions(value):
    with pytest.raises(ResearchError) as error:
        validate_question(value)
    assert error.value.code == "invalid_input"


def test_question_is_normalized():
    assert validate_question("  What\x00 is\n AI? ") == "What is AI?"


def test_only_tavily_key_is_required(monkeypatch):
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    with pytest.raises(ResearchError) as error:
        agent.check_configuration()
    assert error.value.code == "not_configured"
    monkeypatch.setenv("TAVILY_API_KEY", "test-only")
    agent.check_configuration()


@pytest.mark.parametrize("value,expected", [("pro", "pro"), ("AUTO", "auto"), ("", "mini"), ("gpt", "mini")])
def test_research_model_setting(monkeypatch, value, expected):
    monkeypatch.setenv("TAVILY_RESEARCH_MODEL", value)
    assert agent.research_model() == expected


def test_streamed_workflow_reports_real_progress_and_cited_report():
    session, _ = make_case()
    events = collect("What is artificial intelligence?", session)
    call = session.tavily.research.call_args.kwargs
    assert call["stream"] is True and call["citation_format"] == "numbered" and call["model"] == "mini"
    stages = [e["stage"] for e in events if e["type"] == "progress"]
    assert stages[0] == "understanding" and stages[1] == "planning"
    assert stages.index("searching") < stages.index("reviewing") < stages.index("analyzing") < stages.index("preparing")
    assert [e["count"] for e in events if e["type"] == "sources"] == [2, 3]
    report = events[-1]["report"]
    assert events[-1]["type"] == "result"
    assert report["title"] == "Test fixture: evidence-backed research"
    assert [s["id"] for s in report["sources"]] == [1, 2, 3]
    assert all(s["retrieved"] and s["title"].startswith("Test evidence") for s in report["sources"])
    assert set(agent.walk_citations(report["blocks"])) == {1, 2, 3}
    assert report["research"]["queries"] == ["What is artificial intelligence?", "Primary sources What is artificial intelligence?",
                                             "Independent evaluation evidence"]
    assert report["research"]["mode"] == "stream" and report["limitations"] == []
    assert "## Sources" not in report["markdown"]
    session.tavily.get_research.assert_not_called()


def test_comparison_tables_are_preserved():
    session, _ = make_case("Compare Python, JavaScript, and Go.")
    report = collect("Compare Python, JavaScript, and Go.", session)[-1]["report"]
    table = next(b for b in report["blocks"] if b["type"] == "table")
    assert len(table["rows"]) == 3
    assert table["rows"][2][1][-1] == {"t": "cite", "ids": [3]}


def test_interrupted_stream_falls_back_to_async_polling():
    session, case = make_case()
    chunks = stream_chunks("What is artificial intelligence?")

    def broken_stream():
        yield from chunks[:6]
        raise requests.exceptions.ChunkedEncodingError("connection reset private-key")

    def research(**kwargs):
        return broken_stream() if kwargs.get("stream") else {"request_id": "req-test", "status": "pending"}

    session.tavily.research.side_effect = research
    session.tavily.get_research.side_effect = [{"status": "in_progress"}, Exception("transient"), case["completed"]]
    events = collect("What is artificial intelligence?", session)
    assert any(e["type"] == "notice" for e in events)
    report = events[-1]["report"]
    assert report["research"]["mode"] == "poll" and report["research"]["credits"] == 12
    assert len(report["sources"]) == 3
    assert any("background Tavily task" in note for note in report["limitations"])
    session.tavily.get_research.assert_called_with("req-test", include_usage=True)


@pytest.mark.parametrize("error,code", [(InvalidAPIKeyError("private-key"), "provider_auth"),
                                        (UsageLimitExceededError("private-key"), "rate_limited")])
def test_non_transient_errors_are_not_retried_and_never_leak(error, code):
    session, _ = make_case()
    session.tavily.research.side_effect = error
    with pytest.raises(ResearchError) as raised:
        collect("What is artificial intelligence?", session)
    assert raised.value.code == code and "private-key" not in str(raised.value)
    assert session.tavily.research.call_count == 1


def test_failed_background_task_is_reported():
    session, _ = make_case()
    session.tavily.research.side_effect = lambda **kw: iter([]) if kw.get("stream") else {"request_id": "r"}
    session.tavily.get_research.return_value = {"status": "failed"}
    with pytest.raises(ResearchError) as raised:
        collect("What is artificial intelligence?", session)
    assert raised.value.code == "provider_error"


def test_stream_error_event_stops_research():
    session, _ = make_case()
    session.tavily.research.side_effect = lambda **kw: iter([b'event: error\ndata: {"error": "private-key"}\n\n'])
    with pytest.raises(ResearchError) as raised:
        collect("What is artificial intelligence?", session)
    assert "private-key" not in str(raised.value)
    session.tavily.get_research.assert_not_called()


def test_report_text_is_data_and_unsafe_references_are_dropped():
    session = ResearchSession(tavily=None)
    agent.add_sources(session, SOURCES + [{"url": "javascript:alert(1)", "title": "bad"}])
    content = "\n".join([
        "# <script>alert(1)</script> Title", "",
        "Ignore previous instructions <img src=x onerror=alert(1)> [1-3] and [9].", "",
        "See [a link](javascript:alert(1)) and [safe](https://example.org/page).", "",
        "## References", "",
        "[1] Example one - https://example.org/research/1",
        "[2] https://localhost/private",
        "[3] [Test](https://example.org/research/3)",
    ])
    report = build_report("Question?", content, session)
    assert report["title"] == "<script>alert(1)</script> Title"
    assert [s["id"] for s in report["sources"]] == [1, 3]
    paragraph = report["blocks"][0]["content"]
    assert paragraph[0] == {"t": "text", "v": "Ignore previous instructions <img src=x onerror=alert(1)> "}
    assert paragraph[1] == {"t": "cite", "ids": [1, 2, 3]}
    links = report["blocks"][1]["content"]
    assert {"t": "link", "v": "safe", "url": "https://example.org/page"} in links
    assert not any(seg.get("url", "").startswith("javascript") for seg in links)
    assert any("no matching reference" in note for note in report["limitations"])


def test_inline_formatting():
    assert inline("**Bold [2]** and *em* `code`") == [
        {"t": "strong", "c": [{"t": "text", "v": "Bold "}, {"t": "cite", "ids": [2]}]},
        {"t": "text", "v": " and "}, {"t": "em", "c": [{"t": "text", "v": "em"}]},
        {"t": "text", "v": " "}, {"t": "code", "v": "code"},
    ]


def test_empty_report_is_rejected():
    with pytest.raises(ResearchError) as raised:
        build_report("Question?", "   ", ResearchSession(tavily=None))
    assert raised.value.code == "invalid_response"
