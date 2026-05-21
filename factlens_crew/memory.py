"""Persistent memory store for runs, cache, similarity retrieval, and drift tracking."""

from __future__ import annotations

import json
import os
import sqlite3
import time
import uuid
import hashlib
import re
from pathlib import Path
from typing import Any, Dict, List


def _db_path() -> Path:
    raw = os.getenv("FACTLENS_MEMORY_DB", "").strip()
    if raw:
        return Path(raw)
    local_default = Path(__file__).resolve().parent.parent / "data" / "factlens_memory.sqlite3"
    try:
        local_default.parent.mkdir(parents=True, exist_ok=True)
        with open(local_default.parent / ".write_probe", "w", encoding="utf-8") as fp:
            fp.write("ok")
        (local_default.parent / ".write_probe").unlink(missing_ok=True)
        return local_default
    except Exception:
        # Serverless/runtime fallback (e.g., Vercel): use writable temp dir.
        return Path("/tmp") / "factlens_memory.sqlite3"


def _ensure_dir(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def normalize_for_cache(claim: str) -> str:
    claim = " ".join((claim or "").strip().lower().split())
    return claim


def cache_key(claim_normalized: str, policy_version: str) -> str:
    payload = f"{policy_version}::{claim_normalized}".encode("utf-8", errors="ignore")
    return hashlib.sha256(payload).hexdigest()


def extract_entities(claim: str) -> List[str]:
    return list(dict.fromkeys(re.findall(r"\b[A-Z][a-zA-Z]+\b", claim or "")))[:16]


def extract_numbers(text: str) -> List[str]:
    return list(dict.fromkeys(re.findall(r"\b\d+(?:\.\d+)?\b", text or "")))[:32]


class MemoryStore:
    def __init__(self) -> None:
        self.path = _db_path()
        _ensure_dir(self.path)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS runs (
                    run_id TEXT PRIMARY KEY,
                    session_id TEXT,
                    claim_raw TEXT,
                    claim_normalized TEXT,
                    cache_key TEXT,
                    policy_version TEXT,
                    input_type TEXT,
                    verdict TEXT,
                    confidence INTEGER,
                    started_at INTEGER,
                    ended_at INTEGER,
                    entities_json TEXT,
                    numbers_json TEXT,
                    evidence_summary TEXT,
                    stage_metrics_json TEXT,
                    result_json TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_runs_claim_norm ON runs(claim_normalized);
                CREATE INDEX IF NOT EXISTS idx_runs_ended_at ON runs(ended_at DESC);

                CREATE TABLE IF NOT EXISTS evidence (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT,
                    title TEXT,
                    url TEXT,
                    source_type TEXT,
                    channel TEXT,
                    domain TEXT,
                    credibility INTEGER,
                    relevance REAL,
                    snippet TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_evidence_run ON evidence(run_id);
                CREATE INDEX IF NOT EXISTS idx_evidence_url ON evidence(url);

                CREATE TABLE IF NOT EXISTS run_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT,
                    agent TEXT,
                    status TEXT,
                    message TEXT,
                    data_json TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_events_run ON run_events(run_id);

                CREATE TABLE IF NOT EXISTS trust_stats (
                    root_domain TEXT PRIMARY KEY,
                    seen_count INTEGER DEFAULT 0,
                    total_credibility INTEGER DEFAULT 0,
                    last_seen INTEGER DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS change_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT,
                    related_run_id TEXT,
                    what_changed TEXT,
                    why_changed TEXT,
                    created_at INTEGER
                );

                CREATE TABLE IF NOT EXISTS claim_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    claim_normalized TEXT,
                    policy_version TEXT,
                    run_id TEXT,
                    verdict TEXT,
                    confidence INTEGER,
                    quality_score REAL,
                    evidence_count INTEGER,
                    trusted_count INTEGER,
                    domain_diversity INTEGER,
                    is_current INTEGER DEFAULT 0,
                    reason_json TEXT,
                    created_at INTEGER
                );
                CREATE INDEX IF NOT EXISTS idx_snap_claim ON claim_snapshots(claim_normalized, policy_version, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_snap_current ON claim_snapshots(claim_normalized, policy_version, is_current);

                CREATE TABLE IF NOT EXISTS claim_heads (
                    claim_normalized TEXT,
                    policy_version TEXT,
                    current_snapshot_id INTEGER,
                    current_run_id TEXT,
                    updated_at INTEGER,
                    PRIMARY KEY(claim_normalized, policy_version)
                );
                """
            )
            # Lightweight migrations for existing DBs.
            for col_def in (
                "cache_key TEXT",
                "policy_version TEXT",
                "entities_json TEXT",
                "numbers_json TEXT",
                "evidence_summary TEXT",
            ):
                col = col_def.split()[0]
                try:
                    conn.execute(f"ALTER TABLE runs ADD COLUMN {col_def}")
                except sqlite3.OperationalError:
                    pass
            try:
                conn.execute("CREATE INDEX IF NOT EXISTS idx_runs_cache_key ON runs(cache_key)")
            except sqlite3.OperationalError:
                pass

    def start_run(
        self,
        run_id: str,
        claim_raw: str,
        claim_normalized: str,
        input_type: str,
        session_id: str = "",
        policy_version: str = "v1",
    ) -> None:
        now = int(time.time())
        ckey = cache_key(claim_normalized, policy_version) if claim_normalized else ""
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO runs(run_id, session_id, claim_raw, claim_normalized, cache_key, policy_version, input_type, started_at)
                VALUES(?,?,?,?,?,?,?,?)
                """,
                (run_id, session_id or str(uuid.uuid4()), claim_raw, claim_normalized, ckey, policy_version, input_type, now),
            )

    def finish_run(self, run_id: str, result: Dict[str, Any]) -> None:
        now = int(time.time())
        verdict = str(result.get("verdict") or "")
        confidence = int(result.get("confidence") or 0)
        claim_raw = str(result.get("input_claim_raw") or "")
        claim_norm = str(result.get("input_claim_normalized") or "")
        policy_version = str(result.get("policy_version") or "v1")
        ckey = cache_key(claim_norm, policy_version) if claim_norm else ""
        entities = extract_entities(claim_raw or claim_norm)
        numbers = extract_numbers(claim_raw or claim_norm)
        evidence_summary = " | ".join(
            f"{(s.get('title') or '')[:120]} :: {(s.get('snippet') or '')[:180]}"
            for s in (result.get("sources") or [])[:6]
        )
        stage_metrics = json.dumps(result.get("stage_metrics") or {}, ensure_ascii=True)
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE runs
                SET verdict=?, confidence=?, ended_at=?, claim_raw=COALESCE(NULLIF(?,''), claim_raw),
                    claim_normalized=COALESCE(NULLIF(?,''), claim_normalized),
                    cache_key=COALESCE(NULLIF(?,''), cache_key),
                    policy_version=COALESCE(NULLIF(?,''), policy_version),
                    entities_json=?, numbers_json=?, evidence_summary=?,
                    stage_metrics_json=?, result_json=?
                WHERE run_id=?
                """,
                (
                    verdict,
                    confidence,
                    now,
                    claim_raw,
                    claim_norm,
                    ckey,
                    policy_version,
                    json.dumps(entities, ensure_ascii=True),
                    json.dumps(numbers, ensure_ascii=True),
                    evidence_summary,
                    stage_metrics,
                    json.dumps(result, ensure_ascii=True),
                    run_id,
                ),
            )
            conn.execute("DELETE FROM evidence WHERE run_id=?", (run_id,))
            for row in result.get("sources") or []:
                conn.execute(
                    """
                    INSERT INTO evidence(run_id,title,url,source_type,channel,domain,credibility,relevance,snippet)
                    VALUES(?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        run_id,
                        str(row.get("title") or ""),
                        str(row.get("url") or ""),
                        str(row.get("source_type") or ""),
                        str(row.get("channel") or ""),
                        str(row.get("domain") or ""),
                        int(row.get("credibility") or 0),
                        float(row.get("relevance") or 0.0),
                        str(row.get("snippet") or ""),
                    ),
                )
            conn.execute("DELETE FROM run_events WHERE run_id=?", (run_id,))
            for ev in result.get("events") or []:
                conn.execute(
                    "INSERT INTO run_events(run_id,agent,status,message,data_json) VALUES(?,?,?,?,?)",
                    (
                        run_id,
                        str(ev.get("agent") or ""),
                        str(ev.get("status") or ""),
                        str(ev.get("message") or ""),
                        json.dumps(ev.get("data") or {}, ensure_ascii=True),
                    ),
                )
            self._update_trust_stats(conn, result.get("sources") or [])
            if claim_norm:
                self._upsert_claim_snapshot(conn, run_id, claim_norm, policy_version, result)

    def cache_get(self, claim_normalized: str, policy_version: str, ttl_sec: int = 86400) -> Dict[str, Any] | None:
        now = int(time.time())
        ckey = cache_key(claim_normalized, policy_version) if claim_normalized else ""
        if not ckey:
            return None
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT r.result_json AS result_json, r.ended_at AS ended_at
                FROM claim_heads h
                JOIN claim_snapshots s ON s.id = h.current_snapshot_id
                JOIN runs r ON r.run_id = s.run_id
                WHERE h.claim_normalized=? AND h.policy_version=? AND r.result_json IS NOT NULL
                LIMIT 1
                """,
                (claim_normalized, policy_version),
            ).fetchone()
            if not row:
                row = conn.execute(
                    """
                    SELECT result_json, ended_at
                    FROM runs
                    WHERE cache_key=? AND result_json IS NOT NULL
                    ORDER BY ended_at DESC LIMIT 1
                    """,
                    (ckey,),
                ).fetchone()
        if not row:
            return None
        ended_at = int(row["ended_at"] or 0)
        if ttl_sec > 0 and ended_at > 0 and (now - ended_at) > ttl_sec:
            return None
        try:
            return json.loads(row["result_json"])
        except Exception:
            return None

    def find_similar_claims(self, claim_normalized: str, claim_raw: str = "", limit: int = 3) -> List[Dict[str, Any]]:
        tokens = set(claim_normalized.split())
        if not tokens:
            return []
        q_entities = set(extract_entities(claim_raw))
        q_numbers = set(extract_numbers(claim_raw or claim_normalized))
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT run_id, claim_normalized, verdict, confidence, ended_at, result_json, entities_json, numbers_json, evidence_summary FROM runs WHERE result_json IS NOT NULL ORDER BY ended_at DESC LIMIT 300"
            ).fetchall()
        scored: List[Dict[str, Any]] = []
        for r in rows:
            other = str(r["claim_normalized"] or "")
            if not other or other == claim_normalized:
                continue
            other_tokens = set(other.split())
            if not other_tokens:
                continue
            sem = len(tokens & other_tokens) / max(1, len(tokens | other_tokens))
            try:
                e2 = set(json.loads(r["entities_json"] or "[]"))
            except Exception:
                e2 = set()
            try:
                n2 = set(json.loads(r["numbers_json"] or "[]"))
            except Exception:
                n2 = set()
            ent = (len(q_entities & e2) / max(1, len(q_entities | e2))) if (q_entities or e2) else 0.0
            num = (len(q_numbers & n2) / max(1, len(q_numbers | n2))) if (q_numbers or n2) else 0.0
            score = (0.60 * sem) + (0.25 * ent) + (0.15 * num)
            if score < 0.35:
                continue
            prior_evidence = []
            try:
                rs = json.loads(r["result_json"] or "{}")
                prior_evidence = rs.get("sources") or []
            except Exception:
                pass
            scored.append(
                {
                    "run_id": r["run_id"],
                    "claim_normalized": other,
                    "verdict": r["verdict"],
                    "confidence": int(r["confidence"] or 0),
                    "similarity": round(score, 3),
                    "semantic_similarity": round(sem, 3),
                    "entity_overlap": round(ent, 3),
                    "numeric_overlap": round(num, 3),
                    "prior_evidence": prior_evidence[:5],
                    "evidence_summary": str(r["evidence_summary"] or ""),
                    "ended_at": int(r["ended_at"] or 0),
                }
            )
        scored.sort(key=lambda x: (x["similarity"], x["confidence"], x["ended_at"]), reverse=True)
        return scored[:limit]

    def _upsert_claim_snapshot(
        self,
        conn: sqlite3.Connection,
        run_id: str,
        claim_normalized: str,
        policy_version: str,
        result: Dict[str, Any],
    ) -> None:
        now = int(time.time())
        verdict = str(result.get("verdict") or "")
        confidence = int(result.get("confidence") or 0)
        sources = result.get("sources") or []
        evidence_count = len(sources)
        trusted_count = sum(1 for s in sources if int(s.get("credibility") or 0) >= 80 or str(s.get("source_type") or "") == "trusted")
        domains = {self._root_domain(str(s.get("url") or "")) for s in sources if s.get("url")}
        domains.discard("")
        domain_diversity = len(domains)
        quality_score = self._quality_score(confidence, trusted_count, domain_diversity, evidence_count)
        score = quality_score

        prev = conn.execute(
            """
            SELECT s.id, s.run_id, s.verdict, s.confidence, s.quality_score, s.trusted_count, s.domain_diversity, s.evidence_count, r.result_json
            FROM claim_heads h
            JOIN claim_snapshots s ON s.id = h.current_snapshot_id
            LEFT JOIN runs r ON r.run_id = s.run_id
            WHERE h.claim_normalized=? AND h.policy_version=?
            LIMIT 1
            """,
            (claim_normalized, policy_version),
        ).fetchone()

        reason = self._snapshot_reason(prev, verdict, confidence, score, trusted_count, domain_diversity, evidence_count)
        cur = 1 if self._should_promote(prev, verdict, confidence, score, trusted_count, domain_diversity) else 0

        conn.execute(
            """
            INSERT INTO claim_snapshots(
                claim_normalized, policy_version, run_id, verdict, confidence, quality_score,
                evidence_count, trusted_count, domain_diversity, is_current, reason_json, created_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                claim_normalized,
                policy_version,
                run_id,
                verdict,
                confidence,
                float(score),
                evidence_count,
                trusted_count,
                domain_diversity,
                cur,
                json.dumps(reason, ensure_ascii=True),
                now,
            ),
        )
        snap_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])

        if prev is None:
            cur = 1
            conn.execute("UPDATE claim_snapshots SET is_current=1 WHERE id=?", (snap_id,))
            conn.execute(
                """
                INSERT INTO claim_heads(claim_normalized, policy_version, current_snapshot_id, current_run_id, updated_at)
                VALUES(?,?,?,?,?)
                ON CONFLICT(claim_normalized, policy_version) DO UPDATE SET
                    current_snapshot_id=excluded.current_snapshot_id,
                    current_run_id=excluded.current_run_id,
                    updated_at=excluded.updated_at
                """,
                (claim_normalized, policy_version, snap_id, run_id, now),
            )
            return

        if cur == 1:
            conn.execute(
                "UPDATE claim_snapshots SET is_current=0 WHERE claim_normalized=? AND policy_version=? AND id<>?",
                (claim_normalized, policy_version, snap_id),
            )
            conn.execute(
                """
                INSERT INTO claim_heads(claim_normalized, policy_version, current_snapshot_id, current_run_id, updated_at)
                VALUES(?,?,?,?,?)
                ON CONFLICT(claim_normalized, policy_version) DO UPDATE SET
                    current_snapshot_id=excluded.current_snapshot_id,
                    current_run_id=excluded.current_run_id,
                    updated_at=excluded.updated_at
                """,
                (claim_normalized, policy_version, snap_id, run_id, now),
            )
        else:
            # keep previous current head untouched; still store historical snapshot.
            pass

    @staticmethod
    def _quality_score(confidence: int, trusted_count: int, diversity: int, evidence_count: int) -> float:
        return (
            float(confidence)
            + (trusted_count * 3.0)
            + (diversity * 2.0)
            + (min(evidence_count, 12) * 0.5)
        )

    def _should_promote(
        self,
        prev: sqlite3.Row | None,
        verdict: str,
        confidence: int,
        quality_score: float,
        trusted_count: int,
        domain_diversity: int,
    ) -> bool:
        if prev is None:
            return True
        prev_conf = int(prev["confidence"] or 0)
        prev_q = float(prev["quality_score"] or 0.0)
        prev_trusted = int(prev["trusted_count"] or 0)
        prev_div = int(prev["domain_diversity"] or 0)
        prev_verdict = str(prev["verdict"] or "")

        if verdict != prev_verdict and confidence >= max(55, prev_conf + 7):
            return True
        if quality_score >= (prev_q + 5.0):
            return True
        if confidence >= (prev_conf + 7) and (trusted_count >= prev_trusted or domain_diversity >= prev_div):
            return True
        return False

    def _snapshot_reason(
        self,
        prev: sqlite3.Row | None,
        verdict: str,
        confidence: int,
        quality_score: float,
        trusted_count: int,
        domain_diversity: int,
        evidence_count: int,
    ) -> Dict[str, Any]:
        if prev is None:
            return {
                "decision": "insert_first",
                "why_updated": "first snapshot for claim",
                "deltas": {
                    "confidence_delta": confidence,
                    "quality_delta": round(quality_score, 3),
                    "trusted_delta": trusted_count,
                    "diversity_delta": domain_diversity,
                    "evidence_delta": evidence_count,
                },
            }
        prev_conf = int(prev["confidence"] or 0)
        prev_q = float(prev["quality_score"] or 0.0)
        prev_tr = int(prev["trusted_count"] or 0)
        prev_div = int(prev["domain_diversity"] or 0)
        prev_ev = int(prev["evidence_count"] or 0)
        prev_verdict = str(prev["verdict"] or "")
        reasons: List[str] = []
        if verdict != prev_verdict:
            reasons.append("verdict_changed")
        if confidence > prev_conf:
            reasons.append("higher_confidence")
        if quality_score > prev_q:
            reasons.append("higher_quality_score")
        if trusted_count > prev_tr:
            reasons.append("more_trusted_sources")
        if domain_diversity > prev_div:
            reasons.append("better_domain_diversity")
        if evidence_count > prev_ev:
            reasons.append("broader_evidence_set")
        if not reasons:
            reasons.append("kept_for_history")
        return {
            "decision": "promote_or_keep_history",
            "why_updated": ", ".join(reasons),
            "deltas": {
                "confidence_delta": confidence - prev_conf,
                "quality_delta": round(quality_score - prev_q, 3),
                "trusted_delta": trusted_count - prev_tr,
                "diversity_delta": domain_diversity - prev_div,
                "evidence_delta": evidence_count - prev_ev,
            },
            "previous_run_id": str(prev["run_id"] or ""),
        }

    def build_change_log(self, run_id: str, verdict: str, similar_claims: List[Dict[str, Any]], sources: List[Dict[str, Any]]) -> List[Dict[str, str]]:
        now = int(time.time())
        if not similar_claims:
            return []
        change_rows: List[Dict[str, str]] = []
        src_domains = {self._root_domain(str(s.get("url") or "")) for s in sources if s.get("url")}
        src_domains.discard("")
        for sim in similar_claims[:3]:
            related = str(sim.get("run_id") or "")
            if not related:
                continue
            what_changed = []
            why_changed = []
            if str(sim.get("verdict") or "") != verdict:
                what_changed.append("verdict_changed")
                why_changed.append("new run verdict differs from nearest historical claim")
            if float(sim.get("similarity") or 0.0) >= 0.70:
                what_changed.append("high_similarity_reused")
                why_changed.append("high similarity triggered retrieval bootstrap hints")
            prior_domains = {
                self._root_domain(str(s.get("url") or ""))
                for s in (sim.get("prior_evidence") or [])
                if s.get("url")
            }
            prior_domains.discard("")
            new_domains = sorted(list(src_domains - prior_domains))
            if new_domains:
                what_changed.append("new_source_domains")
                why_changed.append(f"new stronger domains observed: {', '.join(new_domains[:4])}")
            if not what_changed:
                continue
            row = {
                "related_run_id": related,
                "what_changed": "; ".join(what_changed),
                "why_changed": "; ".join(why_changed),
            }
            change_rows.append(row)
            with self._connect() as conn:
                conn.execute(
                    "INSERT INTO change_log(run_id, related_run_id, what_changed, why_changed, created_at) VALUES(?,?,?,?,?)",
                    (run_id, related, row["what_changed"], row["why_changed"], now),
                )
        return change_rows

    @staticmethod
    def _root_domain(url: str) -> str:
        host = re.sub(r"^https?://", "", url or "").split("/")[0].lower()
        host = host.split(":")[0]
        parts = [p for p in host.split(".") if p and p != "www"]
        if len(parts) >= 2:
            return ".".join(parts[-2:])
        return host

    def _update_trust_stats(self, conn: sqlite3.Connection, sources: List[Dict[str, Any]]) -> None:
        now = int(time.time())
        for s in sources:
            d = self._root_domain(str(s.get("url") or ""))
            if not d:
                continue
            c = int(s.get("credibility") or 0)
            row = conn.execute("SELECT seen_count,total_credibility FROM trust_stats WHERE root_domain=?", (d,)).fetchone()
            if not row:
                conn.execute(
                    "INSERT INTO trust_stats(root_domain,seen_count,total_credibility,last_seen) VALUES(?,?,?,?)",
                    (d, 1, c, now),
                )
            else:
                conn.execute(
                    "UPDATE trust_stats SET seen_count=?, total_credibility=?, last_seen=? WHERE root_domain=?",
                    (int(row["seen_count"] or 0) + 1, int(row["total_credibility"] or 0) + c, now, d),
                )
