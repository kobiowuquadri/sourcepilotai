"""Opt-in real-provider smoke test. Never substitutes fixture results."""

import os

import pytest
from dotenv import load_dotenv

from agent import run_research

load_dotenv()


@pytest.mark.live
@pytest.mark.skipif(os.getenv("RUN_LIVE_TESTS") != "1" or not all(os.getenv(k) for k in ("TAVILY_API_KEY", "GEMINI_API_KEY")),
                    reason="Live test needs both API keys and RUN_LIVE_TESTS=1; makes billable requests.")
def test_live_research():
    events = list(run_research("What is artificial intelligence? Explain it using authoritative sources."))
    report = events[-1]["report"]
    retrieved_urls = {s["url"] for event in events if event["type"] == "sources" for s in event["sources"]}
    assert report["executive_summary"] and report["key_findings"]
    assert report["sources"]
    assert all(source["url"] in retrieved_urls for source in report["sources"])
    assert report["research"]["search_count"] <= 3
