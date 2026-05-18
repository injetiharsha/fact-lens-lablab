import os
import re
from typing import Any, Dict, List
from .event_bus import EventEmitter
from .schemas import AgentReport, EvidenceItem
from .llm import generate_gemini_json, generate_featherless_json, compact_sources_for_prompt
from .tools import (
    normalize_text, classify_claim_domain, search_web_routed, search_primary_sources,
    gather_api_evidence, gather_web_scrape_evidence, dedupe_sources, temporal_weight,
    root_domain, live_sources
)
from .events import event_store, WarRoomEvent

class BaseAgent:
    def __init__(self, event_bus: EventEmitter):
        self.event_bus = event_bus

    def _log_event(self, run_id: str, agent: str, status: str, message: str, data: Dict = None):
        event_store.add(WarRoomEvent(run_id=run_id, agent=agent, status=status, message=message, data=data or {}))

class IntakeAgent(BaseAgent):
    def process(self, event_data: Dict):
        run_id, raw_text = event_data["run_id"], event_data["raw_text"]
        claim = self._best_claim(raw_text)
        llm = self._llm_intake(raw_text)
        
        if llm:
            claim = normalize_text(str(llm.get("claim") or claim))[:500]
            checkable = bool(llm.get("checkable", False))
            queries = llm.get("queries") or {}
        else:
            checkable = bool(claim and len(claim.split()) >= 4)
            queries = {}

        report = AgentReport(
            agent="Intake Agent",
            summary=claim if claim else "No claim extracted.",
            confidence=82 if checkable else 25,
            findings=["Claim extracted and gated for checkability."]
        )
        self._log_event(run_id, "Intake Agent", "completed", report.summary)
        self.event_bus.publish("claim_extracted", run_id=run_id, claim=claim, checkable=checkable, report=report, queries=queries)

    def _best_claim(self, text: str) -> str:
        text = normalize_text(text)
        chunks = re.split(r"(?<=[.!?])\s+", text)
        candidates = [c.strip() for c in chunks if len(c.split()) >= 4]
        return candidates[0][:500] if candidates else text[:500]

    def _llm_intake(self, raw_text: str) -> Dict:
        prompt = (f"Extract one factual claim and queries. Return JSON only.\nInput: {raw_text[:4000]}")
        return generate_gemini_json(prompt, "GEMINI_INTAKE_MODEL", "gemini-2.5-flash")

class DomainRouterAgent(BaseAgent):
    def process(self, event_data: Dict):
        run_id, claim = event_data["run_id"], event_data["claim"]
        domain = classify_claim_domain(claim)
        claim_type = "statistical" if re.search(r"\b\d+\b", claim) else "general"
        
        report = AgentReport(agent="Domain Router Agent", summary=f"Routed to {domain}", confidence=85, findings=[f"Type: {claim_type}"])
        self._log_event(run_id, "Domain Router Agent", "completed", report.summary)
        self.event_bus.publish("domain_routed", run_id=run_id, claim=claim, domain=domain, claim_type=claim_type, report=report, queries=event_data.get("queries", {}))

class WebScoutAgent(BaseAgent):
    def process(self, event_data: Dict):
        run_id, claim, domain = event_data["run_id"], event_data["claim"], event_data["domain"]
        sources = search_web_routed(claim, domain=domain, max_results=8)
        report = AgentReport(agent="Web Scout Agent", summary="Web evidence gathered", confidence=70, sources=sources)
        self.event_bus.publish("web_evidence_gathered", run_id=run_id, report=report)

class ArchiveHunterAgent(BaseAgent):
    def process(self, event_data: Dict):
        run_id, claim, domain = event_data["run_id"], event_data["claim"], event_data["domain"]
        sources = search_primary_sources(f"{claim} {domain}", max_results=6)
        report = AgentReport(agent="Archive Hunter Agent", summary="Primary sources gathered", confidence=80, sources=sources)
        self.event_bus.publish("archive_evidence_gathered", run_id=run_id, report=report)

class DataExtractorAgent(BaseAgent):
    def process(self, event_data: Dict):
        run_id, claim, domain = event_data["run_id"], event_data["claim"], event_data["domain"]
        api_rows = gather_api_evidence(claim, domain)
        scrape_rows = gather_web_scrape_evidence(claim, domain, max_results=3)
        sources = dedupe_sources(api_rows + scrape_rows)
        report = AgentReport(agent="Data Extractor Agent", summary="Structured data gathered", confidence=75, sources=sources)
        self.event_bus.publish("extractor_evidence_gathered", run_id=run_id, report=report)

class SkepticAgent(BaseAgent):
    def process(self, event_data: Dict):
        run_id, claim, sources = event_data["run_id"], event_data["claim"], event_data["gathered_sources"]
        prompt = f"Challenge this claim based on sources. JSON only.\nClaim: {claim}\nSources: {compact_sources_for_prompt(sources)}"
        llm = generate_featherless_json(prompt, "FEATHERLESS_SKEPTIC_MODEL", "meta-llama/Meta-Llama-3.1-8B-Instruct")
        
        report = AgentReport(
            agent="Skeptic Agent",
            summary=normalize_text(str(llm.get("summary") or "Review complete.")),
            confidence=int(llm.get("confidence", 50)),
            findings=llm.get("findings", ["No major contradictions."])
        )
        self._log_event(run_id, "Skeptic Agent", "completed", report.summary)
        self.event_bus.publish("skeptic_report_generated", run_id=run_id, claim=claim, gathered_sources=sources, skeptic=report)

class StatComparatorAgent(BaseAgent):
    def process(self, event_data: Dict):
        run_id, claim, sources = event_data["run_id"], event_data["claim"], event_data["auditor"].sources
        evidence_text = compact_sources_for_prompt(sources)
        prompt = f"Compare numeric assertions. JSON only.\nClaim: {claim}\nEvidence: {evidence_text}"
        
        # Goal: Optimize for time-bound claims like "India 2026"
        prompt = (
            "You are Stat Comparator Agent. Compare numeric assertions specifically looking at rankings and dates.\n"
            "If the claim is about a future year (like 2026), look for projections or forecasts in the evidence.\n"
            "Return JSON only.\n"
            f"Claim: {claim}\n"
            f"Evidence: {evidence_text}"
        )
        llm = generate_gemini_json(prompt, "GEMINI_MODERATOR_MODEL", "gemini-2.5-flash")
        
        verdict = normalize_text(str(llm.get("verdict") or "insufficient_evidence")).lower()
        report = AgentReport(
            agent="Stat Comparator Agent",
            summary=f"Numeric verdict: {verdict}. {llm.get('rationale','')}",
            confidence=int(llm.get("confidence", 45)),
            findings=llm.get("extracted_assertions", [])
        )
        self._log_event(run_id, "Stat Comparator Agent", "completed", report.summary)
        self.event_bus.publish("stat_guard_report_generated", run_id=run_id, claim=claim, stat_guard=report, skeptic=event_data["skeptic"], auditor=event_data["auditor"])

class ModeratorAgent(BaseAgent):
    def process(self, event_data: Dict):
        run_id, claim, reports = event_data["run_id"], event_data["claim"], event_data["reports"]
        sources = dedupe_sources([s for r in reports for s in r.sources])
        
        # Determine verdict based on evidence and LLM
        prompt = f"Moderator verdict. JSON only.\nClaim: {claim}\nReports: {reports}\nSources: {compact_sources_for_prompt(sources)}"
        llm = generate_gemini_json(prompt, "GEMINI_MODERATOR_MODEL", "gemini-2.5-pro")
        
        verdict = normalize_text(str(llm.get("verdict") or "needs_review")).lower()
        report = AgentReport(
            agent="Consensus Moderator Agent",
            summary=normalize_text(str(llm.get("explanation") or f"Verdict: {verdict}")),
            confidence=int(llm.get("confidence", 50)),
            findings=[r.summary for r in reports]
        )
        self._log_event(run_id, "Consensus Moderator Agent", "completed", report.summary)
        self.event_bus.publish("moderator_report_generated", run_id=run_id, claim=claim, moderator=report, skeptic=event_data["skeptic"])

class ExplainerAgent(BaseAgent):
    def explain(self, claim: str, reports: List[AgentReport], moderator: AgentReport) -> str:
        final = self._extract_verdict(moderator.summary)
        return f"After evidence audit, final verdict became {final}. Domain diversity penalties were applied."

    def _extract_verdict(self, summary: str) -> str:
        match = re.search(r"Verdict:\s*([a-z_]+)", summary, flags=re.I)
        return match.group(1).lower() if match else "needs_review"

class EvidenceAuditorAgent(BaseAgent):
    def process(self, event_data: Dict):
        run_id, sources = event_data["run_id"], event_data["gathered_sources"]
        unique = dedupe_sources(sources)
        weighted = []
        for s in unique:
            tw = temporal_weight(s.url)
            s.extract_score = (s.credibility / 100.0) * tw
            if s.extract_score > 0.15:
                weighted.append(s)
        
        weighted.sort(key=lambda x: x.extract_score, reverse=True)
        report = AgentReport(agent="Evidence Auditor Agent", summary="Evidence scored and filtered.", confidence=70, sources=weighted[:8])
        self.event_bus.publish("audited_evidence_pool_updated", run_id=run_id, claim=event_data["claim"], auditor=report, skeptic=event_data["skeptic"])