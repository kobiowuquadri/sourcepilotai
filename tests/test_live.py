"""Opt-in real-provider smoke test. Never substitutes fixture results."""

import json
import os

import pytest
from dotenv import load_dotenv

from app import create_app

load_dotenv()


@pytest.mark.live
@pytest.mark.skipif(os.getenv("RUN_LIVE_TESTS") != "1" or not os.getenv("TAVILY_API_KEY"),
                    reason="Live test needs TAVILY_API_KEY and RUN_LIVE_TESTS=1; uses Tavily research credits.")
def test_live_research():
    with create_app().test_client() as client:
        response = client.post("/api/research", json={
            "question": "What is artificial intelligence? Explain it using authoritative sources.",
        })
        assert response.status_code == 200
        assert response.mimetype == "application/x-ndjson"
        events = [json.loads(line) for line in response.data.splitlines()]
    assert events[-1]["type"] == "result", events[-1]
    stages = {event["stage"] for event in events if event["type"] == "progress"}
    assert {"planning", "searching", "preparing"} <= stages
    report = events[-1]["report"]
    assert report["blocks"] and report["sources"]
    assert any(block["type"] == "heading" for block in report["blocks"])
    assert all(source["url"].startswith("http") for source in report["sources"])
    cited = {seg_id for block in report["blocks"] for seg_id in _cites(block)}
    assert cited and cited <= {source["id"] for source in report["sources"]}


def _cites(value):
    if isinstance(value, dict):
        if value.get("t") == "cite":
            yield from value["ids"]
        for child in value.values():
            yield from _cites(child)
    elif isinstance(value, list):
        for child in value:
            yield from _cites(child)
