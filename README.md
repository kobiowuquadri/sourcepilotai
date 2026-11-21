# SourcePilot AI

**Research smarter. Verify everything.**

A focused research workspace built for the Global AI Hackathon — Tavily Research Agent Track. Ask a question, watch the research unfold live, and receive a structured report with numbered, clickable citations that link to every source.

## The problem and the solution

Search engines leave users to assemble an answer from many pages. An unsupported chatbot answer creates a different problem: the reader cannot trace its claims. SourcePilot puts Tavily's research agent behind a clean workspace: it plans the research, searches the live web, analyzes sources, and writes a cited report, while you watch each real step.

The app uses the **real Tavily Research API exclusively**. There is no other AI provider, no offline answer generator, and no demo mode. Without a key, it explains what needs configuration instead of generating a pretend report.

## Features

- **One provider, end to end.** The Tavily Research API handles research planning, web searches, source analysis, and report writing with numbered citations.
- **Live, real progress.** The research timeline, search queries, and discovered sources come from Tavily's streamed research events. Nothing is simulated.
- **Reliable by design.** If the live stream drops, SourcePilot recovers with Tavily's asynchronous research task and polls it to completion, so a network hiccup doesn't end in a timeout.
- **Verifiable citations.** Every `[n]` in the report links to its reference in the sources panel, which links to the original page. Each source card shows the passages of the report that cite it.
- **Safe rendering.** Tavily's Markdown report is parsed on the server into plain-text blocks (headings, paragraphs, lists, tables, quotes). The browser renders them with `textContent` only; no provider HTML ever reaches the page, and unsafe URLs never become links.
- Copy reports with their references, or export a print-formatted PDF from your browser.
- A warm, responsive research workspace with a focused question composer, collapsible sources, local Lucide icons, and a custom SVG identity.

## How it works

```text
Question → Flask validates it → Tavily Research (stream=True, citation_format="numbered")
         → live events: Planning → WebSearch (queries, sources) → Generating → report text
         → server parses the Markdown report and its numbered reference list
         → report + citations + sources panel

If the stream breaks:  Tavily research task (async) → poll get_research(request_id) → same report pipeline
```

`agent.py` keeps the steps in straightforward functions:

| Function | Responsibility |
| --- | --- |
| `stream_research` | Calls `TavilyClient.research(stream=True)`, parses its Server-Sent Events, and turns Tavily's real tool events into progress, query, and source updates. |
| `poll_research` | Tavily's asynchronous workflow: `research()` creates a task, `get_research(request_id)` polls it until `completed` or `failed`. Used automatically if the stream is interrupted. |
| `build_report` | Extracts the title and the numbered reference list, matches each reference to the sources Tavily actually retrieved, and parses the body into safe blocks with citation segments. |
| `run_research` | Coordinates the steps and yields progress, sources, notice, and result events. |
| `provider_error` | Maps Tavily SDK errors (auth, credits, rate limits, timeouts) to clear user messages without exposing provider details. |

### Reliability and timeouts

- The stream uses a 150-second idle timeout between chunks, not a total timeout, so long research is never cut off while Tavily is still working.
- The whole workflow has a 300-second deadline. The browser waits up to 330 seconds and Gunicorn's worker timeout is 330 seconds.
- Network drops, timeouts, and 5xx errors trigger the polling fallback once. Authentication, credit, rate-limit, and bad-request errors are reported right away and never retried.
- While polling, short transient failures are retried, up to four consecutive failures.
- Cancellation stops the stream and closes the connection. A research task that Tavily has already accepted may still finish on Tavily's side and use credits.

## Architecture and stack

- **Backend:** Python 3.11+, Flask, `tavily-python` (official SDK), `requests`, `python-dotenv`.
- **Frontend:** HTML, CSS, vanilla JavaScript, Lucide, Inter and Lora.
- **Transport:** Fetch reads newline-delimited JSON from Flask. Heartbeats keep the connection open but never advance the timeline.
- **Deployment:** Gunicorn on Render; no database, Docker, Redis, queues, or frontend build system.

`app.py` exposes `GET /`, `GET /api/health`, and `POST /api/research`. Each admitted research request runs in a small background thread so Flask can stream heartbeats while Tavily works. A semaphore allows at most two research tasks per process. Gunicorn uses one process and four request threads.

## Tavily integration

SourcePilot uses two SDK methods from `tavily-python` 0.8.5:

- `TavilyClient.research(input, model, stream, citation_format, timeout)`: `stream=True` returns Tavily's live event stream; `stream=False` returns `{request_id, status: "pending"}`.
- `TavilyClient.get_research(request_id, include_usage=True)`: returns `status` (`pending`, `in_progress`, `completed`, `failed`) and, when complete, `content`, `sources`, and `usage.credits`.

The research model is set by `TAVILY_RESEARCH_MODEL`:

| Model | When to use it |
| --- | --- |
| `mini` (default) | Fast, focused research. It took about 30–40 seconds and 38 credits in testing. |
| `pro` | Deeper, multi-angle research. It is slower and uses more credits. |
| `auto` | Tavily chooses based on the question. |

Citations use `citation_format="numbered"`. Each report ends with a numbered reference list, which SourcePilot separates from the body and checks:

- Only `http(s)` URLs on public hosts become links.
- Each reference is matched to the sources Tavily actually retrieved during the run (`retrieved: true` in the API response).
- A citation number with no matching reference is shown as plain text and recorded in the report's research notes.

**These are provenance checks, not proof that every statement is correct.** Citations show where Tavily found each claim; open the linked sources to verify important findings.

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

Add your key from [Tavily](https://app.tavily.com/) to `.env`. Do not commit that file.

```env
TAVILY_API_KEY=your_tavily_key
TAVILY_RESEARCH_MODEL=mini
FLASK_DEBUG=false
PORT=5000
```

| Variable | Purpose |
| --- | --- |
| `TAVILY_API_KEY` | Required server-side Tavily credential. |
| `TAVILY_RESEARCH_MODEL` | `mini` (default), `pro`, or `auto`. Unknown values fall back to `mini`. |
| `FLASK_DEBUG` | Optional local debugging; keep `false` in deployment. |
| `PORT` | Local port or hosting-provided production port. |

Start the app in PowerShell:

```powershell
.\.venv\Scripts\python.exe app.py
```

If your Windows terminal is **Git Bash** (it shows `MINGW64`), use forward slashes:

```bash
./.venv/Scripts/python.exe app.py
```

On macOS/Linux, use `.venv/bin/python app.py`. Open **http://127.0.0.1:5000**. The page loads without a key, but research needs `TAVILY_API_KEY` and an account with Research API access and available credits. Restart the server after changing `.env`. Questions must be between 3 and 1,500 characters.

## Using the workspace

1. Type a question or select an example, then choose **Start research**. Ctrl/Cmd + Enter also submits.
2. Follow the live timeline as Tavily plans, searches, reviews sources, and writes. Open **Search activity** to see each query; sources appear as Tavily finds them.
3. Read the report. Select a citation number to jump to its source, expand **Where it's cited in the report**, or open the original page.
4. **Copy report** includes the full report and every reference URL. **Export PDF** opens the browser print dialog: choose **Save as PDF** and turn off browser headers and footers for the cleanest output.
5. Choose **New research** to start again. Reports stay only in the current page; reloading discards them.

## API

```http
POST /api/research
Content-Type: application/json

{"question":"Compare Python, Node.js, and Go for backend development."}
```

Success returns `application/x-ndjson`: one JSON object per line, with types `progress`, `sources`, `notice`, `heartbeat`, and finally `result` or `error`. Always check the final event: once streaming has started, an error can no longer change the HTTP status. Input, configuration, and capacity errors that happen before streaming return a JSON error with a 4xx or 5xx status.

The `result` report contains:

| Field | Contents |
| --- | --- |
| `title`, `question`, `generated_at` | Report title, the original question, and the generation time. |
| `blocks` | The parsed report body: headings, paragraphs, lists, tables, quotes, and code, with inline `cite`, `link`, `strong`, `em`, and `code` segments. |
| `markdown` | The report body as Markdown, used by **Copy report**. |
| `sources` | Numbered references, each with `id`, `title`, `url`, `domain`, and `retrieved`. |
| `limitations` | Research notes, such as a fallback that was used or a citation that couldn't be matched. |
| `research` | The model, transport mode, queries, source counts, elapsed time, and Tavily credits (when Tavily reports them). |

`GET /api/health` returns liveness and a boolean `research_available`. The boolean only means a Tavily key is configured; it does **not** test the connection to Tavily. Keys, prompts, and reports are not stored in any database. Questions are sent to Tavily; check Tavily's data policy before researching sensitive topics.

## Testing

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest -m "not browser and not live" -q
.\.venv\Scripts\python.exe -m pytest -m browser -q
```

The unit and app tests replay synthetic Tavily streams in the exact SSE format the live API returns. They cover:

- progress mapping and stream buffering
- the stream-to-polling fallback and failed tasks
- authentication and credit errors
- reference parsing, citation ranges, comparison tables, and unsafe-URL filtering
- HTML-injection safety, input validation, streaming responses, and bounded concurrency

Browser tests use a local Chrome or Edge when one is available. Otherwise, set `BROWSER_EXECUTABLE` to your Chromium binary or run `python -m playwright install chromium` in the virtual environment. They check desktop, tablet, and mobile widths, citation anchors, copy, print/PDF, cancellation, and the help dialog. Fixture data lives only under `tests/` and is never used as a production fallback.

To run a **genuine Tavily test**, configure `.env` and opt in explicitly. This uses Tavily credits.

```powershell
$env:RUN_LIVE_TESTS = "1"
.\.venv\Scripts\python.exe -m pytest -m live -q
```

On macOS/Linux, use `RUN_LIVE_TESTS=1 .venv/bin/python -m pytest -m live -q`.

## Render deployment

The repository includes `render.yaml` and `gunicorn.conf.py`. Push the source to your Git repository and create a Render Blueprint from it, or create a Python Web Service manually by following [Render's Flask guide](https://render.com/docs/deploy-flask).

| Setting | Value |
| --- | --- |
| Build command | `pip install -r requirements.txt` |
| Start command | `gunicorn --config gunicorn.conf.py app:app` |
| Health check | `/api/health` |
| Required secret | `TAVILY_API_KEY` |
| Model | `TAVILY_RESEARCH_MODEL=mini` (or `pro` / `auto`) |
| Debug | `FLASK_DEBUG=false` |

Gunicorn binds to `0.0.0.0:$PORT` using the port Render supplies, and Render terminates HTTPS. Gunicorn is installed only on non-Windows platforms; on Windows, use Flask's development server locally. Never enable Flask's debugger publicly. The app sends no-buffering headers for streaming; if you add a reverse proxy, turn off response buffering and allow for the full research duration.

## Current limitations

- Report quality, depth, and source selection depend on Tavily's research agent and the model you choose.
- Citation checks confirm that each reference is real and was retrieved during the run. They do not prove that a source supports every word of a claim.
- If the stream is interrupted, the polling fallback reruns the research as a new Tavily task, which uses additional credits.
- Two research tasks per process protect concurrency, but there is no user authentication, distributed rate limiting, or spending cap. Set provider quotas before running a public demo.
- Research is not saved. There is no history, no recovery after a reload, and no server-side PDF generator.
- External Google Fonts need network access; system sans-serif fallbacks still work. Icons are bundled locally.

Lucide icons are used under their ISC license; see `static/assets/lucide-LICENSE.txt`.
