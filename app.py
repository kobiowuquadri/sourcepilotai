"""Flask routes and a small NDJSON stream; no database or task service needed."""

import json
import logging
import os
from queue import Empty, Queue
from threading import BoundedSemaphore, Event, Thread

from dotenv import load_dotenv
from flask import Flask, Response, jsonify, render_template, request
from werkzeug.exceptions import RequestEntityTooLarge

from agent import ResearchError, check_configuration, run_research, validate_question

load_dotenv()
# Provider exception logs can include request details. We return curated errors.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("google.genai").setLevel(logging.WARNING)


def create_app():
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = 16 * 1024
    capacity = BoundedSemaphore(2)

    @app.get("/")
    def index():
        return render_template("index.html")

    @app.get("/api/health")
    def health():
        ready = all(os.getenv(key, "").strip() for key in ("TAVILY_API_KEY", "GEMINI_API_KEY"))
        return jsonify(status="ok", research_available=ready)

    @app.errorhandler(RequestEntityTooLarge)
    def too_large(_error):
        return jsonify(error="That request is too large. Please use a shorter question.", code="invalid_input"), 413

    @app.after_request
    def security_headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self' https://fonts.googleapis.com; "
            "font-src 'self' https://fonts.gstatic.com; img-src 'self' data:; connect-src 'self'; "
            "object-src 'none'; base-uri 'self'; frame-ancestors 'none'; form-action 'self'"
        )
        if request.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.post("/api/research")
    def research():
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return jsonify(error="Send a JSON object containing your research question.", code="invalid_input"), 400
        try:
            question = validate_question(payload.get("question"))
            check_configuration()
        except ResearchError as error:
            return jsonify(error=str(error), code=error.code), error.status
        if not capacity.acquire(blocking=False):
            return jsonify(error="Both research slots are busy. Please try again shortly.", code="busy"), 429

        events = Queue()
        cancelled = Event()

        def produce():
            workflow = None
            try:
                workflow = run_research(question)
                while not cancelled.is_set():
                    try:
                        event = next(workflow)
                    except StopIteration:
                        break
                    if cancelled.is_set():
                        break
                    # Full source text is only needed on the server for synthesis.
                    if event["type"] == "sources":
                        event = {**event, "sources": [{k: v for k, v in source.items() if k not in ("content", "score")}
                                                      for source in event["sources"]]}
                    events.put(event)
            except ResearchError as error:
                events.put({"type": "error", "message": str(error), "code": error.code})
            except Exception:
                app.logger.error("Research failed with an unexpected internal error.")
                events.put({"type": "error", "message": "Something interrupted the research. Please try again.", "code": "internal_error"})
            finally:
                try:
                    if workflow is not None:
                        workflow.close()
                finally:
                    capacity.release()
                    events.put(None)

        worker = Thread(target=produce, daemon=True, name="sourcepilot-research")
        try:
            worker.start()
        except RuntimeError:
            capacity.release()
            return jsonify(error="Research is temporarily unavailable. Please retry.", code="busy"), 503

        def stream():
            try:
                while True:
                    try:
                        event = events.get(timeout=10)
                    except Empty:
                        # Connection keepalive only; never advances the workflow.
                        event = {"type": "heartbeat"}
                    if event is None:
                        break
                    yield json.dumps(event, ensure_ascii=False) + "\n"
            finally:
                cancelled.set()

        return Response(stream(), mimetype="application/x-ndjson", headers={"X-Accel-Buffering": "no"})

    return app


app = create_app()

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.getenv("PORT", "5000")),
            debug=os.getenv("FLASK_DEBUG", "false").lower() == "true", threaded=True)
