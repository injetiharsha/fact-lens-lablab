# FactLens Crew

FactLens Crew is a collaborative, agentic fact-checking system built for hackathon/demo workflows.
It accepts text, image, or PDF input, retrieves live evidence, scores evidence quality, and returns a transparent verdict with traceable pipeline events.

## What This Project Does

- Runs a multi-agent verification pipeline end-to-end.
- Uses live retrieval channels (web/API/scrape) with source-aware filtering.
- Applies deterministic scoring and quality guardrails before verdicting.
- Stores run history, evidence, events, and similarity memory in SQLite.
- Exposes workflow telemetry UI and backend/storage live pages.

## Core Pipeline

1. Intake Agent
2. Domain Router Agent
3. Retrieval Trio
   - Web Research Agent
   - Primary Source Agent
   - Data Extractor Agent
4. Tri Consistency Agent
5. Evidence Aggregator Agent
6. Skeptic Agent
7. Source Quality Agent
8. Stat Comparator Agent
9. Consensus Moderator Agent
10. Memory/Cache tail nodes (similar claims, run store, trust stats)

## Repository Structure

- `api/main.py` - FastAPI app and endpoints
- `factlens_crew/orchestrator.py` - main workflow orchestration
- `factlens_crew/tools.py` - retrieval/extraction/scoring helpers
- `factlens_crew/memory.py` - SQLite memory/cache integration
- `static/workflow.html` - primary workflow UI
- `static/backend-live.html` - live backend event monitor
- `static/storage-live.html` - stored runs/evidence explorer
- `.env.example` - runtime config template

## Quick Start

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install --upgrade pip
.\.venv\Scripts\python -m pip install -r requirements.txt
Copy-Item .env.example .env
.\.venv\Scripts\python api\main.py
```

Open:

- `http://127.0.0.1:8000/workflow.html`
- `http://127.0.0.1:8000/backend-live.html`
- `http://127.0.0.1:8000/storage-live.html`

## API Endpoints

- `POST /api/verify` - synchronous run
- `POST /api/verify/start` - async run start
- `GET /api/runs/{run_id}/status` - run status/result
- `GET /api/runs/{run_id}/events` - run event stream
- `GET /api/storage/runs` - stored runs list
- `GET /api/storage/run/{run_id}` - stored run detail
- `GET /health` - health check

## Environment Configuration

Copy from `.env.example`.
Important keys:

- LLM/providers: `GEMINI_API_KEY`, `FEATHERLESS_API_KEY`
- Search: `GOOGLE_SEARCH_API_KEY`, `GOOGLE_SEARCH_CX`, `TAVILY_API_KEY`
- Vertex search: `VERTEX_SEARCH_ENABLE`, `VERTEX_PROJECT_ID`, `VERTEX_LOCATION`, `VERTEX_MODEL`
- Guardrails: `FACTLENS_MIN_EVIDENCE_COUNT`, `FACTLENS_MIN_TRUSTED_SOURCES`, `FACTLENS_MIN_DOMAIN_DIVERSITY`
- Cache/memory: `CACHE_MODE`, `CACHE_TTL_SECONDS`, `FACTLENS_MEMORY_DB`

## Scoring and Verdicting

Source quality uses weighted scoring across:

- relevance
- credibility
- temporal signals
- domain diversity penalties

The moderator returns:

- verdict (`supported`, `refuted`, `insufficient_evidence`, `needs_live_evidence`)
- confidence
- reasoning trace/events

When live evidence is unavailable, the system returns `needs_live_evidence` rather than fabricating output.

## Notes for Deployment

- Keep secrets in platform environment variables (do not commit `.env`).
- On deploy/local, workflow UI includes `Open Backend Live`, `Open Stored Data`, and `Full Pipeline` toggle.
- For serverless runtimes, memory DB automatically falls back to `/tmp/factlens_memory.sqlite3` if project `data/` is read-only.
- For Cloud Run/Vertex usage, ensure service auth and Vertex env vars are set.

## Current Status

- UI telemetry + storage pages integrated.
- Memory/history persistence enabled.
- Retrieval quality depends on provider health/config at runtime.
