# Retrieval Intent vs Current Behavior

## Scope
This note is independent of product/hackathon goals.  
It documents:
- what retrieval was originally supposed to do,
- what it currently does,
- why it fails in practice.

---

## 1) Original Intent for Retrieval

Retrieval was intended to be **claim-centric and coordinated**, not just multi-node.

### Intended properties
1. Parse one claim into shared intent:
   - entities, metric, time/year, comparison/rank semantics.
2. Run multi-channel retrieval with shared intent:
   - web search,
   - high-trust/official sources,
   - structured/API extraction,
   - scrape fallback.
3. Return enough **independent evidence** for arbitration:
   - multiple domains,
   - trusted + corroborating sources,
   - year-matched numeric evidence when claim is time-bound.
4. Feed scoring with quality evidence so downstream verdict is driven by facts, not sparse data.

In short: tri-search should act like one coordinated system with different tools, not isolated workers.

---

## 2) Current Retrieval Design (As Implemented)

### Current flow
1. `Intake Agent` extracts claim/checkability.
2. `Domain Router Agent` chooses domain and claim type.
3. Tri-search runs in parallel:
   - `Web Research Agent`
   - `Primary Source Agent`
   - `Data Extractor Agent`
4. `Evidence Aggregator Agent` merges channel outputs.
5. `Source Quality Agent` filters and scores.

### Current scoring gates affecting retrieval usefulness
- Drop evidence if `RS < 0.30` or `EW < 0.15`.
- Domain repetition penalties (1st x1.0, 2nd x0.7, 3rd x0.5, 4th+ drop).
- Min evidence / min trusted / min domain diversity constraints.

### Current search backend chain
For web path, code attempts providers in fallback order (configured env dependent), but success depends on live provider availability and response quality.

---

## 3) Observed Failure Pattern

From recent smoke runs (example run IDs):
- `25f094ac-b7fc-412b-abb3-34abb7b96c5f`
- `403e9e4a-10a2-4303-a174-607ad7ca2e98`
- `a1992a16-0835-46d9-8a0a-7c57117739c5` (2026-05-18 18:43 IST)

Observed:
1. `Web Research Agent` returned 0 sources.
2. `Primary Source Agent` returned 0 sources.
3. `Data Extractor Agent` returned 1 API source (often World Bank).
4. Aggregator received too-small pool.
5. Auditor dropped or heavily penalized the pool.
6. Verdict falls to `insufficient_evidence`.

Latest confirmed snapshot:
- Verdict: `insufficient_evidence`
- Confidence: `25%`
- Final accepted sources: `0`

---

## 4) Why It Fails (Root Cause)

### A) Provider-level sparsity / empty retrieval
The dominant failure is upstream retrieval emptiness (web + primary returning zero), not only scorer strictness.

### B) Coordination is improved but still shallow at execution
Shared claim intent exists, but in practice each channel still depends on its own provider success and may not produce complementary evidence.

### C) Low lexical relevance for structured evidence
API snippets may be factually useful but can under-score on token-overlap relevance, then get filtered by RS/EW thresholds.

### D) Time-bound comparative claims need multi-source numeric corroboration
Single-source API output is usually insufficient for rank claims (e.g., “4th largest in 2026”).

---

## 5) Original Intent vs Current Reality (Gap)

### Intended
- Multi-source, mutually consistent retrieval with enough trusted and diverse evidence.

### Current
- Frequently sparse retrieval (0/0/1 pattern), so scoring/arbitration has too little material.

### Net effect
- System behaves safely (does not hallucinate verdicts), but often under-delivers usable verification because retrieval recall is low.

---

## 6) Practical Interpretation

The system is currently **failing safely**, not failing silently:
- It avoids fabricated confidence,
- but retrieval reliability is below required level for robust fact-checking.

Primary issue is retrieval recall/coverage, not just final moderator logic.

## Latest Smoke Snapshot

- Timestamp: 2026-05-18 18:54:26 +05:30
- Claim: India is the 4th largest economy in 2026
- Run ID: 9beb0cdd-2bdd-4e8a-b6e0-015b5b4423c4
- Result: insufficient_evidence (confidence 25%, sources 0)
- Note: intake path is valid; full-pipeline bottleneck is retrieval sparsity.

---

## 7) Scoring Upgrade Rules for This Failure Class

Given current failure is retrieval sparsity, scoring upgrades must follow:

1. Do not relax thresholds purely to force verdict output.
2. Any threshold adaptation must be global/config-driven, not claim-targeted.
3. Keep deterministic guardrails as safety floor (`RS`, `EW`, diversity, trust minima).
4. Improve retrieval recall first; then recalibrate scoring using matrix-wide data.
5. Validate with multi-claim regression before promoting policy.

## Latest Retrieval Validation

- Timestamp: 2026-05-18 19:30:12 +05:30
- Claim: India is the 4th largest economy in 2026
- Run ID: 11c1d3d8-dc36-46c3-9a44-90a70f6eb50e
- Result: insufficient_evidence (25%, sources=0)
- Interpretation: retrieval recall/coverage still below required threshold for arbitration.
