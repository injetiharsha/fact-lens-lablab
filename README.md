# FactLens Crew

FactLens Crew is a collaborative, agentic fact-verification system built on LangChain and LangGraph.
It accepts text/image/PDF input, retrieves live evidence through multiple channels, scores evidence quality with deterministic rules, and returns a transparent verdict with confidence, citations, and run telemetry.

---

## 1. End-to-End Flow

1. **Input Intake** — Text claim, image OCR output, or PDF extracted text enters pipeline.
2. **Intake Agent** — Normalizes and validates claim checkability.
3. **Domain Router Agent** — Selects semantic route (e.g., economy/science/general) and claim type.
4. **Retrieval Trio** (parallel via LangGraph `RunnableParallel`):
   - **Web Scout Agent** — Broad web search across multiple providers.
   - **Archive Hunter Agent** — Institutional/high-trust targeted retrieval.
   - **Data Extractor Agent** — Structured API calls + deep web scraping.
5. **Tri-Consistency Agent** — Measures Jaccard overlap/coherence across retrieval channels.
6. **Evidence Aggregator Agent** — Merges, deduplicates, balances channels, applies consistency-aware filtering.
7. **Skeptic Agent** — Challenges weak assumptions and identifies contradiction gaps.
8. **Evidence Auditor Agent (Source Quality)** — Deterministic quality scoring and rejection filters.
9. **Stat Comparator Agent** — Numeric/rank guardrail for statistical claims.
10. **Consensus Moderator Agent** — Final verdict arbitration with confidence + explanation.
11. **Rebuttal Loop** — If skeptic flags contradictions and confidence is below gate, a single rebuttal pass retrieves more evidence and re-runs audit → stat → moderator.
12. **Memory/Cache Tail** — Cache write, similar-claim retrieval, run persistence, change log, trust stats update.

---

## 2. Architecture (Key Components)

| File | Role |
|------|------|
| `api/main.py` | FastAPI API, async run lifecycle, static hosting |
| `factlens_crew/orchestrator.py` | LangGraph workflow definition, stage orchestration, all agent logic |
| `factlens_crew/tools.py` | Retrieval providers, extraction, scoring utilities, query planning |
| `factlens_crew/agents.py` | Event-bus-based agent classes (alternative to orchestrator agents) |
| `factlens_crew/llm.py` | LLM provider clients (Gemini via LangChain, Featherless via OpenAI-compat) |
| `factlens_crew/schemas.py` | Pydantic schemas: `EvidenceItem`, `AgentReport`, `VerificationState`, etc. |
| `factlens_crew/memory.py` | SQLite-backed cache, history, similarity retrieval, trust stats, drift tracking |
| `static/workflow.html` | Primary workflow UI |
| `static/backend-live.html` | Live event stream monitor |
| `static/storage-live.html` | Stored-run explorer |

---

## 3. How Sources Are Retrieved

### 3.1 Source Retrieval Cascade

Source retrieval follows a **priority waterfall** — the system tries providers in order and stops at the first one that returns results:

```
1. Vertex AI Grounding Search  (Google Cloud, uses googleSearch tool)
       ↓ (empty?)
2. Gemini Grounding Search     (Gemini API key, prompt-based search)
       ↓ (empty?)
3. DuckDuckGo                  (ddgs library, no API key needed)
       ↓ (empty?)
4. Google Custom Search Engine (GOOGLE_SEARCH_API_KEY + GOOGLE_SEARCH_CX)
       ↓ (empty?)
5. Tavily Search API           (TAVILY_API_KEY)
       ↓ (empty?)
6. Wikipedia API               (always available, no key)
       ↓ (empty?)
7. Offline Stub                (only if FACTLENS_ALLOW_OFFLINE_FALLBACK=1)
```

This cascade is implemented in `tools.py → search_web()`. Each provider normalizes results into the canonical `EvidenceItem` schema before returning.

### 3.2 The Three Retrieval Channels (Parallel)

Once a claim is routed to a domain, three agents execute **in parallel** using LangChain's `RunnableParallel`:

| Channel | Agent | What It Does | Sources |
|---------|-------|-------------|---------|
| **Web Search** | Web Scout Agent | Broad web evidence using the retrieval cascade | Any search provider above |
| **Primary Sources** | Archive Hunter Agent | Targeted queries scoped to `.gov`, `.edu`, WHO, NASA, World Bank, IMF, OECD, etc. | Same cascade but with `site:` operator queries |
| **Structured Data + Scrape** | Data Extractor Agent | API calls (World Bank API for economy/population) + deep web scraping of discovered URLs | World Bank API, scraped HTML |

The number of results per channel scales with the `MODEL_POLICY` setting:

| Policy | Web | Archive | Extractor |
|--------|-----|---------|-----------|
| `fast` | 4 | 3 | 3 |
| `balanced` | 6 | 5 | 5 |
| `quality` | 8 | 7 | 7 |

### 3.3 Query Planning (How Search Queries Are Built)

Queries are **not** just the raw claim text. The system uses an **LLM-powered query planner** (`_llm_query_plan()`) that generates 5 specialized queries per claim:

1. `original_query` — The claim as-is for direct matching.
2. `primary_source_query` — Targeted at official institutions/regulators.
3. `refutation_query` — Seeks counter-evidence to avoid confirmation bias.
4. `entity_keyword_query` — Entity and keyword focused for precision.
5. `translated_query` — English translation for multilingual claims.

If the LLM is unavailable, the system falls back to **domain-specific heuristic query templates** that embed `site:` operators targeting authoritative domains (e.g., `site:imf.org OR site:worldbank.org` for economy claims).

### 3.4 Multilingual Support

For non-ASCII claims (Hindi, Kannada, Tamil, Telugu, Malayalam, etc.):

1. The claim is translated to English via Vertex AI / Gemini.
2. Translation uses a **dual-prompt strategy** — a full-sentence translation prompt and a concise rewrite prompt, keeping the best result.
3. Translated English queries are **prioritized** ahead of original-language queries.
4. The original-language claim is also kept as a fallback query.

### 3.5 Domain-Aware Routing

Before retrieval, the **Domain Router Agent** classifies the claim into one of 6 domains using LLM semantic routing (with keyword heuristic fallback):

- `economy` → IMF, World Bank, OECD, central banks
- `population` → Census, UN population data
- `health` → WHO, CDC, NIH, Lancet, NEJM
- `politics` → Government records, election commissions
- `science` → NASA, NOAA, Nature, ScienceDirect
- `general` → Mixed official + reputable web

Each domain maps to different `site:` scoped queries and different source expectations.

---

## 4. Source Quality Scoring

Quality scoring in FactLens is **deterministic, not a single LLM guess**. Evidence passes through multiple scoring dimensions with explicit rules.

### 4.1 Credibility Tiers (Domain-Based)

Every source URL is scored against a **7-tier domain credibility table**:

| Tier | Score | Example Domains |
|------|-------|----------------|
| **T1** — Government / Official | 1.00 (100) | `.gov`, `.edu`, `nasa.gov`, `who.int`, `nih.gov`, `cdc.gov`, `rbi.org.in` |
| **T2** — International / Science | 0.95 (95) | `un.org`, `worldbank.org`, `imf.org`, `oecd.org`, `nature.com`, `thelancet.com`, `nejm.org` |
| **T3** — Wire Services / Major Outlets | 0.85 (85) | `reuters.com`, `apnews.com`, `bloomberg.com`, `bbc.com`, `wsj.com` |
| **T4** — Reputable National News | 0.75 (75) | `thehindu.com`, `ndtv.com`, `economictimes.com`, `livemint.com` |
| **T5** — Fact-Checkers / Regional | 0.65 (65) | `snopes.com`, `politifact.com`, `factcheck.org`, `altnews.in`, `boomlive.in` |
| **T6** — Satire / Tabloid | 0.15 (15) | Known satire, tabloid, clickbait domains |
| **T7** — Social Media / Restricted | 0.05 (5) | `twitter.com`, `facebook.com`, `instagram.com`, `tiktok.com`, `t.me` |

Wikipedia receives a fixed credibility of **72** (between T3 and T4).

Unknown domains default to **40**.

### 4.2 Temporal Decay Weights

Sources are penalized based on the age inferred from the URL (year extracted via regex):

| Article Age | Weight |
|-------------|--------|
| Current year | 1.00 |
| 1 year old | 0.90 |
| 2 years old | 0.80 |
| 5 years old | 0.65 |
| 10 years old | 0.50 |
| 10+ years old | 0.40 |

**Exception:** Historical/scientific facts (e.g., claims about planetary orbits) bypass temporal decay entirely (weight = 1.00).

Trusted domains with no extractable year get a neutral weight of **0.80**.

### 4.3 Domain Diversity Penalty

To prevent a single website from dominating the evidence pool, the auditor applies diminishing multipliers:

| Occurrence from Same Domain | Multiplier |
|-----------------------------|-----------|
| 1st | 1.00 |
| 2nd | 0.70 |
| 3rd | 0.50 |
| 4th+ | **Rejected** (dropped entirely) |

### 4.4 Bad Domain Filtering

Sources from the following domains are **unconditionally excluded** from all evidence pools:

`twitter.com`, `x.com`, `facebook.com`, `instagram.com`, `tiktok.com`, `wa.me`, `t.me`, `reddit.com`, `quora.com`, `pinterest.com`

### 4.5 The Evidence Auditor Formula

The final evidence quality score (`T`) for the entire pool is:

```
T = (Σ(w_i × R_i) / n) × (1 − ρ) × 100
```

Where:
- `w_i` = credibility weight for source `i` (from tier table, 0–1)
- `R_i` = relevance score for source `i` (0–1, token overlap)
- `n` = number of sources
- `ρ` = duplicate penalty ratio (duplicate-rejected sources / total unique sources)

Individual source scores are computed as:

```
evidence_weight = (credibility/100) × relevance × temporal_weight × diversity_multiplier
```

Sources with `evidence_weight < 0.10` are rejected.

### 4.6 Configurable Guardrails

The auditor enforces minimum thresholds (configurable via environment variables):

| Variable | Default | Description |
|----------|---------|-------------|
| `FACTLENS_MIN_EVIDENCE_COUNT` | 4 | Minimum total evidence items required |
| `FACTLENS_MIN_TRUSTED_SOURCES` | 2 | Minimum sources from `.gov`/`.edu`/reference domains |
| `FACTLENS_MIN_DOMAIN_DIVERSITY` | 3 | Minimum distinct root domains required |
| `FACTLENS_DOMAIN_BLACKLIST` | _(empty)_ | Comma-separated domains to unconditionally block |

---

## 5. Relevance Scoring

### 5.1 Token-Overlap Relevance

Relevance is computed as the **Jaccard-like token overlap** between the claim and each evidence snippet:

```python
claim_tokens = {t for t in re.findall(r"[a-z0-9]+", claim.lower()) if len(t) > 2}
evidence_tokens = set(re.findall(r"[a-z0-9]+", evidence_text.lower()))
relevance = len(claim_tokens ∩ evidence_tokens) / len(claim_tokens)
```

This gives a 0–1 score per evidence item, computed at the point of retrieval.

### 5.2 MMR Diversity Selection

When selecting the final evidence pool, the system uses **Maximal Marginal Relevance (MMR)** to balance relevance against redundancy:

```
MMR_score(item) = λ × relevance(item) − (1−λ) × redundancy(item)
```

Where `λ = 0.70` (favoring relevance) and:
- `relevance(item)` = 0.45 × base_score + 0.35 × credibility + 0.20 × claim_overlap
- `redundancy(item)` = max Jaccard similarity to any already-selected item

### 5.3 Tri-Consistency Scoring

The **Tri-Consistency Agent** measures agreement across the three retrieval channels (web, archive, extractor) using pairwise **Jaccard similarity** on token sets:

```
tri_consistency = (J(web, archive) + J(web, extractor) + J(archive, extractor)) / 3
```

If `tri_consistency < 0.45`, the aggregator applies stricter filtering — only keeping evidence with `credibility ≥ 60` or from trusted/reference source types.

---

## 6. Verdict Logic

### 6.1 Verdicts

The moderator produces one of:

| Verdict | Meaning |
|---------|---------|
| `supported` | Claim is supported by the evidence |
| `refuted` | Claim is contradicted by the evidence |
| `insufficient_evidence` | Not enough quality evidence to decide |
| `needs_live_evidence` | No live evidence available; system refuses to fabricate |

### 6.2 How the Verdict Is Decided

1. **Deterministic pre-check**: If no live sources exist → `insufficient_evidence`. If average source quality < 45 or no trusted sources → `insufficient_evidence`.
2. **Stance counting**: Support vs. refutation hits are counted from evidence stance hints.
3. **LLM arbitration**: The moderator LLM (Gemini) produces a verdict and explanation, which can override the deterministic result if confidence is below the trigger gate (default: 65).
4. **Stat Comparator override**: For statistical claims, if the numeric guardrail says "refuted" or "insufficient_evidence", it overrides the moderator.
5. **Confidence clamping**: `insufficient_evidence` is capped at 45 confidence; `needs_live_evidence` is capped at 0.

### 6.3 Rebuttal Loop

A single rebuttal pass is triggered when:
- The verdict is `needs_review` or `insufficient_evidence` **and** confidence is below the gate threshold, OR
- The skeptic agent flagged a direct contradiction **and** confidence < 70.

The rebuttal re-runs: retrieval → tri-consistency → skeptic → auditor → stat comparator → moderator.

---

## 7. LLM Provider Architecture

The system uses **two LLM providers** through LangChain:

| Provider | Usage | Models |
|----------|-------|--------|
| **Gemini** (Google) | Intake, domain routing, query planning, translation, moderation, stat comparison | `gemini-2.5-flash` (default), configurable per agent |
| **Featherless** (OpenAI-compatible) | Skeptic analysis, heavy moderation | `Meta-Llama-3.1-8B-Instruct` (skeptic), `Llama-3.3-70B-Instruct` (heavy moderator) |

**Graceful degradation:** If API keys are missing or calls fail, agents fall back to deterministic rules. The system never halts due to an LLM failure.

---

## 8. Input Modes

- `text`: Direct claim text.
- `image`: OCR pipeline (Tesseract) with multi-language support (English, Hindi, Kannada, Malayalam, Tamil, Telugu), then intake.
- `pdf`: Page extraction (PyMuPDF/fitz) with section-aware chunking, then intake.

Run API supports multipart fields:
- `text`, `input_type` (`text|image|pdf`), `file` (optional), `pdf_pages` (optional, e.g., `1-3,5`), `cache_mode`, `force_live_recheck`

---

## 9. Cache, Memory, and History

### Cache Mode Control

Cache mode is per-run and controlled by UI/API request. If missing, fallback default is `auto`. Env `CACHE_MODE` is only fallback when request mode is absent.

Supported modes: `auto`, `off`, `read`, `write`, `read_write`, `update`

### What Is Stored

SQLite memory DB stores:
- **runs** — Run metadata + final result JSON
- **evidence** — All evidence items per run with credibility/relevance scores
- **run_events** — Timeline of agent events
- **trust_stats** — Per-domain credibility tracking across runs
- **change_log** — Verdict drift tracking when re-checking claims
- **claim_snapshots** — Historical verdict snapshots for drift detection
- **claim_heads** — Current "head" verdict per normalized claim

### Similar Claim Retrieval

On each run, the memory store searches for **previously verified similar claims** using normalized text matching. Matches are used to:
- Bootstrap retrieval queries with prior context
- Detect verdict drift (change log)
- Provide provenance metadata in the response

### Runtime Path Behavior

- Uses `FACTLENS_MEMORY_DB` if provided.
- Else tries project `data/factlens_memory.sqlite3`.
- If filesystem is read-only (common on serverless), auto-falls back to `/tmp/factlens_memory.sqlite3`.

---

## 10. Telemetry and Explainability

The UI/response surfaces:

- **Stage metrics** — Wall-clock timing per pipeline stage.
- **Decision trace** — Per-agent action log with rationale and tools used.
- **Event timeline** — Chronological agent events with status and data payload.
- **Evidence rejections** — Breakdown of why sources were dropped (blacklist, duplicate penalty, low score).
- **The Turn** — Natural-language narrative explaining how the verdict evolved from initial to final.
- **Stance scoring** — Quantified support/refutation balance.
- **Confidence bands** — Categorical confidence level.
- **Similar claims** — Prior verified claims used for context.
- **Change log** — What changed vs. previous verification of the same claim.

---

## 11. Local Setup

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

---

## 12. API Endpoints

- `POST /api/verify` — Synchronous verification
- `POST /api/verify/start` — Async verification (returns run_id)
- `GET /api/runs/{run_id}/status` — Poll run status
- `GET /api/runs/{run_id}/events` — Stream run events
- `GET /api/runs/live` — List active runs
- `GET /api/storage/runs` — List stored runs
- `GET /api/storage/run/{run_id}` — Get stored run result
- `GET /health` — Health check

---

## 13. Environment Configuration

Copy from `.env.example`. Key groups:

| Group | Variables |
|-------|-----------|
| **LLM Providers** | `GEMINI_API_KEY`, `FEATHERLESS_API_KEY` |
| **Search** | `GOOGLE_SEARCH_API_KEY`, `GOOGLE_SEARCH_CX`, `TAVILY_API_KEY`, `GOOGLE_WIKIPEDIA_ONLY` |
| **Vertex AI** | `VERTEX_SEARCH_ENABLE`, `GOOGLE_CLOUD_PROJECT`, `GOOGLE_CLOUD_LOCATION`, `GEMINI_SEARCH_MODEL` |
| **Model Selection** | `GEMINI_INTAKE_MODEL`, `GEMINI_MODERATOR_MODEL`, `FEATHERLESS_MODEL`, `FEATHERLESS_SKEPTIC_MODEL` |
| **Quality Guardrails** | `FACTLENS_MIN_EVIDENCE_COUNT`, `FACTLENS_MIN_TRUSTED_SOURCES`, `FACTLENS_MIN_DOMAIN_DIVERSITY`, `FACTLENS_DOMAIN_BLACKLIST` |
| **Runtime** | `MODEL_POLICY` (`quality`/`balanced`/`fast`), `FACTLENS_STAGE_TIMEOUT_SEC`, `FACTLENS_VERIFIER_TRIGGER_CONFIDENCE_GATE` |
| **Cache/Memory** | `CACHE_MODE`, `CACHE_TTL_SECONDS`, `FACTLENS_MEMORY_DB`, `FACTLENS_SESSION_ID`, `FACTLENS_POLICY_VERSION` |

---

## 14. Deployment Notes

- Never commit `.env`.
- Set all secrets in deployment environment variables.
- For serverless, `/tmp` DB fallback is expected behavior unless external DB path is configured.
- Workflow UI includes: `Open Backend Live`, `Open Stored Data`, `Full Pipeline` toggle.

---

## 15. Submission Support

- `submission_assets/SUBMISSION.md` contains copy-ready hackathon submission text.
- `submission_assets/README.md` explains submission asset usage.
