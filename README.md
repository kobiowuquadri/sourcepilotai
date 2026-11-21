# SourcePilot AI

**Research smarter. Verify everything.**

A focused autonomous research agent built for the Global AI Hackathon — Tavily Research Agent Track. Ask a question, watch the research unfold, and receive a structured report with source references, supporting excerpts, and explicit limitations.

## The problem and the solution

Search engines leave users to assemble an answer from many pages. An unsupported chatbot answer creates a different problem: the reader cannot trace its claims. SourcePilot combines live web discovery with deliberate planning, evidence review, and cited synthesis in one research workspace.

The app uses **real Tavily and Gemini APIs**. It has no offline answer generator or production demo mode. Without credentials, it explains what needs configuration instead of generating a pretend report.

## Features

- Gemini plans research objectives, subquestions, and focused searches.
- Tavily discovers current sources and returns relevant text for analysis.
- Gemini reviews coverage and decides whether a targeted follow-up is worthwhile.
- A hard budget limits each request to **three Tavily searches**.
- Reports distinguish source-backed findings, interpretation, and uncertainty.
- Citation IDs and exact supporting excerpts are checked against retrieved content.
- A real event timeline, source discovery panel, cancellation, and recoverable errors keep the process transparent.
- Copy reports with their references, or export a print-formatted PDF from your browser.
- A warm, responsive research workspace with a focused question composer, collapsible sources, local Lucide icons, and a custom SVG identity.

## How the agent works

```text
Question → Gemini plan → 1–2 Tavily searches → source filtering
         → Gemini evidence review → optional targeted Tavily search
         → Gemini report → citation checks → report + source panel
```

This is a controlled agent workflow: the model formulates its search strategy, evaluates the returned evidence, and chooses a further search when that evidence leaves an important gap. The final answer depends on retrieved content, rather than one search snippet or an answer from model memory.

`agent.py` keeps the steps in straightforward functions:

| Function | Responsibility |
| --- | --- |
| `plan_research` | Resolve the question into objectives, subquestions, queries, and a recency filter. |
| `search_web` | Call the Tavily SDK, enforce the shared search budget, and use explicit timeouts. |
| `analyze_sources` | Remove low-scoring, empty, unsafe, and duplicate results; retain original URLs. |
| `identify_gaps` | Ask Gemini to select relevant sources and decide whether to follow up. |
| `generate_report` | Synthesize only supplied evidence into a validated report schema. |
| `validate_citations` | Check source IDs and quote provenance, omit invalid statements, and renumber cited sources. |
| `run_research` | Coordinate the steps and yield actual progress and result events. |

**Call budget:** up to three Tavily calls and three Gemini calls (plan, evidence review, synthesis). The small additional review call is intentional: it makes gap detection semantic and responsive to the retrieved evidence. There are no automatic retries or open-ended agent loops. A cooperative 160-second workflow deadline limits the time available to each next call; each Tavily request has at most an 18-second timeout, planning/review at most 35 seconds each, and synthesis at most 60 seconds. Provider/network timeout semantics can add overhead.

## Architecture and stack

- **Backend:** Python 3.11+, Flask, `google-genai`, `tavily-python`, Pydantic, `python-dotenv`.
- **Frontend:** HTML, CSS, vanilla JavaScript, Lucide, Inter and Lora.
- **Transport:** Fetch reads newline-delimited JSON from Flask. Heartbeats keep the connection open but never advance the timeline.
- **Deployment:** Gunicorn on Render; no database, Docker, Redis, queues, or frontend build system.

`app.py` exposes `GET /`, `GET /api/health`, and `POST /api/research`. Each admitted research request uses a small background thread so Flask can stream heartbeats while a provider call is in progress. A semaphore permits at most two research tasks per process. Gunicorn uses one process and four request threads. Cancellation prevents subsequent research steps once noticed; an API call already in progress may finish and incur provider usage.

## Tavily integration

SourcePilot uses `TavilyClient.search` with advanced search, a maximum of five results per query, explicit timeouts, and no Tavily-generated answer. The returned `content` snippets are the evidence supplied to Gemini. This retrieves relevant web text without an additional extraction request. Recency filters are set for time-sensitive questions.

URL normalization is used only to detect duplicates. The original URL from Tavily is retained in every reference. Results with low relevance scores or insufficient content are removed; Gemini then evaluates relevance to the actual question. At most 12 source snippets of 3,600 characters each reach synthesis.

## Gemini integration and evidence integrity

The supported `google-genai` SDK sends separate system instructions and JSON data, requests structured JSON with Pydantic-derived schemas, and validates the returned shape. `GEMINI_MODEL` is configurable; the example uses `gemini-3.8-flash`, listed as stable in [Google's model catalog](https://ai.google.dev/gemini-api/docs/models). Check model availability for your account if an API call fails.

For Gemini 3 models, the small planning step requests `LOW` thinking; evidence review and report synthesis retain the model's default. See [Google's thinking configuration guidance](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/guides/gemini-3-8-flash). Provider deadline errors are displayed as timeouts.

Webpage text is explicitly untrusted data. The model receives no credentials and has no tool-execution capability. It must not follow commands embedded in a source or question that override the research rules.

Every factual or interpretive report statement must carry a source ID and an exact excerpt from the corresponding retrieved content. If a reference is missing, unknown, or its excerpt is absent, the entire statement is omitted. If this leaves required sections empty or no cited evidence, the report fails with an insufficient-evidence message. References are then numbered consecutively and rendered as safe links. Optional comparisons and conflicting evidence appear only when applicable.

**These are provenance checks, not proof that every interpretation is correct.** Exact excerpt matching cannot prove that a quote entails a claim, establish source independence, or guarantee that a publisher is reliable. The UI exposes supporting excerpts and original links for review. Prompt-injection instructions and output validation reduce risk; they do not make model reasoning infallible.

## Local setup

Install Python 3.11 or newer, then run from this directory:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
```

On macOS/Linux:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
cp .env.example .env
```

Fill in `.env` with your own keys from [Tavily](https://app.tavily.com/) and [Google AI Studio](https://aistudio.google.com/apikey). Do not commit that file.

```env
TAVILY_API_KEY=your_tavily_key
GEMINI_API_KEY=your_gemini_key
GEMINI_MODEL=gemini-3.8-flash
FLASK_DEBUG=false
PORT=5000
```

| Variable | Purpose |
| --- | --- |
| `TAVILY_API_KEY` | Required server-side Tavily credential. |
| `GEMINI_API_KEY` | Required server-side Gemini credential. |
| `GEMINI_MODEL` | Gemini model ID; defaults to `gemini-3.8-flash` if empty. |
| `FLASK_DEBUG` | Optional local debugging; keep `false` in deployment. |
| `PORT` | Local port or hosting-provided production port. |

Start the local application in PowerShell:

```powershell
.\.venv\Scripts\python.exe app.py
```

If your Windows terminal is **Git Bash** (shows `MINGW64`), use forward slashes:

```bash
./.venv/Scripts/python.exe app.py
```

On macOS/Linux use `.venv/bin/python app.py`. Open **http://127.0.0.1:5000**. The app can render without keys; research requires both keys and provider account access. Restart after changing `.env`. Questions must be between 3 and 1,500 characters.

## Using the workspace

1. Type a question or select an example, then choose **Start Research**. Ctrl/Cmd + Enter also submits.
2. Inspect the real progress timeline, research objective, searches, and discovered sources.
3. Read the report. Select a citation to jump to its source; expand supporting excerpts or open the original article.
4. **Copy report** includes all cited references. **Export PDF** opens the browser print dialog: choose **Save as PDF** and disable browser headers/footers for the cleanest output.
5. Choose **New research** to start again. Reports remain only in the current page; a reload discards them.

## API

```http
POST /api/research
Content-Type: application/json

{"question":"Compare Python, JavaScript, and Go for backend development."}
```

Success uses `application/x-ndjson`: one JSON object per line with types `progress`, `plan`, `sources`, `review`, `notice`, `heartbeat`, and finally `result` or `error`. Check the final event, since an error after streaming starts cannot change the HTTP status. Input/configuration/capacity errors before streaming use a JSON error response and an appropriate 4xx/5xx status.

`GET /api/health` returns liveness and a boolean `research_available`. The boolean indicates configured credentials, **not** a successful provider connectivity test. No keys, prompts, or reports are stored in an application database. Questions and retrieved context are transmitted to the configured providers; consult their data policies for sensitive research.

## Testing

Install test dependencies:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest -m "not browser and not live" -q
.\.venv\Scripts\python.exe -m pytest -m browser -q
```

Browser tests use local Chrome or Edge when available. Otherwise set `BROWSER_EXECUTABLE` to your Chromium binary or run `python -m playwright install chromium` in the virtual environment. They exercise the real local Flask/agent flow with provider fixtures, check desktop/tablet/mobile widths, validate citation anchors, test copy and print, and write screenshots and a PDF into ignored `test-results/`. Fixture data lives only under `tests/`; it is not a production fallback.

The automated suite covers basic research, technology comparisons, current-topic filters, follow-up/no-follow-up decisions, no-source failures, provider failures, malformed JSON, input validation, citation provenance, deduplication, deadline/search budgets, stream responses, and bounded concurrency.

To run a **genuine provider test**, configure `.env`, then opt in explicitly (this uses provider credits):

```powershell
$env:RUN_LIVE_TESTS = "1"
.\.venv\Scripts\python.exe -m pytest -m live -q
```

On macOS/Linux: `RUN_LIVE_TESTS=1 .venv/bin/python -m pytest -m live -q`.

Live tests are skipped when credentials or the opt-in flag are absent. Passing mocks does not establish live API compatibility or answer quality. No live research or successful deployment is claimed without those checks actually running.

Validation in the development workspace (November 21, 2026): **48 backend/agent checks and 7 Chromium browser checks passed** on Windows with Python 3.14.2. Browser checks covered widths of 320, 390, 768, and 1440 pixels, clipboard copy, collapsible sources, citation navigation, PDF output with sources collapsed, help-dialog focus, actual progress events, and cancellation. The interface redesign preserved the backend, agent, dependencies, and environment configuration. After credentials were configured, the homepage returned HTTP 200, Gemini model access succeeded, and Tavily returned a real search result. The live end-to-end research test was attempted before the redesign but failed with a Gemini planning deadline timeout (HTTP 504), including a retry with lower planning effort; complete live report generation is not yet verified. The normal Flask launch and unconfigured-service behavior were also checked. Render deployment has not been performed.

## Render deployment

The repository includes `render.yaml` and `gunicorn.conf.py`. Push the source to your Git repository and create a Render Blueprint from it, or create a Python Web Service manually following [Render's Flask guide](https://render.com/docs/deploy-flask).

| Setting | Value |
| --- | --- |
| Build command | `pip install -r requirements.txt` |
| Start command | `gunicorn --config gunicorn.conf.py app:app` |
| Health check | `/api/health` |
| Required secrets | `TAVILY_API_KEY`, `GEMINI_API_KEY` |
| Model | `GEMINI_MODEL=gemini-3.8-flash` (or your supported model) |
| Debug | `FLASK_DEBUG=false` |

Gunicorn binds to `0.0.0.0:$PORT` using the port Render supplies. Render terminates HTTPS. Gunicorn is installed only on non-Windows platforms; use Flask's development server locally on Windows. Do not enable Flask's debugger publicly. The app sends no-buffering headers for streaming; if you add a reverse proxy, disable response buffering and allow the research request duration. No database provisioning is needed.

After deployment, open the HTTPS URL, inspect `/api/health`, and run a real research request to verify credentials, quotas, streaming, and citations. A successful health check alone does not verify the research workflow. Deployment configuration is provided; creating a Render service is a separate step.

## Current limitations

- Three searches cannot exhaust a broad topic. Narrow questions work best.
- Retrieved excerpts may omit important context, and recency filters do not prove a document's publication date. Paywalled or inaccessible content may be absent.
- Synthesis can still misinterpret evidence. Citation provenance is checked, semantic correctness is not independently verified.
- Two research tasks per process protect concurrency, but there is no user authentication, distributed rate limiter, or spending cap. Configure provider quotas for a public demo.
- API failures, quotas, and cold starts affect availability. A failed follow-up can produce a clearly labeled partial report from initial evidence.
- Research is not persisted. There is no history, recovery after a reload, or server-side PDF generator.
- External Google Fonts require network access; system sans-serif fallbacks remain usable. Icons are vendored locally.

## Future improvements

Independent claim-entailment checks, stronger source-authority evaluation, better date normalization, and a configurable research-depth budget would improve evidence quality. These are future work, not existing features.

Lucide icons are used under their ISC license; see `static/assets/lucide-LICENSE.txt`.
