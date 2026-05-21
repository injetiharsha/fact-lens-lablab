# FactLens Crew - Submission Draft

Use this as copy-ready content for the hackathon submission form.

## 1) Basic Information

### Project Title
`FactLens Crew: Agentic Evidence Mesh for Transparent Fact Verification`

### Short Description
FactLens Crew is a collaborative multi-agent fact-checking system that retrieves live evidence, scores source quality with deterministic guardrails, and returns transparent verdicts with confidence and citations.

### Long Description
FactLens Crew is designed for high-trust verification workflows where a single LLM is not enough. The system runs a staged agent mesh: Intake, Domain Router, Retrieval Trio (Web, Primary Sources, Data Extractor), Tri Consistency, Evidence Aggregation, Skeptic Review, Source Quality Scoring, Numeric Comparator, and Consensus Moderator.

Every stage emits events for observability, and evidence passes through scoring/quality gates before affecting verdicts. The UI exposes pipeline progress, node-level details, run timelines, and stored run history to keep decisions auditable and explainable.

The project aligns with Collaborative Systems and Agentic Workflows by demonstrating specialized agents coordinating through structured handoffs, bounded retries, and transparent arbitration.

### Technology and Category Tags
- Collaborative Systems
- Agentic Workflows
- Intelligent Reasoning
- Multimodal
- FastAPI
- Gemini
- Featherless

## 2) Cover Image and Presentation

### Cover Image
Use a screenshot of `workflow.html` showing:
- full pipeline graph
- active stage states
- final verdict and confidence

### Video Presentation
Suggested 2-3 minute flow:
1. Problem and motivation
2. Architecture and agent roles
3. Live claim run
4. Evidence scoring and final arbitration
5. Observability and memory/history

### Slide Presentation
Suggested 5-8 slides:
1. Problem
2. System architecture
3. Agent responsibilities
4. Scoring and guardrails
5. UI and telemetry
6. Results and limitations
7. Future improvements

## 3) App Hosting and Repository

### Public GitHub Repository
`https://github.com/injetiharsha/fact-lens-lablab`

### Demo Application Platform
- Vercel / Cloud Run / Vultr (choose final hosted target)

### Application URL
- Add final public URL

## 4) Final Checklist

1. `.env` is excluded from repo, `.env.example` is complete.
2. App starts cleanly in a fresh environment.
3. Smoke tests executed and verified.
4. Cover image, video, and slides are ready.
5. Public URL is accessible.
