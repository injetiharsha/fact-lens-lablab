# FactLens Crew — Submission Draft

Use this as your copy-paste source for the lablab submission form.

## 1) Basic Information

### Project Title
`FactLens Crew: Agentic Evidence Mesh for Transparent Fact Verification`

### Short Description
FactLens Crew is a collaborative multi-agent fact-checking system that retrieves live evidence, challenges weak assumptions, applies deterministic source-quality scoring, and returns a transparent final verdict with confidence and cited sources.

### Long Description
FactLens Crew is built for hackathon-grade, production-minded verification workflows where one LLM alone is not enough. The system runs a stateful agent mesh: Intake, Domain Router, Web Retrieval, Primary Source Retrieval, Data Extraction, Tri-Consistency, Evidence Aggregation, Skeptic Review, Source Quality Scoring, Numeric Comparator, and Consensus Moderator.

The pipeline is designed to reduce black-box behavior. Every stage emits workflow events, and evidence passes through deterministic guardrails before contributing to verdicts. Scoring combines relevance, credibility tier, temporal recency, and domain diversity penalties. A skeptic pass and numeric comparator help catch contradictions, especially on statistical claims.

The UI shows the full workflow graph, per-stage progress, node details, and run history to make reasoning traceable for judges and users. The system also supports cache/memory patterns for repeated claims while preserving clear provenance between live and historical evidence.

This project targets the Collaborative Systems and Agentic Workflows tracks by demonstrating independent specialized nodes that coordinate, self-correct through bounded retry loops, and produce explainable outputs instead of opaque answers.

### Technology & Category Tags
- Collaborative Systems
- Agentic Workflows
- Intelligent Reasoning
- Multimodal
- CrewAI
- FastAPI
- Gemini
- Featherless

## 2) Cover Image and Presentation

### Cover Image
- Use a screenshot of `static/workflow.html` showing:
  - full pipeline graph
  - active node states
  - verdict and confidence

### Video Presentation
- 2-3 min recommended flow:
1. Problem + why naive LLM fact-checking fails.
2. Agent mesh overview (roles and handoffs).
3. Live run with a claim.
4. Show evidence scoring and final verdict.
5. Explain cache/memory + retry logic.

### Slide Presentation
- 5-8 slides:
1. Problem statement
2. System architecture
3. Agent responsibilities
4. Scoring/guardrails
5. Workflow UI + observability
6. Results and limitations
7. Future work

## 3) App Hosting & Code Repository

### Public GitHub Repository
`https://github.com/injetiharsha/fact-lens-lablab`

### Demo Application Platform
- Vultr (recommended), or equivalent public host

### Application URL
- Add deployed public URL here

## 4) Verification Checklist (Before Final Submit)

1. `.env` excluded from repo; `.env.example` complete.
2. App boot tested from clean environment.
3. Smoke test run completed with logs captured.
4. Demo video link and slides link added.
5. Public URL accessible without local setup.
