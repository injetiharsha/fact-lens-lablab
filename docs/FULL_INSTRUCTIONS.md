# Full Instruction Set

## Goal

FactLens Crew is a collaborative multi-agent fact-checking app. It takes a text claim or uploaded PDF/image, extracts a checkable claim, gathers live evidence, challenges the evidence, scores source quality, and returns a transparent verdict with a War Room event log.

## Workflow

1. Intake Agent extracts the normalized claim and rejects vague or non-factual input.
2. Web Research Agent searches for broad live evidence.
3. Primary Source Agent searches for official, academic, government, and reference sources.
4. Skeptic Agent checks weak assumptions, weak sources, missing citations, and unresolved contradictions.
5. Source Quality Agent deduplicates sources and scores trust.
6. Consensus Moderator Agent produces the final verdict.
7. One bounded rebuttal loop runs if the Skeptic finds a strong issue or Moderator confidence is below `50`.

## Verdict Policy

The app must not fabricate evidence.

- If live evidence exists, agents can return `supported`, `refuted`, `needs_review`, or `insufficient_evidence`.
- If no live evidence exists, the final verdict is `needs_live_evidence` with confidence `0`.
- Offline fallback is allowed only for local UI demos with `FACTLENS_ALLOW_OFFLINE_FALLBACK=1`.

## Source Weighting

- Official, government, academic, and primary sources outrank generic web pages.
- Recent sources matter more for time-sensitive claims.
- Independent sources outrank duplicated or syndicated content.
- Weak evidence should produce `insufficient_evidence`, not a forced verdict.
- Skeptic objections must be grounded in citations or labeled unresolved.

## API Contract

`POST /api/verify` accepts multipart form data:

- `text`: claim or context.
- `input_type`: `text`, `pdf`, or `image`.
- `file`: optional uploaded file.

Response fields:

- `run_id`
- `framework`
- `verdict`
- `confidence`
- `agent_reports`
- `sources`
- `disagreements`
- `final_explanation`
- `recommendation`
- `events`

`GET /api/runs/{run_id}/events` returns War Room events for polling.

## UI

The static UI at `/` contains:

- claim input,
- optional file upload,
- Run Crew button,
- War Room agent cards,
- final verdict panel,
- source list,
- disagreement summary.

## Testing

Run:

```powershell
.\.venv\Scripts\python -m unittest discover -s tests -v
```

Tests verify:

- no fake verdict when live evidence is unavailable,
- offline fallback requires an explicit dev flag,
- response contract remains stable,
- empty input returns insufficient evidence.
