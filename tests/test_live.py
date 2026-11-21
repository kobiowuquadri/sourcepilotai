"""Opt-in real-provider smoke test. Never substitutes fixture results."""

import json
import os

import pytest
from dotenv import load_dotenv

from app import create_app

load_dotenv()


@pytest.mark.live
@pytest.mark.skipif(os.getenv("RUN_LIVE_TESTS") != "1" or not all(os.getenv(k) for k in ("TAVILY_API_KEY", "GEMINI_API_KEY")),
                    reason="Live test needs both API keys and RUN_LIVE_TESTS=1; makes billable requests.")
def test_live_research():
    with create_app().test_client() as client:
        response = client.post("/api/research", json={
            "question": "What is artificial intelligence? Explain it using authoritative sources.",
        })
        assert response.status_code == 200
        assert response.mimetype == "application/x-ndjson"
        events = [json.loads(line) for line in response.data.splitlines()]
    assert events[-1]["type"] == "result", events[-1]
    report = events[-1]["report"]
    retrieved_urls = {s["url"] for event in events if event["type"] == "sources" for s in event["sources"]}
    assert report["executive_summary"] and report["key_findings"]
    assert report["sources"]
    assert all(source["url"] in retrieved_urls for source in report["sources"])
    assert report["research"]["search_count"] <= 3
