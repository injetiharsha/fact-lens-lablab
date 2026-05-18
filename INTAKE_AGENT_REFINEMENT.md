# FactLens Intake Agent Refinement

**Date:** 2026-05-18  
**Status:** Implemented & Ready for Testing  
**Target Maturity:** S2 (Evidence-Reliable) per PROJECT_OPTIMIZATION_PLAYBOOK

## Overview

The Intake Agent is the first node in the FactLens fact-checking pipeline. It extracts a checkable factual claim from raw user input (text, PDF, or image). This document describes the refinements made to achieve independent agent competence and explicit error handling.

## Problem Statement

The original intake agent had several limitations:
1. **Silent Failures** - No explicit distinction between "not checkable" and "error"
2. **Weak Validation** - Checkability was determined by simple word count rules
3. **Opinion Detection** - No deterministic filtering for opinion/subjective claims
4. **Limited Diagnostics** - Reports didn't explain why claims were rejected
5. **Confidence Miscalibration** - All non-checkable claims got confidence=25

## Refinements Made

### 1. Explicit Input Contract (Deterministic Criteria)

The intake agent now applies four deterministic criteria to determine checkability:

```python
Criterion 1: has_content
- Requirement: Claim must exist and be non-empty
- Rationale: Cannot verify nothing

Criterion 2: has_min_words  
- Requirement: Minimum 4 words for specificity
- Rationale: Single words or fragments lack context

Criterion 3: is_statement
- Requirement: Claim must be a statement, not a question
- Rationale: "Is X true?" is not a claim, just a query

Criterion 4: is_not_pure_opinion
- Requirement: Claim must have factual basis, not pure opinion
- Rationale: "Best policy" without facts is opinion, not verifiable
```

**Checkable = ALL four criteria met**

### 2. Opinion Detection (`_is_opinion_only`)

New static method to detect pure opinion claims using deterministic rules:

```python
Opinion Markers (hard reject):
- "best", "worst", "greatest", "most beautiful"
- "i think", "i believe", "i feel"
- "should be", "ought to be", "is good", "is bad"
- "better policy", "best for"

Superlatives Without Facts:
- "most X" without factual markers (is, was, has, data, found, etc.)
```

**Examples:**
- ✗ "This policy is the best for growth" → pure opinion
- ✓ "Data shows policy X increased growth 10%" → factual claim

### 3. Detailed Failure Diagnostics

Each rejected claim now includes an explicit failure reason:

```python
Output Format:
{
    "claim": extracted_claim_text,
    "checkable": boolean,
    "report": {
        "summary": claim_summary,
        "confidence": 0-100,
        "findings": [
            "Topic: ...",
            "Failure reason OR success message",
            "Additional context"
        ]
    }
}

Failure Reasons (explicit):
- "No content extracted from input" (empty)
- "Claim too short (N words, minimum 4 required)" (too short)
- "Input appears to be a question, not a factual claim" (question)
- "Claim appears to be opinion or subjective statement" (opinion)
```

### 4. Confidence Calibration

Confidence scores now reflect specific claim characteristics:

```python
Checkable Claims:
- Base: 75 (claim passed all criteria)
- +5 for time-specific markers (year, date) → 80
- +5 for numeric markers → 80
- +5 for explicit factual language → 85

Non-Checkable Claims:
- Empty input: 0
- Any other reason: 25
```

### 5. Enhanced Event Emission

Events now include detailed criteria diagnostics:

```python
Event Data:
{
    "checkable": boolean,
    "topic": "science" | "health" | "business_finance" | "general",
    "criteria": {
        "has_content": boolean,
        "has_min_words": boolean,
        "is_statement": boolean,
        "is_not_opinion": boolean
    }
}
```

## Test Matrix (from Optimization Playbook)

The refined intake agent is tested on 5 standard claim classes:

### 1. Static Science Fact
**Input:** "The Earth revolves around the Sun."  
**Expected:** checkable=True, confidence≥70, topic="science"  
**Validation:** Passes all 4 criteria

### 2. Numeric Ranking/Time-Bound
**Input:** "India is the 4th largest economy in 2026."  
**Expected:** checkable=True, confidence≥75, topic="general"  
**Validation:** Numeric markers detected, time-specific boost applied

### 3. Population/Demography
**Input:** "India's population surpassed China in 2023."  
**Expected:** checkable=True, confidence≥70  
**Validation:** Specific factual claim with temporal marker

### 4. Health/Statistics
**Input:** "Global measles cases increased in 2024."  
**Expected:** checkable=True, confidence≥70  
**Validation:** Factual claim with change marker + year

### 5. Ambiguous/Opinion-Like
**Input:** "This policy is the best for growth."  
**Expected:** checkable=False, confidence≤25  
**Validation:** Fails is_not_opinion criterion

## Edge Cases Handled

| Case | Input | Expected Result |
|------|-------|-----------------|
| Empty | "" | checkable=False, confidence=0 |
| Too Short | "Sun is hot." | checkable=False (3 words) |
| Question | "Is Earth round?" | checkable=False (ends with ?) |
| Multiple Sentences | "A. B. [Long fact]. C." | Extracts longest/most factual |
| Very Long Claim | 1000+ chars | Truncated to 500 chars |
| Opinion + Numbers | "Best 10 ways to..." | checkable=False (opinion despite numbers) |

## Output Contract

### Success Case (Checkable Claim)
```json
{
    "claim": "Extracted normalized claim text",
    "checkable": true,
    "report": {
        "agent": "Intake Agent",
        "summary": "claim text",
        "confidence": 75-85,
        "findings": [
            "Topic: science",
            "Claim is specific enough to verify.",
            "LLM-enhanced extraction"
        ]
    }
}
```

### Failure Case (Non-Checkable)
```json
{
    "claim": "",
    "checkable": false,
    "report": {
        "agent": "Intake Agent",
        "summary": "No claim extracted from input.",
        "confidence": 0,
        "findings": [
            "Topic: general",
            "No content extracted from input"
        ]
    }
}
```

## Integration with Downstream Agents

The refined intake agent ensures downstream reliability:

1. **Metadata Propagation** - Normalized claim is passed to all nodes
2. **Domain Router** - Receives checkable claim with topic hint
3. **Retrieval Agents** - Route queries based on explicit topic classification
4. **Skeptic Agent** - Works with verified factual claims only
5. **Moderator** - Has clear baseline claim to evaluate

## Files Modified

- `factlens_crew/orchestrator.py`
  - `_intake_agent()` - Complete rewrite with 4-criteria validation
  - `_is_opinion_only()` - New helper for opinion detection
  - `_extract_entities()` - Fixed duplicate method

- `tests/test_intake_agent.py`
  - New comprehensive test suite
  - TestMatrix: 5 claim classes from playbook
  - EdgeCases: 6 edge case tests
  - CheckabilityLogic: Detailed criterion tests
  - TopicClassification: 4 topic classification tests
  - ClaimExtraction: Extraction quality tests

- `scripts/test_intake_endpoint.py`
  - New endpoint test script
  - Demonstrates all 5 claim classes + 3 edge cases
  - Reports detailed results and diagnostics

## Validation & Testing

### Local Unit Tests
```bash
python -m pytest tests/test_intake_agent.py -v
```

### Endpoint Test
```bash
python scripts/test_intake_endpoint.py
```

### Integration Test
```bash
python -m pytest tests/test_workflow.py -v
```

## Success Metrics (S2 Achievement)

| Metric | Target | Validation |
|--------|--------|-----------|
| Test matrix pass rate | ≥80% | 4+ of 5 claims classified correctly |
| Checkability accuracy | ≥85% | Opinion/non-factual correctly rejected |
| Confidence calibration | ±10% | True claims have higher confidence |
| Error rate | <5% | Silent failures eliminated |
| Downstream impact | No regression | Other agents work correctly |

## Backwards Compatibility

The refined intake agent maintains API compatibility:
- Input: same `_intake_agent(raw_text: str)` signature
- Output: same `{"claim": str, "checkable": bool, "report": AgentReport}` structure
- Existing tests updated to match new behavior
- No breaking changes to downstream agents

## Future Enhancements (S3+)

Post-refinement improvements could include:
1. **LLM-assisted fact grounding** - Validate claims against known facts
2. **Entity linking** - Link extracted entities to knowledge bases
3. **Claim normalization** - Standardize claim phrasings
4. **Temporal resolution** - Automatically detect time-bound claims
5. **Multi-language support** - Handle non-English input

## References

- `PROJECT_OPTIMIZATION_PLAYBOOK.md` - System maturity framework
- `README.md` - FactLens overview
- `factlens_crew/schemas.py` - Data models
- `factlens_crew/tools.py` - Utility functions
