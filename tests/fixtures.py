"""Synthetic provider fixtures. Imported by tests only, never by the app."""

import json
from types import SimpleNamespace
from unittest.mock import Mock

from agent import ResearchSession


def make_case(question="What is artificial intelligence?", *, follow_up=False):
    comparison = "compare" in question.lower()
    current = "this month" in question.lower()
    contents = (
        ["Python supports multiple programming paradigms and has a broad package ecosystem for web development and data analysis.",
         "JavaScript is used in server applications with Node.js and supports asynchronous network operations through its runtime.",
         "Go provides built-in concurrency primitives and compiles programs to native executables for supported platforms."]
        if comparison else
        ["Artificial intelligence systems can perform tasks such as reasoning, learning, and making predictions from data. Evidence quality matters.",
         "Machine learning systems learn patterns from examples and require evaluation on data representative of their intended use.",
         "Evaluation results depend on the tasks, datasets, and conditions used. A single score does not establish general reliability."]
    )
    sources = [{"title": f"Test evidence {i + 1}", "url": f"https://example.org/research/{i + 1}",
                "content": content, "score": 0.9} for i, content in enumerate(contents)]
    claims = [{"text": text, "kind": "evidence", "citations": [{"source_id": i + 1, "quote": text[:85]}]}
              for i, text in enumerate(contents)]
    plan = {"objective": f"Investigate: {question}", "subquestions": ["What does the evidence establish?", "Where is the evidence limited?"],
            "queries": [question, f"Primary sources {question}"], "time_range": "month" if current else "none"}
    review = {"relevant_source_ids": [1, 2], "gaps": ["Need an independent perspective."] if follow_up else [],
              "follow_up_query": "Independent evaluation evidence" if follow_up else "",
              "reasoning": "A targeted search could resolve an evidence gap." if follow_up else "The available sources cover the planned questions."}
    used = claims if follow_up else claims[:2]
    report = {"title": "Test fixture: evidence-backed research", "executive_summary": [claims[0]],
              "key_findings": used, "detailed_analysis": [{"heading": "Evidence overview", "paragraphs": used}],
              "comparisons": [{"option": label, "assessment": claim} for label, claim in zip(["Python", "JavaScript", "Go"], used)] if comparison else [],
              "conflicting_evidence": [], "conclusion": [claims[0]],
              "limitations": ["Synthetic fixtures used for automated testing; this is not a live research result."]}
    responses = [SimpleNamespace(text=json.dumps(item)) for item in [plan, review, report]]
    gemini = SimpleNamespace(models=SimpleNamespace(generate_content=Mock(side_effect=responses)), close=Mock())
    tavily = SimpleNamespace(search=Mock(side_effect=[{"results": sources[:2]}, {"results": sources[:2]}, {"results": sources[2:]}]), session=SimpleNamespace(close=Mock()))
    session = ResearchSession(gemini=gemini, tavily=tavily)
    return session, {"plan": plan, "review": review, "report": report, "sources": sources}
