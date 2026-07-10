# FactLens Crew

FactLens Crew is a collaborative, agentic fact-verification system built on LangChain and LangGraph.
It accepts text/image/PDF input, retrieves live evidence through multiple channels, scores evidence quality with deterministic rules, and returns a transparent verdict with confidence, citations, and run telemetry.

## 1. End-to-End Flow

1. Input intake
   - Text claim, image OCR output, or PDF extracted text enters pipeline.
2. Intake Agent
   - Normalizes and validates claim checkability.
3. Domain Router Agent
   - Selects semantic route (e.g., economy/science/general) and claim type.
4. Retrieval Trio (parallel)
   - Web Research Agent (broad web)
   - Primary Source Agent (institutional/high-trust targets)
   - Data Extractor Agent (structured APIs + deep scrape)
5. Tri Consistency Agent
   - Measures overlap/coherence across retrieval channels.
6. Evidence Aggregator Agent
   - Merges, deduplicates, balances channels, applies consistency-aware filtering.
7. Skeptic Agent
   - Challenges weak assumptions and contradiction gaps.
8. Source Quality Agent
   - Deterministic quality scoring and rejection filters.
9. Stat Comparator Agent
   - Numeric/rank guardrail for statistical claims.
10. Consensus Moderator Agent
   - Final verdict arbitration with confidence + explanation.
11. Memory/Cache tail nodes
   - Cache policy, similar-claim retrieval, run store, change log, trust stats update.

## 2. Architecture (Key Components)

- `api/main.py`: FastAPI API, async run lifecycle, static hosting.
- `factlens_crew/orchestrator.py`: LangGraph workflow and stage orchestration.
- `factlens_crew/tools.py`: retrieval, extraction, scoring utilities.
- `factlens_crew/memory.py`: SQLite-backed cache/history/similarity/trust storage.
- `static/workflow.html`: primary workflow UI.
- `static/backend-live.html`: live event stream monitor.
- `static/storage-live.html`: stored-run explorer.

## 3. Input Modes

- `text`: direct claim text.
- `image`: OCR pipeline then intake.
- `pdf`: page extraction then intake.

Run API supports multipart fields:
- `text`
- `input_type` (`text|image|pdf`)
- `file` (optional for image/pdf)
- `pdf_pages` (optional)
- `cache_mode`
- `force_live_recheck`

## 4. Retrieval Strategy

Retrieval is claim-driven and routed by domain/type.
The system can use multiple providers/channels depending on config and availability:

- Vertex grounding search
- Google CSE
- DuckDuckGo fallback
- Structured API retrieval (e.g., domain-specific datasets)
- Web scraping extractors

All candidate evidence is normalized into a canonical `EvidenceItem` schema before scoring.

## 5. Scoring and Verdict Logic

Quality is not a single LLM guess. Evidence passes through deterministic scoring dimensions:

- relevance
- credibility
- temporal signals
- domain diversity penalties

Then moderator arbitration produces one of:

- `supported`
- `refuted`
- `insufficient_evidence`
- `needs_live_evidence`

If live evidence is unavailable, the system returns `needs_live_evidence` rather than fabricating facts.

## 6. Cache, Memory, and History

### Cache mode control

- Cache mode is per-run and controlled by UI/API request.
- If missing, fallback default is `auto`.
- Env `CACHE_MODE` is only fallback when request mode is absent.

Supported modes:
- `auto`
- `off`
- `read`
- `write`
- `read_write`
- `update`

### What is stored

SQLite memory DB stores:
- runs (run metadata + final result JSON)
- evidence rows
- run events/timeline
- trust stats
- change log

### Runtime path behavior

- Uses `FACTLENS_MEMORY_DB` if provided.
- Else tries project `data/factlens_memory.sqlite3`.
- If filesystem is read-only (common on serverless), auto-falls back to `/tmp/factlens_memory.sqlite3`.

## 7. Telemetry and Explainability

The UI/response surfaces:

- stage metrics
- decision trace
- event timeline
- per-node details
- final verdict + confidence + sources

This supports hackathon judging for collaborative/agentic behavior and observability.

## 8. Local Setup

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

## 9. API Endpoints

- `POST /api/verify`
- `POST /api/verify/start`
- `GET /api/runs/{run_id}/status`
- `GET /api/runs/{run_id}/events`
- `GET /api/runs/live`
- `GET /api/storage/runs`
- `GET /api/storage/run/{run_id}`
- `GET /health`

## 10. Environment Configuration

Copy from `.env.example`. Key groups:

- LLM/providers: `GEMINI_API_KEY`, `FEATHERLESS_API_KEY`
- search: `GOOGLE_SEARCH_API_KEY`, `GOOGLE_SEARCH_CX`, `TAVILY_API_KEY`
- vertex: `VERTEX_SEARCH_ENABLE`, `VERTEX_PROJECT_ID`, `VERTEX_LOCATION`, `VERTEX_MODEL`
- guardrails: `FACTLENS_MIN_EVIDENCE_COUNT`, `FACTLENS_MIN_TRUSTED_SOURCES`, `FACTLENS_MIN_DOMAIN_DIVERSITY`
- cache/memory: `CACHE_MODE`, `CACHE_TTL_SECONDS`, `FACTLENS_MEMORY_DB`

## 11. Deployment Notes

- Never commit `.env`.
- Set all secrets in deployment environment variables.
- For serverless, `/tmp` DB fallback is expected behavior unless external DB path is configured.
- Workflow UI includes:
  - `Open Backend Live`
  - `Open Stored Data`
  - `Full Pipeline` toggle

## 12. Submission Support

- `submission_assets/SUBMISSION.md` contains copy-ready hackathon submission text.
- `submission_assets/README.md` explains submission asset usage.

