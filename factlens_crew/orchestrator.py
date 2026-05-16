"""Collaborative multi-agent verification workflow."""

from __future__ import annotations

import concurrent.futures
import os
import re
import time
import uuid
from typing import Any, Callable, Dict, List
from pydantic import BaseModel, ValidationError

from .events import event_store
from .llm import compact_sources_for_prompt, generate_featherless_json, generate_gemini_json
from .memory import MemoryStore, normalize_for_cache, cache_key as build_cache_key
from .schemas import (
    AgentReport,
    AgentTraceLog,
    ClaimMetadata,
    EvidenceItem,
    VerificationState,
    WarRoomEvent,
)
from .tools import (
    classify_claim_domain,
    dedupe_sources,
    extract_claim_facts,
    extract_file_text,
    gather_api_evidence,
    gather_web_scrape_evidence,
    has_live_evidence_sources,
    live_sources,
    normalize_text,
    offline_fallback_message,
    root_domain,
    search_primary_sources,
    search_web_routed,
    temporal_weight,
)

try:
    import crewai as _crewai  # type: ignore
except Exception:
    _crewai = None


def run_factlens_crew(
    text: str = "",
    file_path: str = "",
    input_type: str = "text",
    run_id: str | None = None,
    cache_mode: str | None = None,
    force_live_recheck: bool = False,
    cancel_check: Callable[[], bool] | None = None,
) -> Dict[str, Any]:
    run_id = run_id or str(uuid.uuid4())
    workflow = FactLensCrewWorkflow(
        run_id=run_id,
        cache_mode_override=cache_mode,
        force_live_recheck=force_live_recheck,
        cancel_check=cancel_check,
    )
    return workflow.run(text=text, file_path=file_path, input_type=input_type)


class AuditorContract(BaseModel):
    trust_score_t: float
    accepted_count: int
    rejected: Dict[str, int]


class RunCancelledError(Exception):
    """Raised when a run is cancelled by user request."""


class FactLensCrewWorkflow:
    def __init__(
        self,
        run_id: str,
        cache_mode_override: str | None = None,
        force_live_recheck: bool = False,
        cancel_check: Callable[[], bool] | None = None,
    ) -> None:
        self.run_id = run_id
        self.crewai_available = _crewai is not None
        self.state = VerificationState()
        self.model_policy = os.getenv("MODEL_POLICY", "quality").strip().lower()
        if self.model_policy not in {"quality", "balanced", "fast"}:
            self.model_policy = "quality"
        self.memory = MemoryStore()
        self.cache_mode = (cache_mode_override or os.getenv("CACHE_MODE", "off")).strip().lower()
        if self.cache_mode not in {"off", "read", "write", "read_write", "update", "auto"}:
            self.cache_mode = "off"
        self.cache_ttl_sec = int(os.getenv("CACHE_TTL_SECONDS", "86400"))
        self.session_id = os.getenv("FACTLENS_SESSION_ID", str(uuid.uuid4()))
        self.policy_version = os.getenv("FACTLENS_POLICY_VERSION", "v1")
        self.force_live_recheck = bool(force_live_recheck)
        self.similar_claims: List[Dict[str, Any]] = []
        self.change_log: List[Dict[str, str]] = []
        self.cache_hit = False
        self.cache_decision = self.cache_mode
        self.cancel_check = cancel_check
        self._cancel_emitted = False

    def run(self, text: str = "", file_path: str = "", input_type: str = "text") -> Dict[str, Any]:
        self._event("System", "started", "FactLens Crew run started", {"crewai_available": self.crewai_available})
        raw_text = self._load_input_text(text=text, file_path=file_path, input_type=input_type)
        claim_hint = self._best_claim(raw_text)
        claim_norm = normalize_for_cache(claim_hint)
        self.memory.start_run(
            self.run_id,
            claim_raw=raw_text[:2000],
            claim_normalized=claim_norm,
            input_type=input_type,
            session_id=self.session_id,
            policy_version=self.policy_version,
        )

        auto_seed_similar = self.memory.find_similar_claims(claim_norm, claim_raw=claim_hint, limit=1) if claim_norm else []
        self.cache_decision = self._resolve_cache_decision(claim_norm, auto_seed_similar)

        if self.cache_decision in {"read", "read_write"} and claim_norm and not self.force_live_recheck:
            cached = self.memory.cache_get(claim_norm, policy_version=self.policy_version, ttl_sec=self.cache_ttl_sec)
            if cached:
                self.cache_hit = True
                cached["run_id"] = self.run_id
                cached["cache"] = {
                    "mode": self.cache_mode,
                    "decision": self.cache_decision,
                    "hit": True,
                    "force_live_recheck": False,
                    "claim_key": build_cache_key(claim_norm, self.policy_version),
                    "policy_version": self.policy_version,
                    "ttl_seconds": self.cache_ttl_sec,
                }
                cached["input_claim_raw"] = claim_hint
                cached["input_claim_normalized"] = claim_norm
                self._event("System", "completed", "Cache hit served existing verdict", {"claim_key": claim_norm})
                cached["events"] = event_store.list(self.run_id)
                if self.cache_decision in {"write", "read_write", "update"}:
                    self.memory.finish_run(self.run_id, cached)
                return cached

        intake = self._stage_timed("intake", lambda: self._intake_agent(raw_text))
        claim_norm = normalize_for_cache(intake["claim"])
        self.similar_claims = self.memory.find_similar_claims(claim_norm, claim_raw=intake["claim"], limit=3) if claim_norm else []
        if self.similar_claims:
            self._event("Memory", "completed", "Found similar historical claims for retrieval bootstrap.", {"similar": self.similar_claims})
        claim_mode = self._claim_mode_agent(intake["claim"])
        self.state.metadata = ClaimMetadata(
            normalized_claim=intake["claim"],
            is_checkable=bool(intake["checkable"]),
            domain_category="general",
            target_entities=self._extract_entities(intake["claim"]),
            claim_type=claim_mode,
        )
        if not intake["checkable"]:
            final = self._final_response(
                verdict="insufficient_evidence",
                confidence=25,
                agent_reports=[intake["report"]],
                sources=[],
                disagreements=["Input did not contain a clearly checkable factual claim."],
                explanation="The Intake Agent could not isolate a verifiable factual claim.",
                recommendation="Submit a specific factual claim with enough context to verify.",
            )
            self._event("System", "completed", "Run completed with insufficient input", final)
            if self.cache_decision in {"write", "read_write", "update"}:
                self.memory.finish_run(self.run_id, final)
            return final

        claim = intake["claim"]
        domain_route = self._stage_timed("domain_router", lambda: self._domain_router_agent(claim))
        self.state.metadata.domain_category = domain_route["domain"]

        tri_reports = self._stage_timed("tri_search", lambda: self._tri_search_cluster(claim, domain_route["domain"]))
        web_scout = tri_reports["web"]
        archive_hunter = tri_reports["archive"]
        data_extractor = tri_reports["extractor"]
        tri_consistency = self._stage_timed(
            "tri_consistency",
            lambda: self._tri_consistency_agent(claim, web_scout, archive_hunter, data_extractor),
        )
        gathered_sources = self._stage_timed(
            "evidence_aggregation",
            lambda: self._aggregate_evidence_agent(claim, web_scout, archive_hunter, data_extractor, tri_consistency),
        )
        self.state.raw_evidence_pool = gathered_sources

        if not live_sources(gathered_sources):
            self._event(
                "System",
                "needs_live_evidence",
                offline_fallback_message(),
                {"live_providers_available": has_live_evidence_sources()},
            )
            agent_reports = [intake["report"], domain_route["report"], web_scout, archive_hunter, data_extractor, tri_consistency]
            final = self._final_response(
                verdict="needs_live_evidence",
                confidence=0,
                agent_reports=agent_reports,
                sources=[],
                disagreements=["No live cited evidence was available, so the crew did not fabricate a verdict."],
                explanation="The crew requires real cited evidence before arbitration.",
                recommendation=offline_fallback_message(),
            )
            self._event("System", "completed", "Run stopped until live evidence is available", final)
            if self.cache_decision in {"write", "read_write", "update"}:
                self.memory.finish_run(self.run_id, final)
            return final

        skeptic = self._stage_timed("skeptic", lambda: self._skeptic_agent(claim, gathered_sources))
        auditor = self._stage_timed("auditor", lambda: self._evidence_auditor_agent(claim, gathered_sources, skeptic))
        self.state.audited_evidence_pool = auditor.sources
        stat_guard = self._stage_timed("stat_comparator", lambda: self._stat_comparator_agent(claim, self.state.audited_evidence_pool))

        moderator = self._stage_timed(
            "moderator",
            lambda: self._moderator_agent(
                claim,
                [domain_route["report"], web_scout, archive_hunter, data_extractor, tri_consistency, skeptic, auditor],
                stat_guard,
            ),
        )
        reports = [intake["report"], domain_route["report"], web_scout, archive_hunter, data_extractor, tri_consistency, skeptic, auditor, stat_guard, moderator]

        if self._needs_rebuttal(skeptic=skeptic, moderator=moderator):
            self.state.retry_count += 1
            self._event("Moderator", "retry", "One rebuttal loop triggered", {"reason": moderator.summary})
            rebuttal = self._stage_timed(
                "rebuttal_search",
                lambda: self._web_scout_agent(
                    f"{claim} official source dispute {(skeptic.findings[0] if skeptic.findings else '')}",
                    domain_route["domain"],
                    max_results=6,
                ),
            )
            merged = dedupe_sources(gathered_sources + rebuttal.sources)
            tri_consistency = self._stage_timed(
                "tri_consistency_recheck",
                lambda: self._tri_consistency_agent(claim, rebuttal, archive_hunter, data_extractor),
            )
            skeptic = self._stage_timed("skeptic_recheck", lambda: self._skeptic_agent(claim, merged))
            auditor = self._stage_timed("auditor_recheck", lambda: self._evidence_auditor_agent(claim, merged, skeptic))
            self.state.audited_evidence_pool = auditor.sources
            stat_guard = self._stage_timed(
                "stat_comparator_recheck",
                lambda: self._stat_comparator_agent(claim, self.state.audited_evidence_pool),
            )
            moderator = self._stage_timed(
                "moderator_recheck",
                lambda: self._moderator_agent(
                    claim, [domain_route["report"], web_scout, archive_hunter, data_extractor, rebuttal, tri_consistency, skeptic, auditor], stat_guard
                ),
            )
            reports = [intake["report"], domain_route["report"], web_scout, archive_hunter, data_extractor, rebuttal, tri_consistency, skeptic, auditor, stat_guard, moderator]

        verdict = self._extract_verdict(moderator.summary)
        self.state.final_verdict = verdict
        self.state.confidence_score = float(moderator.confidence)
        self.state.the_turn = self._explainer_agent(claim, reports, moderator)
        self.change_log = self.memory.build_change_log(
            run_id=self.run_id,
            verdict=verdict,
            similar_claims=self.similar_claims,
            sources=[s.to_dict() for s in self.state.audited_evidence_pool[:12]],
        )

        final = self._final_response(
            verdict=verdict,
            confidence=moderator.confidence,
            agent_reports=reports,
            sources=self.state.audited_evidence_pool[:8],
            disagreements=skeptic.findings,
            explanation=moderator.summary,
            recommendation=self._recommendation(moderator.confidence, self.state.audited_evidence_pool),
        )
        self._event("System", "completed", "FactLens Crew run completed", final)
        if self.cache_decision in {"write", "read_write", "update"}:
            self.memory.finish_run(self.run_id, final)
        return final

    def _stage_timed(self, stage: str, fn):
        self._check_cancel()
        start = time.time()
        out = fn()
        self._check_cancel()
        self.state.stage_metrics[stage] = round(time.time() - start, 3)
        return out

    def _check_cancel(self) -> None:
        if self.cancel_check and self.cancel_check():
            if not self._cancel_emitted:
                self._event("System", "cancelled", "Run cancelled by user", {"run_id": self.run_id})
                self._cancel_emitted = True
            raise RunCancelledError(f"Run {self.run_id} cancelled")

    def _load_input_text(self, text: str, file_path: str, input_type: str) -> str:
        parts = [normalize_text(text)]
        if file_path:
            extracted = extract_file_text(file_path)
            if extracted:
                self._event("Intake", "file_extracted", f"Extracted text from {input_type} input", {"chars": len(extracted)})
                parts.append(extracted)
            else:
                self._event("Intake", "file_warning", f"No text extracted from {input_type} input")
        return normalize_text(" ".join(part for part in parts if part))

    def _intake_agent(self, raw_text: str) -> Dict[str, Any]:
        claim = self._best_claim(raw_text)
        checkable = bool(claim and len(claim.split()) >= 4 and not claim.endswith("?"))
        topic = self._topic_guess(claim)
        llm = self._llm_intake(raw_text)
        if llm:
            llm_claim = normalize_text(str(llm.get("claim") or ""))
            if llm_claim:
                claim = llm_claim[:500]
            if isinstance(llm.get("checkable"), bool):
                checkable = bool(llm["checkable"])
            topic = normalize_text(str(llm.get("topic") or topic)) or topic
        report = AgentReport(
            agent="Intake Agent",
            summary=claim if claim else "No claim extracted.",
            confidence=82 if checkable else 25,
            findings=[f"Topic guess: {topic}", "Claim is specific enough to verify." if checkable else "No clear factual claim found."],
        )
        self._trace("Intake Agent", "claim_extracted", "Extracted normalized claim and checkability gate.", 0, "llm+rules")
        self._event("Intake Agent", "completed", report.summary, {"checkable": checkable, "topic": topic})
        return {"claim": claim, "checkable": checkable, "report": report}

    def _domain_router_agent(self, claim: str) -> Dict[str, Any]:
        domain = classify_claim_domain(claim)
        claim_type = self._claim_type(claim)
        strategy_map = {
            "economy": "Route to macroeconomic sources and indicators.",
            "population": "Route to census/demography datasets.",
            "health": "Route to health institutions and medical journals.",
            "politics": "Route to official government/election records.",
            "science": "Route to scientific and institutional sources.",
            "general": "Use mixed official + reputable web retrieval.",
        }
        summary = f"Selected domain route: {domain}."
        findings = [strategy_map.get(domain, strategy_map["general"]), f"Semantic route type: {claim_type}."]
        report = AgentReport(agent="Domain Router Agent", summary=summary, confidence=84, findings=findings)
        self._trace("Domain Router Agent", "route_selected", f"Domain={domain}, claim_type={claim_type}.", 0, "semantic_router")
        self._event("Domain Router Agent", "completed", summary, {"domain": domain, "claim_type": claim_type})
        return {"domain": domain, "claim_type": claim_type, "report": report}

    def _tri_search_cluster(self, claim: str, domain: str) -> Dict[str, AgentReport]:
        self._check_cancel()
        claim_type = self.state.metadata.claim_type
        timeout_s = int(os.getenv("FACTLENS_STAGE_TIMEOUT_SEC", "15"))
        if self.model_policy == "fast":
            plan = {"web": 4, "archive": 3, "extractor": 3}
        elif self.model_policy == "balanced":
            plan = {"web": 6, "archive": 5, "extractor": 5}
        else:
            plan = {"web": 8, "archive": 7, "extractor": 7}
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
            futures = {}
            if claim_type == "breaking_news":
                futures["web"] = pool.submit(self._web_scout_agent, claim, domain, plan["web"] + 2)
                futures["archive"] = pool.submit(self._archive_hunter_agent, claim, domain, plan["archive"])
                futures["extractor"] = pool.submit(self._data_extractor_agent, claim, domain, plan["extractor"])
            elif claim_type == "statistical":
                futures["archive"] = pool.submit(self._archive_hunter_agent, claim, domain, plan["archive"] + 1)
                futures["extractor"] = pool.submit(self._data_extractor_agent, claim, domain, plan["extractor"] + 1)
                futures["web"] = pool.submit(self._web_scout_agent, claim, domain, max(2, plan["web"] - 2))
            else:
                futures["web"] = pool.submit(self._web_scout_agent, claim, domain, plan["web"])
                futures["archive"] = pool.submit(self._archive_hunter_agent, claim, domain, plan["archive"])
                futures["extractor"] = pool.submit(self._data_extractor_agent, claim, domain, plan["extractor"])
            out = {}
            for name, fut in futures.items():
                self._check_cancel()
                try:
                    out[name] = fut.result(timeout=max(4, timeout_s))
                except Exception:
                    out[name] = AgentReport(agent=f"{name.title()} Agent", summary="Timed out", confidence=20, findings=["Node timed out."], sources=[])
                    self._trace(f"{name.title()} Agent", "timeout", "Timed out while gathering evidence.", 0, "async_router")
            return out

    def _web_scout_agent(self, claim: str, domain: str, max_results: int = 8) -> AgentReport:
        query_claim = claim
        if self.similar_claims:
            hints = "; ".join(f"{x['claim_normalized']} ({x['verdict']})" for x in self.similar_claims[:2])
            query_claim = f"{claim} context from prior verified similar claims: {hints}"
            self._event("Memory", "completed", "Using prior similar-claim hints for retrieval.", {"from_history": True, "hints": hints})
        sources = search_web_routed(query_claim, domain=domain, max_results=max_results)
        channels = sorted({source.channel for source in sources}) if sources else []
        findings = [
            f"Collected {len(sources)} web results.",
            f"Channels used: {', '.join(channels) if channels else 'none'}.",
        ]
        report = AgentReport(agent="Web Scout Agent", summary="General web evidence gathered.", confidence=66 if sources else 30, findings=findings, sources=sources)
        self._trace("Web Scout Agent", "web_evidence_collected", "Gathered open-web evidence.", 0, "web_search")
        self._event("Web Research Agent", "completed", "Broad evidence collected from web search.", {"sources": len(sources)})
        return report

    def _archive_hunter_agent(self, claim: str, domain: str, max_results: int = 6) -> AgentReport:
        query_claim = claim
        if self.similar_claims:
            query_claim = f"{claim} official update latest {domain}"
        sources = search_primary_sources(f"{query_claim} {domain}", max_results=max_results)
        for source in sources:
            source.channel = source.channel or "web_search"
            source.domain = domain
            source.relevance = max(source.relevance, source.extract_score)
        findings = [f"Collected {len(sources)} archive/official results."]
        report = AgentReport(
            agent="Archive Hunter Agent",
            summary="Institutional and official evidence gathered.",
            confidence=75 if sources else 35,
            findings=findings,
            sources=sources,
        )
        self._trace("Archive Hunter Agent", "archive_evidence_collected", "Focused official source retrieval complete.", 0, "official_domain_search")
        self._event("Primary Source Agent", "completed", f"Found {len(sources)} higher-trust source(s).", {"trusted_sources": len(sources)})
        return report

    def _data_extractor_agent(self, claim: str, domain: str, max_results: int = 6) -> AgentReport:
        api_rows = gather_api_evidence(claim, domain)
        scrape_rows = gather_web_scrape_evidence(claim, domain, max_results=max_results // 2)
        sources = dedupe_sources(api_rows + scrape_rows)[:max_results]
        findings = [f"Collected {len(api_rows)} API rows and {len(scrape_rows)} deep-scraped rows."]
        report = AgentReport(
            agent="Data Extractor Agent",
            summary="Structured and deep-scraped evidence extracted.",
            confidence=74 if sources else 34,
            findings=findings,
            sources=sources,
        )
        self._trace("Data Extractor Agent", "deep_extraction_done", "Structured API plus scrape extraction complete.", 0, "api_data+web_scrape")
        self._event("Data Extractor Agent", "completed", report.summary, {"sources": len(sources)})
        return report

    def _tri_consistency_agent(
        self,
        claim: str,
        web_scout: AgentReport,
        archive_hunter: AgentReport,
        data_extractor: AgentReport,
    ) -> AgentReport:
        pools = {
            "web": web_scout.sources,
            "archive": archive_hunter.sources,
            "extractor": data_extractor.sources,
        }
        joined = {k: " ".join(f"{s.title} {s.snippet}" for s in v[:5]).lower() for k, v in pools.items()}
        sets = {k: set(re.findall(r"[a-z0-9]+", txt)) for k, txt in joined.items()}
        def jacc(a: set, b: set) -> float:
            if not a or not b:
                return 0.0
            return len(a & b) / max(1, len(a | b))
        score_wa = jacc(sets["web"], sets["archive"])
        score_we = jacc(sets["web"], sets["extractor"])
        score_ae = jacc(sets["archive"], sets["extractor"])
        consistency = (score_wa + score_we + score_ae) / 3.0
        findings = [
            f"web<->archive overlap={score_wa:.2f}",
            f"web<->extractor overlap={score_we:.2f}",
            f"archive<->extractor overlap={score_ae:.2f}",
            f"tri-consistency={consistency:.2f}",
        ]
        confidence = int(max(30, min(92, 35 + consistency * 60)))
        report = AgentReport(
            agent="Tri Consistency Agent",
            summary="Tri-search consistency measured across all retrieval nodes.",
            confidence=confidence,
            findings=findings,
            sources=dedupe_sources(web_scout.sources + archive_hunter.sources + data_extractor.sources)[:4],
        )
        self._trace("Tri Consistency Agent", "tri_overlap_computed", "Measured cross-channel consistency.", 0, "consistency")
        self._event("Tri Consistency Agent", "completed", report.summary, {"consistency": round(consistency, 3)})
        return report

    def _aggregate_evidence_agent(
        self,
        claim: str,
        web_scout: AgentReport,
        archive_hunter: AgentReport,
        data_extractor: AgentReport,
        tri_consistency: AgentReport,
    ) -> List[EvidenceItem]:
        all_rows = dedupe_sources(web_scout.sources + archive_hunter.sources + data_extractor.sources)
        by_channel: Dict[str, List[EvidenceItem]] = {"web_search": [], "api_data": [], "web_scrape": [], "google_search_api": []}
        for row in all_rows:
            by_channel.setdefault(row.channel, []).append(row)
        for rows in by_channel.values():
            rows.sort(key=lambda x: (x.credibility, x.relevance, x.extract_score), reverse=True)
        # balanced pick: avoid single-channel domination
        pick: List[EvidenceItem] = []
        for key in ("api_data", "google_search_api", "web_scrape", "web_search"):
            pick.extend(by_channel.get(key, [])[:3])
        pick = dedupe_sources(pick)
        if len(pick) < 8:
            pick = dedupe_sources(pick + all_rows)
        if tri_consistency.confidence < 45:
            # low consistency => keep only stronger items
            pick = [p for p in pick if p.credibility >= 60 or p.source_type in {"trusted", "reference"}]
        self._trace("Evidence Aggregator Agent", "aggregated_pool_built", "Merged all channels with consistency-aware balancing.", 0, "aggregation")
        self._event("Evidence Aggregator Agent", "completed", "Cross-channel evidence aggregated.", {"total": len(pick)})
        return pick[:18]

    def _skeptic_agent(self, claim: str, sources: List[EvidenceItem]) -> AgentReport:
        weak_sources = [source for source in sources if source.credibility < 60]
        findings = []
        if weak_sources:
            findings.append(f"{len(weak_sources)} source(s) have weak credibility scores.")
        if not findings:
            findings.append("No major contradiction found from available cited evidence.")
        findings.append("Decision policy: escalate only if direct contradiction or unresolved high-risk gap is detected.")
        llm = self._llm_skeptic(claim, sources)
        llm_findings = llm.get("findings") if isinstance(llm.get("findings"), list) else []
        llm_findings = [normalize_text(str(item)) for item in llm_findings if normalize_text(str(item))]
        if llm_findings:
            findings = llm_findings[:5]
        confidence = 68 if findings else 45
        if isinstance(llm.get("confidence"), int):
            confidence = max(0, min(100, int(llm["confidence"])))
        report = AgentReport(
            agent="Skeptic Agent",
            summary=normalize_text(str(llm.get("summary") or "")) or "Counter-evidence and weak assumptions reviewed.",
            confidence=confidence,
            findings=findings,
            sources=sources[:3],
        )
        self._trace("Skeptic Agent", "skeptic_review", "Checked conflicts/weaknesses in evidence.", len(weak_sources), "reasoner")
        self._event("Skeptic Agent", "completed", report.summary, {"objections": findings})
        return report

    def _evidence_auditor_agent(self, claim: str, sources: List[EvidenceItem], skeptic: AgentReport) -> AgentReport:
        unique = dedupe_sources(sources)
        min_count = int(os.getenv("FACTLENS_MIN_EVIDENCE_COUNT", "4"))
        min_trusted = int(os.getenv("FACTLENS_MIN_TRUSTED_SOURCES", "2"))
        min_diversity = int(os.getenv("FACTLENS_MIN_DOMAIN_DIVERSITY", "3"))
        blacklist_raw = os.getenv("FACTLENS_DOMAIN_BLACKLIST", "")
        blacklist = {d.strip().lower() for d in blacklist_raw.split(",") if d.strip()}

        domain_counts: Dict[str, int] = {}
        rejected = {"blacklist": 0, "duplicate_penalty": 0, "low_score": 0}
        weighted: List[EvidenceItem] = []
        raw_scores = []
        for source in unique:
            domain = root_domain(source.url)
            if domain in blacklist:
                rejected["blacklist"] += 1
                continue
            domain_counts[domain] = domain_counts.get(domain, 0) + 1
            rank = domain_counts[domain]
            if rank == 1:
                diversity_mult = 1.0
            elif rank == 2:
                diversity_mult = 0.70
            elif rank == 3:
                diversity_mult = 0.50
            else:
                rejected["duplicate_penalty"] += 1
                continue
            tw = temporal_weight(source.url, claim=claim)
            rs = max(0.20, min(1.0, float(source.relevance or 0.5)))
            wi = max(0.05, min(1.0, source.credibility / 100.0))
            raw_score = wi * rs
            raw_scores.append(raw_score)
            ew = raw_score * tw * diversity_mult
            if ew < 0.10:
                rejected["low_score"] += 1
                continue
            source.credibility = int(round(max(0.0, min(1.0, ew)) * 100))
            weighted.append(source)

        rho = rejected["duplicate_penalty"] / max(1, len(unique))
        trust_t = ((sum(raw_scores) / max(1, len(raw_scores))) * (1 - rho)) * 100.0
        weighted.sort(key=lambda item: item.credibility, reverse=True)
        avg = int(sum(source.credibility for source in weighted) / len(weighted)) if weighted else 0
        trusted_count = sum(1 for source in weighted if source.source_type in {"trusted", "reference"} and source.credibility >= 60)
        diversity = len({root_domain(source.url) for source in weighted})

        findings = [
            f"Deduplicated to {len(unique)} source(s).",
            f"Usable evidence after filters: {len(weighted)} source(s).",
            f"Average weighted evidence score: {avg}/100.",
            f"Auditor Trust Score T={trust_t:.2f}.",
            f"Trusted sources={trusted_count}, root-domain diversity={diversity}.",
            "Scoring policy: T = (sum(w_i*R_i)/n) * (1-rho).",
        ]
        if len(weighted) < min_count:
            findings.append("Constraint miss: minimum evidence count not met.")
        if trusted_count < min_trusted:
            findings.append("Constraint miss: minimum trusted sources not met.")
        if diversity < min_diversity:
            findings.append("Constraint miss: minimum domain diversity not met.")
        if any("No live cited evidence" in finding for finding in skeptic.findings):
            findings.append("Live evidence is required before making a high-confidence verdict.")

        try:
            AuditorContract(
                trust_score_t=trust_t,
                accepted_count=len(weighted),
                rejected=rejected,
            )
        except ValidationError:
            weighted = []
            rejected["low_score"] += len(unique)
            trust_t = 0.0

        self.state.evidence_rejections = rejected
        report = AgentReport(
            agent="Evidence Auditor Agent",
            summary="Evidence scored for credibility, recency, relevance, and independence.",
            confidence=int(max(0, min(100, (avg + trust_t) / 2))),
            findings=findings,
            sources=weighted[:8],
        )
        self._trace(
            "Evidence Auditor Agent",
            "audited_evidence_pool_updated",
            f"Applied deterministic constraints and scoring; accepted={len(weighted)}.",
            sum(rejected.values()),
            "rule_validator",
        )
        self._event(
            "Source Quality Agent",
            "completed",
            report.summary,
            {"average_credibility": avg, "trust_score_t": round(trust_t, 2), "rejections": rejected},
        )
        return report

    def _stat_comparator_agent(self, claim: str, sources: List[EvidenceItem]) -> AgentReport:
        evidence_text = compact_sources_for_prompt(sources[:10])
        prompt = (
            "You are Numeric Comparator Agent. Compare claim against evidence snippets. "
            "No assumptions. No external knowledge. "
            "Return JSON keys only: verdict, confidence, rationale, extracted_assertions.\n"
            "Allowed verdict: supported, refuted, insufficient_evidence.\n\n"
            f"Claim:\n{claim}\n\nEvidence:\n{evidence_text}"
        )
        llm = generate_gemini_json(prompt, "GEMINI_MODERATOR_MODEL", "gemini-2.5-flash")
        verdict = normalize_text(str(llm.get("verdict") or "insufficient_evidence")).lower()
        if verdict not in {"supported", "refuted", "insufficient_evidence"}:
            verdict = "insufficient_evidence"
        conf = llm.get("confidence")
        confidence = int(conf) if isinstance(conf, int) else 45
        confidence = max(20, min(95, confidence))
        rationale = normalize_text(str(llm.get("rationale") or "No stable numeric comparison available from provided evidence."))
        assertions = llm.get("extracted_assertions") if isinstance(llm.get("extracted_assertions"), list) else []
        findings = [rationale] + [normalize_text(str(x)) for x in assertions[:4] if normalize_text(str(x))]
        summary = f"Numeric comparison verdict: {verdict}. {rationale}"
        report = AgentReport(
            agent="Stat Comparator Agent",
            summary=summary,
            confidence=confidence,
            findings=findings,
            sources=sources[:4],
        )
        self._trace("Stat Comparator Agent", "numeric_guardrail_applied", "Compared claim numbers/ranks against extracted evidence.", 0, "comparator")
        self._event("Stat Comparator Agent", "completed", summary, {"verdict": verdict, "confidence": confidence})
        return report

    def _moderator_agent(self, claim: str, reports: List[AgentReport], stat_guard: AgentReport | None = None) -> AgentReport:
        sources = dedupe_sources([row for report in reports for row in report.sources])
        min_gate = float(os.getenv("FACTLENS_VERIFIER_TRIGGER_CONFIDENCE_GATE", "65"))
        source_quality = int(sum(source.credibility for source in sources) / len(sources)) if sources else 0
        support_hits = sum(1 for source in sources if "support" in source.stance_hint.lower())
        refute_hits = sum(1 for source in sources if "refute" in source.stance_hint.lower())
        conflict = support_hits > 0 and refute_hits > 0
        has_trusted = any(source.source_type in {"trusted", "reference"} and source.credibility >= 60 for source in sources)

        if not live_sources(sources):
            verdict = "insufficient_evidence"
            confidence = 25
        elif source_quality < 45 or not has_trusted:
            verdict = "insufficient_evidence"
            confidence = max(25, source_quality)
        else:
            verdict = "supported" if refute_hits <= support_hits else "refuted"
            confidence = min(90, max(55, source_quality))

        llm = self._llm_moderator(claim, reports, sources)
        if live_sources(sources) and llm:
            llm_verdict = normalize_text(str(llm.get("verdict") or "")).lower()
            allowed = {"supported", "refuted", "needs_review", "insufficient_evidence"}
            if llm_verdict in allowed and confidence < min_gate:
                verdict = llm_verdict
            if isinstance(llm.get("confidence"), int):
                confidence = max(0, min(100, int(llm["confidence"])))
            llm_explanation = normalize_text(str(llm.get("explanation") or ""))
        else:
            llm_explanation = ""

        if stat_guard and self._claim_type(claim) == "statistical":
            sg = stat_guard.summary.lower()
            if "verdict: refuted" in sg:
                verdict = "refuted"
                confidence = max(confidence, stat_guard.confidence)
                llm_explanation = f"Numeric guardrail override: {stat_guard.summary}"
            elif "verdict: insufficient_evidence" in sg:
                verdict = "insufficient_evidence"
                confidence = min(confidence, stat_guard.confidence)
                llm_explanation = f"Numeric guardrail override: {stat_guard.summary}"

        summary = (
            f"Verdict: {verdict}. Claim reviewed: {claim}. "
            f"Source quality: {source_quality}/100. Conflict={conflict}."
        )
        if llm_explanation:
            summary = f"Verdict: {verdict}. {llm_explanation}"
        report = AgentReport(
            agent="Consensus Moderator Agent",
            summary=summary,
            confidence=confidence,
            findings=[report.summary for report in reports],
            sources=sources[:8],
        )
        self._trace("Consensus Moderator Agent", "final_arbitration", "Resolved verdict from audited evidence.", 0, "moderator")
        self._event("Consensus Moderator Agent", "completed", summary, {"verdict": verdict, "confidence": confidence, "conflict": conflict})
        return report

    def _needs_rebuttal(self, skeptic: AgentReport, moderator: AgentReport) -> bool:
        verdict = self._extract_verdict(moderator.summary)
        has_contradiction = any(token in finding.lower() for finding in skeptic.findings for token in ("contradiction", "refute", "conflict"))
        if self.state.retry_count >= 1:
            return False
        if verdict in {"needs_review", "insufficient_evidence"} and moderator.confidence < float(
            os.getenv("FACTLENS_VERIFIER_TRIGGER_CONFIDENCE_GATE", "65")
        ):
            return True
        return has_contradiction and moderator.confidence < 70

    def _explainer_agent(self, claim: str, reports: List[AgentReport], moderator: AgentReport) -> str:
        initial = ""
        final = self._extract_verdict(moderator.summary)
        for report in reports:
            if report.agent == "Consensus Moderator Agent":
                initial = self._extract_verdict(report.summary)
                break
        if not initial:
            initial = final
        decisive = reports[-2].sources[0].url if len(reports) >= 2 and reports[-2].sources else "no decisive source captured"
        return (
            f"Initial consensus leaned {initial}. "
            f"After evidence audit and conflict checks, final verdict became {final}. "
            f"Decisive signal came from {decisive}. "
            f"Domain diversity and trust penalties were applied before arbitration."
        )

    def _final_response(
        self,
        verdict: str,
        confidence: int,
        agent_reports: List[AgentReport],
        sources: List[EvidenceItem],
        disagreements: List[str],
        explanation: str,
        recommendation: str,
    ) -> Dict[str, Any]:
        claim_norm = normalize_for_cache(self.state.metadata.normalized_claim)
        ckey = build_cache_key(claim_norm, self.policy_version) if claim_norm else ""
        return {
            "run_id": self.run_id,
            "session_id": self.session_id,
            "framework": "CrewAI-compatible" if not self.crewai_available else "CrewAI",
            "verdict": verdict,
            "confidence": confidence,
            "agent_reports": [report.to_dict() for report in agent_reports],
            "sources": [source.to_dict() for source in sources],
            "disagreements": disagreements,
            "final_explanation": explanation,
            "recommendation": recommendation,
            "events": event_store.list(self.run_id),
            "stage_metrics": self.state.stage_metrics,
            "decision_trace": [row.to_dict() for row in self.state.trace_logs],
            "the_turn": self.state.the_turn,
            "evidence_rejections": self.state.evidence_rejections,
            "state": self.state.to_dict(),
            "cache": {
                "mode": self.cache_mode,
                "decision": self.cache_decision,
                "hit": self.cache_hit,
                "force_live_recheck": self.force_live_recheck,
                "claim_key": ckey,
                "policy_version": self.policy_version,
                "ttl_seconds": self.cache_ttl_sec,
            },
            "similar_claims": self.similar_claims,
            "provenance": {
                "from_history": bool(self.similar_claims),
                "live": True,
            },
            "change_log": self.change_log,
            "input_claim_raw": self.state.metadata.normalized_claim,
            "input_claim_normalized": claim_norm,
            "policy_version": self.policy_version,
            "timestamps": {
                "finished_at": int(time.time()),
            },
        }

    def _resolve_cache_decision(self, claim_norm: str, auto_seed_similar: List[Dict[str, Any]]) -> str:
        if self.force_live_recheck:
            # force bypasses read-hit serving; keep write semantics when mode supports it
            if self.cache_mode in {"read", "off"}:
                return "off"
            if self.cache_mode == "auto":
                return "update"
            return self.cache_mode
        if self.cache_mode != "auto":
            return self.cache_mode
        # AUTO policy:
        # 1) exact fresh cache -> read
        # 2) highly similar historical claim -> update (read hints + write fresh)
        # 3) default -> write
        if claim_norm:
            cached = self.memory.cache_get(claim_norm, policy_version=self.policy_version, ttl_sec=self.cache_ttl_sec)
            if cached:
                return "read"
        top_sim = float((auto_seed_similar[0].get("similarity") if auto_seed_similar else 0.0) or 0.0)
        if top_sim >= 0.70:
            return "update"
        return "write"

    def _trace(self, agent: str, action: str, rationale: str, rejected: int, tool: str) -> None:
        self.state.trace_logs.append(
            AgentTraceLog(agent_name=agent, action_taken=action, rationale=rationale, rejected_items_count=rejected, tool_used=tool)
        )

    def _event(self, agent: str, status: str, message: str, data: Dict[str, Any] | None = None) -> None:
        event_store.add(
            WarRoomEvent(
                run_id=self.run_id,
                agent=agent,
                status=status,
                message=message,
                data=data or {},
            )
        )

    @staticmethod
    def _best_claim(text: str) -> str:
        text = normalize_text(text)
        if not text:
            return ""
        chunks = re.split(r"(?<=[.!?])\s+", text)
        candidates = [chunk.strip() for chunk in chunks if len(chunk.split()) >= 4]
        if not candidates:
            return text[:500]
        candidates.sort(key=lambda chunk: (bool(re.search(r"\d| is | are | was | were | has | have ", chunk.lower())), len(chunk)), reverse=True)
        return candidates[0][:500]

    @staticmethod
    def _topic_guess(claim: str) -> str:
        low = claim.lower()
        if any(token in low for token in ("stock", "market", "company", "revenue", "payment")):
            return "business_finance"
        if any(token in low for token in ("disease", "health", "medicine", "covid", "doctor")):
            return "health"
        if any(token in low for token in ("climate", "earth", "river", "planet", "space")):
            return "science"
        return "general"

    @staticmethod
    def _extract_verdict(summary: str) -> str:
        match = re.search(r"Verdict:\s*([a-z_]+)", summary, flags=re.I)
        return match.group(1).lower() if match else "needs_review"

    @staticmethod
    def _recommendation(confidence: int, sources: List[EvidenceItem]) -> str:
        if confidence < 50:
            return "Treat this as unresolved and gather stronger primary sources before publishing."
        if not any(source.credibility >= 70 for source in sources):
            return "Use with caution because no high-trust primary source was found."
        return "Verdict is usable with cited evidence and source-quality caveats."

    @staticmethod
    def _extract_entities(claim: str) -> List[str]:
        tokens = re.findall(r"\b[A-Z][a-zA-Z]+\b", claim)
        return list(dict.fromkeys(tokens))[:8]

    @staticmethod
    def _claim_type(claim: str) -> str:
        low = claim.lower()
        if any(token in low for token in ("breaking", "today", "yesterday", "just now", "viral", "reported")):
            return "breaking_news"
        if re.search(r"\b\d+(\.\d+)?\b", low):
            return "statistical"
        return "general"

    @staticmethod
    def _claim_mode_agent(claim: str) -> str:
        prompt = (
            "Classify claim for retrieval planning. Return JSON with key claim_type only. "
            "Allowed values: statistical, breaking_news, general.\n\n"
            f"Claim: {claim}"
        )
        row = generate_gemini_json(prompt, "GEMINI_INTAKE_MODEL", "gemini-2.5-flash")
        claim_type = normalize_text(str(row.get("claim_type") or "")).lower()
        if claim_type in {"statistical", "breaking_news", "general"}:
            return claim_type
        return FactLensCrewWorkflow._claim_type(claim)

    @staticmethod
    def _llm_intake(raw_text: str) -> Dict[str, Any]:
        if not normalize_text(raw_text):
            return {}
        prompt = (
            "You are the Intake Agent in a fact-checking crew. Extract one checkable factual claim.\n"
            "Return JSON with keys: claim string, checkable boolean, topic string.\n\n"
            f"Input:\n{raw_text[:4000]}"
        )
        return generate_gemini_json(prompt, "GEMINI_INTAKE_MODEL", "gemini-2.5-flash")

    @staticmethod
    def _llm_skeptic(claim: str, sources: List[EvidenceItem]) -> Dict[str, Any]:
        if not sources:
            return {}
        if os.getenv("MODEL_POLICY", "quality").strip().lower() == "fast":
            return {}
        prompt = (
            "You are the Skeptic Agent. Challenge the evidence without inventing facts. "
            "Every objection must be grounded in the cited source snippets or explicitly say it is unresolved.\n"
            "Return JSON with keys: summary string, confidence integer, findings array of concise strings.\n\n"
            f"Claim: {claim}\n\nSources:\n{compact_sources_for_prompt(sources)}"
        )
        return generate_featherless_json(
            prompt,
            model_env="FEATHERLESS_SKEPTIC_MODEL",
            default_model=os.getenv("FEATHERLESS_MODEL", "meta-llama/Meta-Llama-3.1-8B-Instruct"),
        )

    @staticmethod
    def _llm_moderator(claim: str, reports: List[AgentReport], sources: List[EvidenceItem]) -> Dict[str, Any]:
        if not live_sources(sources):
            return {}
        if os.getenv("MODEL_POLICY", "quality").strip().lower() == "fast":
            return {}
        report_text = "\n".join(f"{report.agent}: {report.summary}" for report in reports)
        prompt = (
            "You are the Consensus Moderator Agent. Resolve the crew reports into a cautious verdict. "
            "Use only these verdicts: supported, refuted, needs_review, insufficient_evidence. "
            "Do not force a verdict when evidence is weak.\n"
            "Return JSON with keys: verdict string, confidence integer, explanation string.\n\n"
            f"Claim: {claim}\n\nReports:\n{report_text}\n\nSources:\n{compact_sources_for_prompt(sources)}"
        )
        # Escalate to heavy model only when requested and claim appears complex/conflicted.
        use_heavy = os.getenv("FACTLENS_MODERATOR_HEAVY_ENABLE", "1").strip().lower() in {"1", "true", "yes", "on"}
        has_conflict = any("conflict" in (r.summary or "").lower() for r in reports)
        if use_heavy and has_conflict:
            heavy = generate_featherless_json(
                prompt,
                model_env="FEATHERLESS_MODERATOR_HEAVY_MODEL",
                default_model="openai/gpt-oss-120b",
            )
            if heavy:
                return heavy
        return generate_gemini_json(prompt, "GEMINI_MODERATOR_MODEL", "gemini-2.5-flash")
