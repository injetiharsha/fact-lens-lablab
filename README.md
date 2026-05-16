# FactLens Crew

Collaborative multi-agent fact-checking demo for the lablab.ai AI Agent Olympics / Milan AI Week hackathon.

## What It Builds

FactLens Crew accepts a text claim plus optional PDF/image input. A visible War Room of specialized agents extracts the claim, researches evidence, challenges assumptions, scores source quality, and produces a consensus verdict. It does not fabricate final verdicts when live evidence is unavailable.

## Agent Roles

- Intake Agent: extracts a checkable claim from text, PDF, or image input. Uses Gemini when `GEMINI_API_KEY` is configured.
- Domain Router Agent: classifies claim domain and semantic route.
- Web Research Agent: gathers broad web evidence.
- Primary Source Agent: prioritizes official, academic, government, and reference sources.
- Data Extractor Agent: runs structured API + deep scrape extraction.
- Skeptic Agent: challenges weak evidence and missing citations. Uses Featherless when `FEATHERLESS_API_KEY` is configured.
- Source Quality Agent: applies deterministic scoring guardrails and admission filters.
- Consensus Moderator Agent: resolves disagreement and returns the final verdict. Uses Gemini when `GEMINI_API_KEY` is configured.
- Explainer Node ("The Turn"): reports why/when consensus changed.

## Hackathon Partner Alignment

- CrewAI: orchestration framework.
- Gemini: intended reasoning and multimodal provider.
- Featherless: intended open-source Skeptic Agent provider.
- Vultr: deployment target.
- Speechmatics: optional stretch goal for voice/audio input.

The implementation tries free DuckDuckGo search first and can optionally use Gemini for evidence suggestions. It refuses to produce a factual verdict without live cited evidence. For local UI demos only, set `FACTLENS_ALLOW_OFFLINE_FALLBACK=1`.

## Real Evidence Mode

For judging or production-like runs:

- Keep `FACTLENS_ALLOW_OFFLINE_FALLBACK=0`.
- Install dependencies from `requirements.txt` so DuckDuckGo search and PDF/image extraction work.
- Set `GEMINI_API_KEY` for real Intake and Moderator LLM reasoning.
- Set `FEATHERLESS_API_KEY` for the cross-model Skeptic Agent.
- Optional: set `TAVILY_API_KEY` only if DuckDuckGo is blocked or unreliable.

If all live evidence providers fail, the API returns `needs_live_evidence` with confidence `0` instead of inventing sources.

## Local Setup

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install --upgrade pip
.\.venv\Scripts\python -m pip install -r requirements.txt
Copy-Item .env.example .env
.\.venv\Scripts\python api\main.py
```

Open:

```text
http://127.0.0.1:8000
```

## API

```text
POST /api/verify
GET /api/runs/{run_id}/events
GET /health
```

`POST /api/verify` now includes additive mesh fields:
- `stage_metrics`
- `decision_trace`
- `the_turn`
- `evidence_rejections`

`POST /api/verify` accepts multipart form fields:

- `text`: claim or context
- `input_type`: `text`, `pdf`, `image`, or detected file type
- `file`: optional PDF/image upload

## Demo Script

1. Paste a claim or upload a PDF/image.
2. Click `Run Crew`.
3. Show the War Room agent cards filling with findings.
4. Explain how the Skeptic Agent and Source Quality Agent prevent black-box answers.
5. Show final verdict, confidence, source list, disagreement summary, and recommendation.

## Search Strategy

The agents do not rely on Tavily. Evidence lookup order is:

1. DuckDuckGo search through `ddgs`.
2. Optional Gemini evidence helper when `GEMINI_API_KEY` is set.
3. Optional Tavily fallback when `TAVILY_API_KEY` is set.
4. Explicit offline fallback only when `FACTLENS_ALLOW_OFFLINE_FALLBACK=1`.

For real hackathon judging, keep offline fallback disabled. If no live source returns evidence, the system returns `needs_live_evidence` instead of fabricating a verdict.

## Mesh Runtime Config

Use these env knobs to control quality/speed and guardrails:

- `MODEL_POLICY=quality|balanced|fast`
- `FACTLENS_MIN_EVIDENCE_COUNT`
- `FACTLENS_MIN_TRUSTED_SOURCES`
- `FACTLENS_MIN_DOMAIN_DIVERSITY`
- `FACTLENS_STAGE_TIMEOUT_SEC`
- `FACTLENS_VERIFIER_TRIGGER_CONFIDENCE_GATE`
- `FACTLENS_DOMAIN_BLACKLIST`
