# FactLens Optimization Playbook

## Purpose
This file defines a clear, repeatable path to move FactLens from “partially working” to an **optimal, production-ready independent + collaborative agent system**.

It is intentionally framework-agnostic and can be reused even if internal code structure changes.

---

## 1) Core Goals

### G1. Independent Agent Competence
Each agent must perform its own task robustly without hidden dependency on downstream correction.
- Intake extracts a precise checkable claim.
- Router maps correct domain + claim type.
- Retrieval agents fetch relevant evidence independently.
- Scoring agent applies deterministic policy correctly.
- Moderator synthesizes evidence and gives bounded-confidence verdict.

### G2. Collaborative System Intelligence
Agents must cooperate as one system, not isolated steps.
- Shared claim-intent is propagated through all nodes.
- Retrieval outputs are complementary (not redundant).
- Aggregator preserves provenance and channel metadata.
- Skeptic + Moderator run conflict-aware arbitration.

### G3. Deterministic Guardrails
LLM creativity should not bypass policy.
- Fixed scoring formula and thresholds.
- Domain credibility tiers + temporal decay + diversity penalties.
- Required minimums for trusted count and diversity.

### G4. Verifiability and Observability
Every verdict should be explainable and auditable.
- Stage metrics, per-node I/O summaries, and final rationale.
- Run history and cache traceability.
- Explicit failure mode classification (`needs_live_evidence`, `insufficient_evidence`, etc.).

---

## 2) Target System States

Define states to measure maturity objectively.

### S0: Bootstrapped
- Pipeline runs end-to-end for at least one claim.
- Basic events emitted.

### S1: Functionally Connected
- All major nodes execute in sequence/graph.
- Node I/O visible and coherent.
- No missing handler, deadlock, or wiring errors.

### S2: Evidence-Reliable
- For standard factual claims, retrieval returns enough usable evidence.
- Trusted-source count and domain diversity usually meet minima.
- Fallbacks and retries behave predictably.

### S3: Decision-Reliable
- Verdict quality stable across repeated runs.
- Confidence aligns with evidence quality.
- Comparator and moderator are not producing contradictory outcomes.

### S4: Adaptive + Memory-Reliable
- Cache/history behavior consistent.
- Similar-claim retrieval helps but does not contaminate live verification.
- Promotion/update logic is auditable.

### S5: Demo/Production-Ready
- UI reflects true backend state.
- No misleading metrics labels.
- Run cancellation, retry, and history views are reliable.

---

## 3) Independent vs Collaborative Acceptance Criteria

### Independent Agent Criteria
For each agent:
1. Input contract is explicit.
2. Output contract is explicit.
3. Failure output is explicit (no silent empty behavior).
4. Works on at least 5 varied claims in isolation tests.

### Collaborative System Criteria
For the full workflow:
1. Shared claim-intent object used by all retrieval nodes.
2. Aggregation preserves source/channel/domain/timestamp metadata.
3. Scoring policy applied after aggregation and dedupe.
4. Retry loop changes query strategy, not just repeats same query.
5. Final verdict trace explains “what changed” and “why”.

---

## 4) Critical Anti-Patterns to Avoid

1. Hardcoding logic for one benchmark claim.
2. Mixing event-driven and sync flows without a clear ownership model.
3. Using weighted score labels as if they were raw credibility.
4. Letting empty retrieval silently pass downstream.
5. Overfitting thresholds without retrieval diagnostics.

---

## 4.1 Flexible Scoring Policy (Allowed) vs Heuristic Overfit (Not Allowed)

### Allowed (Adaptive but Universal)
1. Dynamic calibration from historical run distributions (percentile-based gates).
2. Claim-class-aware weighting profiles (`statistical`, `breaking_news`, `general`) with versioned config.
3. Data-quality-aware threshold scaling based on retrieval confidence and provider health.
4. Automatic promotion/demotion of source trust only from aggregate evidence over many runs.
5. Confidence calibration using measured error rates from regression matrix outcomes.

### Not Allowed (Heuristic/Overfit)
1. Claim-specific rules like fixed country/year/rank exceptions.
2. Single-claim threshold tweaks (e.g., adjusting RS/EW only to pass one benchmark).
3. Manual whitelist boosts tied to one scenario.
4. Hidden fallback verdict logic that bypasses evidence constraints.

### Mandatory Guardrails for Any Scoring Upgrade
1. Every scoring change must be config-driven and versioned (`policy_version`).
2. Must pass full claim-class matrix, not only target claim.
3. Must publish before/after deltas:
   - `accepted_count`, `trusted_count`, `domain_diversity`,
   - verdict stability,
   - confidence calibration error.
4. Must include rollback path to previous policy version.

---

## 5) Universal Test Matrix

Run these claim classes on every major change:

1. **Static science fact**
- Example: “The Earth revolves around the Sun.”

2. **Numeric ranking/time-bound claim**
- Example: “India is the 4th largest economy in 2026.”

3. **Population/demography claim**
- Example: “India’s population surpassed China in 2023.”

4. **Health/statistics claim**
- Example: “Global measles cases increased in 2024.”

5. **Ambiguous/opinion-like claim**
- Example: “This policy is the best for growth.”

For each run, log:
- verdict
- confidence
- accepted_count
- trusted_count
- domain_diversity
- rejection breakdown
- total latency + stage latency

---

## 6) Long Prompt for Iterative Refinement

Use the following prompt for internal review loops (LLM-assisted debugging/planning):

```text
You are the system architect and reliability auditor for a multi-agent fact-checking pipeline.

Your task:
1) Analyze the latest run logs, node outputs, and scoring metrics.
2) Identify whether failure is caused by:
   - retrieval recall failure,
   - evidence quality filtering,
   - agent contract mismatch,
   - orchestration/wiring bug,
   - or moderation/comparator inconsistency.
3) Propose the smallest high-impact patch set that improves universal reliability (not benchmark-specific hacks).
4) For each patch, provide:
   - expected impact,
   - risk,
   - rollback plan,
   - and exact success metric.
5) Re-run the same test matrix and compare before/after deltas.

Constraints:
- Do not hardcode for one claim or one country/year.
- Keep deterministic guardrails intact.
- Preserve explainability and provenance.
- If retrieval is empty, explicitly surface provider-level failure reasons.

Output format:
- Root cause summary
- Patch plan (P1/P2/P3)
- Measured outcomes table
- Go/No-Go decision for next iteration
```

---

## 7) Iteration Loop (Operational)

Use this strict loop:

1. Baseline snapshot
- Save current test matrix outcomes.

2. Single-change patch
- Change one subsystem at a time (retrieval, scoring, orchestration, UI).

3. Smoke + matrix rerun
- Verify no regression in other claim classes.

4. Compare deltas
- Accept only if reliability improves without new critical regressions.

5. Promote
- Keep change, document rationale.

6. Repeat until S5.

---

## 8) Exit Conditions (Optimal State)

Project is “optimal enough” when:
- >=90% of matrix runs produce non-empty evidence pools.
- >=80% of factual runs achieve valid verdicts with clear trace.
- Confidence calibration aligns with evidence quality.
- No critical orchestration/runtime errors in repeated smoke runs.
- UI accurately mirrors backend execution and metrics.

---

## 9) Immediate Next Steps

1. Stabilize one orchestration mode (sync or event-driven), not mixed.
2. Add provider-level retrieval diagnostics in event payloads.
3. Add per-claim-class regression test script and persist results.
4. Reintroduce advanced features only after S2 is stable.

---

## 10) Current Failure Snapshot (Latest Smoke)

- **Timestamp:** 2026-05-18 18:43 IST
- **Claim:** `India is the 4th largest economy in 2026`
- **Run ID:** `a1992a16-0835-46d9-8a0a-7c57117739c5`
- **Outcome:** `insufficient_evidence` (confidence `25%`, final sources `0`)
- **Observed pattern:** retrieval sparsity (web/primary empty, extractor insufficient)

### Interpretation
- Intake and routing are functioning.
- Arbitration logic is functioning safely.
- Main blocking issue is still **retrieval recall/coverage** before scoring.

### Priority Fix Order (Universal, Non-Hardcoded)
1. Add per-provider diagnostics in retrieval events (success/empty/error/timeout).
2. Add query-variant retry inside each retrieval node (same intent, paraphrased forms).
3. Improve structured evidence relevance scoring (numeric/temporal match-aware relevance, not token overlap only).
4. Re-run full matrix and track deltas (`accepted_count`, `trusted_count`, `domain_diversity`, `sources`).

## Latest Full Smoke Update (Old Workflow)

- Timestamp: 2026-05-18 18:54:26 +05:30
- Claim: `India is the 4th largest economy in 2026`
- Run ID: `9beb0cdd-2bdd-4e8a-b6e0-015b5b4423c4`
- Verdict: `insufficient_evidence`
- Confidence: `25%`
- Final Sources: `0`
- Runtime: `3.985s`
- Observation: Retrieval remains sparse; downstream scoring/moderation are functioning conservatively.

## Current Audit Snapshot

- Audit Time: 2026-05-18 19:30:12 +05:30
- Validated Workflow Path: Old orchestrator path (actlens_crew/orchestrator.py) only
- Latest Full Smoke Claim: India is the 4th largest economy in 2026
- Run ID: $run
- Verdict: insufficient_evidence
- Confidence: 25%
- Final Sources:  
- Runtime: 2.543s

### Diagnosis
- Independent node execution: intake/router/scoring/moderation path runs.
- Collaborative outcome quality: blocked by retrieval sparsity (evidence recall/coverage).
- Scoring behavior: conservative guardrails, not root-cause failure.

### Rules for Next Iteration
1. Keep old workflow architecture unchanged unless break/fix is mandatory.
2. Improve retrieval recall first, then recalibrate scoring.
3. No single-claim tuning or heuristic hardcoding.
4. Validate against full claim-class matrix before promoting changes.
