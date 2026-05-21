"""FastAPI entrypoint for the FactLens Crew hackathon demo."""

from __future__ import annotations

import os
import sys
import tempfile
import threading
import time
import uuid
import sqlite3
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, Form, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

# Allow `python api/main.py` by ensuring project root is importable.
if __package__ in {None, ""}:
    project_root = Path(__file__).resolve().parent.parent
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))

from factlens_crew import run_factlens_crew
from factlens_crew.events import event_store
from factlens_crew.orchestrator import RunCancelledError
from factlens_crew.schemas import WarRoomEvent


def _load_env_file() -> None:
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if key and key not in os.environ:
            os.environ[key] = value


_load_env_file()

app = FastAPI(title="FactLens Crew", version="0.1.0")

_run_lock = threading.Lock()
_run_results: dict[str, dict] = {}
_run_status: dict[str, str] = {}
_run_started_at: dict[str, float] = {}
_run_cancel_requested: dict[str, bool] = {}


def _memory_db_path() -> Path:
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
        return Path("/tmp") / "factlens_memory.sqlite3"

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health() -> dict:
    return {"status": "healthy"}


@app.post("/api/verify")
async def verify(
    text: str = Form(""),
    input_type: str = Form("text"),
    pdf_pages: str = Form(""),
    cache_mode: str = Form("auto"),
    force_live_recheck: str = Form("0"),
    file: Optional[UploadFile] = File(None),
) -> dict:
    file_path = ""
    if file and file.filename:
        suffix = Path(file.filename).suffix
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp.write(await file.read())
            file_path = tmp.name

    try:
        force = str(force_live_recheck).strip().lower() in {"1", "true", "yes", "on"}
        return run_factlens_crew(
            text=text,
            file_path=file_path,
            input_type=input_type,
            pdf_pages=pdf_pages,
            cache_mode=(cache_mode or "auto"),
            force_live_recheck=force,
        )
    finally:
        if file_path:
            Path(file_path).unlink(missing_ok=True)


@app.post("/api/verify/start")
async def verify_start(
    text: str = Form(""),
    input_type: str = Form("text"),
    pdf_pages: str = Form(""),
    cache_mode: str = Form("auto"),
    force_live_recheck: str = Form("0"),
    file: Optional[UploadFile] = File(None),
) -> dict:
    file_path = ""
    if file and file.filename:
        suffix = Path(file.filename).suffix
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp.write(await file.read())
            file_path = tmp.name

    run_id = str(uuid.uuid4())
    with _run_lock:
        _run_status[run_id] = "running"
        _run_started_at[run_id] = time.time()
        _run_cancel_requested[run_id] = False

    def _worker() -> None:
        try:
            force = str(force_live_recheck).strip().lower() in {"1", "true", "yes", "on"}
            result = run_factlens_crew(
                text=text,
                file_path=file_path,
                input_type=input_type,
                pdf_pages=pdf_pages,
                run_id=run_id,
                cache_mode=(cache_mode or "auto"),
                force_live_recheck=force,
                cancel_check=lambda: bool(_run_cancel_requested.get(run_id, False)),
            )
            with _run_lock:
                _run_results[run_id] = result
                _run_status[run_id] = "completed"
        except RunCancelledError as exc:
            with _run_lock:
                _run_results[run_id] = {"run_id": run_id, "error": str(exc), "cancelled": True}
                _run_status[run_id] = "cancelled"
        except Exception as exc:  # pragma: no cover
            with _run_lock:
                _run_results[run_id] = {"run_id": run_id, "error": str(exc)}
                _run_status[run_id] = "failed"
        finally:
            if file_path:
                Path(file_path).unlink(missing_ok=True)

    threading.Thread(target=_worker, daemon=True).start()
    return {"run_id": run_id, "status": "running", "started_at": int(time.time())}


@app.post("/api/runs/{run_id}/cancel")
async def run_cancel(run_id: str) -> dict:
    with _run_lock:
        if run_id not in _run_status:
            return {"run_id": run_id, "status": "unknown"}
        if _run_status.get(run_id) != "running":
            return {"run_id": run_id, "status": _run_status.get(run_id)}
        _run_cancel_requested[run_id] = True
    event_store.add(
        WarRoomEvent(
            run_id=run_id,
            agent="System",
            status="cancelled",
            message="Cancel requested by user",
            data={"run_id": run_id},
        )
    )
    return {"run_id": run_id, "status": "cancelling"}


@app.get("/api/runs/{run_id}/status")
async def run_status(run_id: str) -> dict:
    with _run_lock:
        status = _run_status.get(run_id, "unknown")
        result = _run_results.get(run_id)
        started_at = _run_started_at.get(run_id)
    if status == "running" and started_at is not None:
        max_s = int(os.getenv("FACTLENS_RUN_TIMEOUT_SECONDS", "120"))
        if (time.time() - started_at) > max(30, max_s):
            with _run_lock:
                _run_status[run_id] = "failed"
                _run_results[run_id] = {
                    "run_id": run_id,
                    "error": "Run timed out during evidence retrieval or model inference.",
                }
            status = "failed"
            result = _run_results[run_id]
    return {"run_id": run_id, "status": status, "result": result}


@app.get("/api/runs/{run_id}/events")
async def run_events(run_id: str) -> dict:
    return {"run_id": run_id, "events": event_store.list(run_id)}


@app.get("/api/runs/live")
async def runs_live() -> dict:
    now = time.time()
    rows = []
    with _run_lock:
        for rid, status in _run_status.items():
            if status not in {"running", "unknown"}:
                continue
            started_at = _run_started_at.get(rid)
            rows.append(
                {
                    "run_id": rid,
                    "status": status,
                    "started_at": int(started_at) if started_at else None,
                    "elapsed_sec": round(now - started_at, 2) if started_at else None,
                }
            )
    rows.sort(key=lambda x: x.get("started_at") or 0, reverse=True)
    return {"runs": rows, "count": len(rows)}


@app.get("/api/storage/runs")
async def storage_runs(
    verdict: str = "",
    q: str = "",
    recency: str = "all",
    since_ts: int = 0,
    include_partial: int = 0,
    limit: int = 200,
) -> dict:
    db = _memory_db_path()
    if not db.exists():
        return {"runs": [], "count": 0, "db_path": str(db), "warning": "memory database not found"}
    limit = max(1, min(int(limit or 200), 1000))
    where = []
    params: list[object] = []
    if int(include_partial or 0) != 1:
        where.append("result_json IS NOT NULL")
    if verdict.strip():
        where.append("LOWER(COALESCE(verdict,'')) = ?")
        params.append(verdict.strip().lower())
    if q.strip():
        where.append("(LOWER(COALESCE(claim_raw,'')) LIKE ? OR LOWER(COALESCE(claim_normalized,'')) LIKE ?)")
        like = f"%{q.strip().lower()}%"
        params.extend([like, like])
    now = int(time.time())
    rec = (recency or "all").strip().lower()
    if since_ts and int(since_ts) > 0:
        where.append("COALESCE(ended_at, started_at) >= ?")
        params.append(int(since_ts))
    elif rec in {"1h", "24h", "7d", "30d", "90d"}:
        delta = {"1h": 3600, "24h": 86400, "7d": 604800, "30d": 2592000, "90d": 7776000}[rec]
        where.append("COALESCE(ended_at, started_at) >= ?")
        params.append(now - delta)
    sql = """
        SELECT run_id, session_id, claim_raw, claim_normalized, verdict, confidence, input_type, started_at, ended_at, policy_version
        FROM runs
    """
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY COALESCE(ended_at, started_at) DESC LIMIT ?"
    params.append(limit)
    with sqlite3.connect(db, timeout=10) as conn:
        conn.row_factory = sqlite3.Row
        rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
    return {"runs": rows, "count": len(rows), "db_path": str(db)}


@app.get("/api/storage/run/{run_id}")
async def storage_run_detail(run_id: str) -> dict:
    db = _memory_db_path()
    if not db.exists():
        return {"run": None, "evidence": [], "events": [], "warning": "memory database not found"}
    with sqlite3.connect(db, timeout=10) as conn:
        conn.row_factory = sqlite3.Row
        run = conn.execute(
            """
            SELECT run_id, session_id, claim_raw, claim_normalized, verdict, confidence, input_type, started_at, ended_at,
                   policy_version, entities_json, numbers_json, evidence_summary, stage_metrics_json, result_json
            FROM runs WHERE run_id = ?
            """,
            (run_id,),
        ).fetchone()
        evidence = conn.execute(
            """
            SELECT title, url, source_type, channel, domain, credibility, relevance, snippet
            FROM evidence WHERE run_id = ? ORDER BY id ASC
            """,
            (run_id,),
        ).fetchall()
        events = conn.execute(
            """
            SELECT id, agent, status, message, data_json
            FROM run_events WHERE run_id = ? ORDER BY id ASC
            """,
            (run_id,),
        ).fetchall()
    return {
        "run": dict(run) if run else None,
        "evidence": [dict(r) for r in evidence],
        "events": [dict(r) for r in events],
        "db_path": str(db),
    }


static_dir = Path(__file__).resolve().parent.parent / "static"
if static_dir.exists():
    app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="static")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("api.main:app", host="0.0.0.0", port=8000, reload=True)
