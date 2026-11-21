import json
from threading import Event

import pytest

import agent
import app as webapp
from tests.fixtures import make_case


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-only-not-a-real-key")
    monkeypatch.setenv("TAVILY_API_KEY", "test-only-not-a-real-key")
    monkeypatch.setattr(agent, "create_session", lambda: make_case()[0])
    app = webapp.create_app()
    app.testing = True
    return app.test_client()


def test_home_health_and_security_headers(client):
    response = client.get("/")
    assert response.status_code == 200
    assert b"Where would you like to begin?" in response.data
    assert "script-src 'self'" in response.headers["Content-Security-Policy"]
    assert client.get("/api/health").json == {"status": "ok", "research_available": True}


@pytest.mark.parametrize("payload", [{}, {"question": ""}, {"question": ["wrong"]}, [], None])
def test_invalid_requests(client, payload):
    response = client.post("/api/research", json=payload)
    assert response.status_code == 400
    assert response.json["code"] == "invalid_input"


def test_oversized_request(client):
    assert client.post("/api/research", json={"question": "a" * 17000}).status_code == 413


def test_missing_keys_are_not_a_fake_demo(client, monkeypatch):
    monkeypatch.delenv("TAVILY_API_KEY")
    response = client.post("/api/research", json={"question": "What is AI?"})
    assert response.status_code == 503
    assert response.json["code"] == "not_configured"
    assert client.get("/api/health").json["research_available"] is False


def test_stream_contains_progress_and_cited_report(client):
    response = client.post("/api/research", json={"question": "What is artificial intelligence?"})
    assert response.mimetype == "application/x-ndjson"
    events = [json.loads(line) for line in response.data.splitlines()]
    assert events[0]["stage"] == "understanding"
    assert events[-1]["type"] == "result"
    assert len(events[-1]["report"]["sources"]) == 2
    for event in events:
        if event["type"] == "sources":
            assert all("content" not in source for source in event["sources"])
    assert b"test-only-not-a-real-key" not in response.data


@pytest.mark.parametrize("provider", ["gemini", "tavily"])
def test_streamed_provider_failure(client, monkeypatch, provider):
    session, _ = make_case()
    if provider == "gemini":
        session.gemini.models.generate_content.side_effect = TimeoutError("private-key")
    else:
        session.tavily.search.side_effect = ConnectionError("private-key")
    monkeypatch.setattr(agent, "create_session", lambda: session)
    response = client.post("/api/research", json={"question": "What is artificial intelligence?"})
    events = [json.loads(line) for line in response.data.splitlines()]
    assert events[-1]["type"] == "error"
    assert b"private-key" not in response.data
    assert not any(event["type"] == "result" for event in events)


def test_concurrency_is_bounded_and_slots_released(client, monkeypatch):
    release = Event()

    def blocked_run(_question):
        yield {"type": "progress", "stage": "planning", "message": "Planning"}
        release.wait(10)

    monkeypatch.setattr(webapp, "run_research", blocked_run)
    first = client.post("/api/research", json={"question": "First question"}, buffered=False)
    second = client.post("/api/research", json={"question": "Second question"}, buffered=False)
    try:
        third = client.post("/api/research", json={"question": "Third question"})
        assert third.status_code == 429
    finally:
        release.set()
        first.get_data()
        second.get_data()
        first.close()
        second.close()
    assert client.post("/api/research", json={"question": "Fourth question"}).status_code == 200
