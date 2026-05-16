"""Shared response schemas for the FactLens Crew workflow."""

from __future__ import annotations

from typing import Any, Dict, List

from pydantic import BaseModel, Field


class DictModel(BaseModel):
    def to_dict(self) -> Dict[str, Any]:
        return self.model_dump()


class WarRoomEvent(DictModel):
    run_id: str
    agent: str
    status: str
    message: str
    data: Dict[str, Any] = Field(default_factory=dict)


class EvidenceItem(DictModel):
    title: str
    url: str
    snippet: str
    source_type: str = "web"
    channel: str = "web_search"
    domain: str = "general"
    stance_hint: str = "neutral"
    credibility: int = 50
    relevance: float = 0.5
    extract_score: float = 0.0


class AgentReport(DictModel):
    agent: str
    summary: str
    confidence: int
    findings: List[str] = Field(default_factory=list)
    sources: List[EvidenceItem] = Field(default_factory=list)


class ClaimMetadata(DictModel):
    normalized_claim: str = ""
    is_checkable: bool = False
    domain_category: str = "general"
    target_entities: List[str] = Field(default_factory=list)
    claim_type: str = "general"


class AgentTraceLog(DictModel):
    agent_name: str
    action_taken: str
    rationale: str
    rejected_items_count: int = 0
    tool_used: str = ""


class VerificationState(DictModel):
    metadata: ClaimMetadata = Field(default_factory=ClaimMetadata)
    raw_evidence_pool: List[EvidenceItem] = Field(default_factory=list)
    audited_evidence_pool: List[EvidenceItem] = Field(default_factory=list)
    trace_logs: List[AgentTraceLog] = Field(default_factory=list)
    retry_count: int = 0
    final_verdict: str | None = None
    confidence_score: float = 0.0
    stage_metrics: Dict[str, float] = Field(default_factory=dict)
    evidence_rejections: Dict[str, int] = Field(default_factory=dict)
    the_turn: str = ""
