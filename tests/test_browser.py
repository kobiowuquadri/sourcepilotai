"""Real Chromium UI checks with mocked providers and the real Flask/agent flow."""

import os
from pathlib import Path
from threading import Event, Thread

import pytest
from werkzeug.serving import make_server

import agent
from app import create_app
from tests.fixtures import make_case

pytestmark = pytest.mark.browser


@pytest.fixture
def browser_app(monkeypatch):
    playwright = pytest.importorskip("playwright.sync_api")
    monkeypatch.setenv("GEMINI_API_KEY", "test-only")
    monkeypatch.setenv("TAVILY_API_KEY", "test-only")
    monkeypatch.setattr(agent, "create_session", lambda: make_case("Compare Python, JavaScript, and Go for backend development.", follow_up=True)[0])
    server = make_server("127.0.0.1", 0, create_app(), threaded=True)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    artifacts = Path("test-results")
    artifacts.mkdir(exist_ok=True)
    candidates = [os.getenv("BROWSER_EXECUTABLE", ""),
                  "C:/Program Files/Google/Chrome/Application/chrome.exe",
                  "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"]
    executable = next((p for p in candidates if p and Path(p).is_file()), None)
    try:
        with playwright.sync_playwright() as engine:
            try:
                browser = engine.chromium.launch(executable_path=executable, headless=True)
            except playwright.Error as error:
                pytest.skip(f"Chromium unavailable: {type(error).__name__}. Set BROWSER_EXECUTABLE or run playwright install chromium.")
            yield browser, f"http://127.0.0.1:{server.server_port}", artifacts
            browser.close()
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def assert_no_overflow(page):
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), "Horizontal overflow"


def test_desktop_research_copy_citations_and_pdf(browser_app):
    browser, url, artifacts = browser_app
    context = browser.new_context(viewport={"width": 1440, "height": 1050}, permissions=["clipboard-read", "clipboard-write"])
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(url, wait_until="networkidle")
    page.screenshot(path=str(artifacts / "desktop-home.png"), full_page=True)
    assert_no_overflow(page)
    page.locator("#start-button").click()
    assert page.locator("#form-error").is_visible()
    page.locator(".example").nth(1).click()
    assert "Compare Python" in page.locator("#question").input_value()
    page.locator("#start-button").click()
    page.locator("#report-view").wait_for(state="visible", timeout=20000)
    assert page.locator("#report-source-count").inner_text() == "3"
    assert page.locator(".comparison-table tbody tr").count() == 3
    page.locator("#sources-toggle").click()
    assert not page.locator("#sources-panel").is_visible()
    assert page.locator("#sources-toggle").get_attribute("aria-expanded") == "false"
    for citation in page.locator(".citation").all():
        assert page.locator(citation.get_attribute("href")).count() == 1
    page.locator(".citation").first.click()
    assert page.evaluate("location.hash") == "#source-1"
    assert page.locator("#sources-panel").is_visible()
    assert page.locator("#sources-toggle").get_attribute("aria-expanded") == "true"
    page.locator("#copy-button").click()
    page.wait_for_function("document.querySelector('#toast').textContent.includes('Report copied')")
    copied = page.evaluate("navigator.clipboard.readText()")
    assert "SOURCES & REFERENCES" in copied and "https://example.org/research/1" in copied
    page.evaluate("window.print = () => { window.__printed = true; }")
    page.locator("#print-button").click()
    assert page.evaluate("window.__printed") is True
    page.screenshot(path=str(artifacts / "desktop-report.png"), full_page=True)
    page.locator("#sources-close").click()
    assert not page.locator("#sources-panel").is_visible()
    page.pdf(path=str(artifacts / "research-print.pdf"), format="A4", print_background=True)
    assert (artifacts / "research-print.pdf").stat().st_size > 5000
    page.emulate_media(media="print")
    assert not page.locator(".report-toolbar").is_visible()
    assert page.locator(".print-url").first.is_visible()
    page.emulate_media(media="screen")
    assert not page.locator("#sources-panel").is_visible()
    page.locator("#report-view .new-research").click()
    assert page.locator("#landing").is_visible()
    assert page.locator("#question").input_value() == ""
    assert not errors
    context.close()


@pytest.mark.parametrize("width,height", [(390, 844), (768, 1024), (320, 740)])
def test_responsive_home_and_report(browser_app, width, height):
    browser, url, artifacts = browser_app
    page = browser.new_page(viewport={"width": width, "height": height}, device_scale_factor=1)
    page.goto(url, wait_until="networkidle")
    assert_no_overflow(page)
    page.screenshot(path=str(artifacts / f"home-{width}.png"), full_page=True)
    page.locator("#question").fill("Compare Python, JavaScript, and Go for backend development.")
    page.locator("#start-button").click()
    page.locator("#report-view").wait_for(state="visible")
    assert not page.locator("#sources-panel").is_visible()
    assert_no_overflow(page)
    page.screenshot(path=str(artifacts / f"report-{width}.png"), full_page=True, animations="disabled")
    page.locator(".citation").first.click()
    assert page.locator("#source-1").is_visible()
    page.close()


def test_help_dialog_keeps_question_and_restores_focus(browser_app):
    browser, url, artifacts = browser_app
    page = browser.new_page(viewport={"width": 390, "height": 844})
    page.goto(url, wait_until="networkidle")
    page.locator("#question").fill("How is AI changing education?")
    page.locator("#how-nav").click()
    assert page.locator("#how-dialog").is_visible()
    assert_no_overflow(page)
    page.screenshot(path=str(artifacts / "mobile-help.png"), animations="disabled")
    page.keyboard.press("Escape")
    assert not page.locator("#how-dialog").is_visible()
    assert page.locator("#question").input_value() == "How is AI changing education?"
    assert page.locator("#how-nav").evaluate("element => element === document.activeElement")
    page.close()


def test_progress_tracks_provider_work_and_cancel_preserves_question(browser_app, monkeypatch):
    browser, url, artifacts = browser_app
    released = Event()
    session, _ = make_case()
    generate = session.gemini.models.generate_content

    def wait_for_release(**kwargs):
        released.wait(timeout=30)
        return generate(**kwargs)

    session.gemini.models.generate_content = wait_for_release
    monkeypatch.setattr(agent, "create_session", lambda: session)
    page = browser.new_page(viewport={"width": 1440, "height": 1000})
    try:
        page.goto(url, wait_until="networkidle")
        page.locator("#question").fill("What is artificial intelligence?")
        page.locator("#start-button").click()
        page.locator('[data-stage="planning"].active').wait_for()
        assert page.locator('[data-stage="understanding"]').get_attribute("class") == "completed"
        assert page.locator("#live-source-count").inner_text() == "0"
        assert not page.locator("#plan-preview").is_visible()
        page.screenshot(path=str(artifacts / "desktop-progress.png"), full_page=True, animations="disabled")
        page.locator("#how-nav").click()
        page.locator("#how-close").click()
        assert page.locator("#workspace").is_visible()
        page.locator("#cancel-button").click()
        assert page.locator("#landing").is_visible()
        assert page.locator("#question").input_value() == "What is artificial intelligence?"
        assert page.locator("#start-button").is_enabled()
    finally:
        released.set()
        page.close()


def test_browser_failure_and_safe_rendering(browser_app):
    browser, url, _ = browser_app
    page = browser.new_page()
    page.goto(url)
    page.route("**/api/research", lambda route: route.fulfill(status=503, content_type="application/json", body='{"error":"Tavily is unavailable. Please try again."}'))
    page.locator("#question").fill("<img src=x onerror=window.hacked=true> Explain AI.")
    page.locator("#start-button").click()
    page.locator("#workspace-error").wait_for(state="visible")
    assert "Tavily is unavailable" in page.locator("#workspace-error").inner_text()
    assert page.locator("#workspace-title img").count() == 0
    assert page.evaluate("window.hacked") is None
    page.locator("#workspace-error .new-research").click()
    assert page.locator("#landing").is_visible()
    page.close()
