"""M3 AI router — /api/ai/*."""
from typing import Optional, Dict, Any, List
from fastapi import APIRouter, HTTPException, Depends, Header, Request
from pydantic import BaseModel
from fastapi.responses import StreamingResponse
import json

from auth import session_is_valid
from m3 import M3, GOLDEN_QA
from kumod_client import KumodClient, KumodOffline

router = APIRouter(prefix="/api/ai", tags=["m3-ai"])

_engine: Optional[KumodClient] = None
_m3: Optional[M3] = None


def init(engine: KumodClient, m3: M3):
    global _engine, _m3
    _engine = engine
    _m3 = m3


def admin_guard(request: Request, authorization: Optional[str] = Header(None)):
    if request.cookies.get("mta_session") and session_is_valid(request.cookies["mta_session"]):
        return True
    if authorization and authorization.startswith("Bearer "):
        tok = authorization[7:]
        if not tok.startswith("mta_") and session_is_valid(tok):
            return True
    raise HTTPException(403, "admin session required")


class ChatRequest(BaseModel):
    query: str
    context: Optional[Dict[str, Any]] = None
    stream: bool = False


class AnalyzeQueueRequest(BaseModel):
    queue_name: str


class ExplainConfigRequest(BaseModel):
    config_name: str


@router.post("/insights")
async def api_insights(body: ChatRequest, _=Depends(admin_guard)):
    # Auto-collect context if none provided
    ctx = body.context
    if ctx is None and _engine is not None:
        try:
            ctx = {
                "machine": await _engine.machine_info(),
                "memory": await _engine.memory(),
                "ready_q_states": await _engine.ready_q_states(),
                "bounces": await _engine.bounces(),
                "suspends_ready": await _engine.suspends_ready(),
            }
        except KumodOffline:
            ctx = {"engine_offline": True}
    if body.stream:
        async def gen():
            async for chunk in _m3.stream_chat("insights", body.query, ctx):
                yield chunk
        return StreamingResponse(gen(), media_type="text/plain")
    return await _m3.chat("insights", body.query, ctx)


@router.post("/analyze-queue")
async def api_analyze_queue(body: AnalyzeQueueRequest,
                            _=Depends(admin_guard)):
    if _engine is None:
        raise HTTPException(503, "engine not initialized")
    try:
        sched = await _engine.inspect_sched_q(body.queue_name)
    except Exception:
        sched = None
    try:
        ready_states = await _engine.ready_q_states()
        queue_state = next(
            (q for q in ready_states if q.get("queue_name") == body.queue_name
             or q.get("name") == body.queue_name), ready_states[:3])
    except Exception:
        queue_state = []
    ctx = {"queue": body.queue_name, "scheduled": sched, "ready_state": queue_state}
    return await _m3.chat("analyze-queue",
                          f"Analyze queue '{body.queue_name}'", ctx)


@router.post("/explain-config")
async def api_explain_config(body: ExplainConfigRequest,
                             _=Depends(admin_guard)):
    from config_manager import read_config
    f = await read_config(body.config_name)
    if f is None:
        raise HTTPException(404, f"config '{body.config_name}' not found")
    return await _m3.chat("explain-config",
                          f"Explain the {body.config_name} config",
                          {"content": f.content, "name": f.name})


@router.post("/suggest-actions")
async def api_suggest_actions(_=Depends(admin_guard)):
    if _engine is None:
        raise HTTPException(503, "engine not initialized")
    try:
        ctx = {
            "metrics": await _engine.memory(),
            "ready_q_states": await _engine.ready_q_states(),
            "bounces": await _engine.bounces(),
            "suspends_ready": await _engine.suspends_ready(),
        }
    except KumodOffline:
        ctx = {"engine_offline": True}
    return await _m3.chat("suggest-actions",
                          "What should I do next given the current engine state?",
                          ctx)


@router.get("/golden-qa")
async def api_golden_qa(_=Depends(admin_guard)):
    """Run all golden Q&A pairs. Used by tests."""
    results = []
    for qa in GOLDEN_QA:
        r = await _m3.chat(qa["task"], qa["query"], qa.get("context"))
        # _m3.chat may return a Dict (real client) or a plain str (test stub).
        text = r.get("text", "") if isinstance(r, dict) else str(r)
        ok = all(s.lower() in text.lower() for s in qa["must_contain"])
        results.append({"task": qa["task"], "query": qa["query"],
                        "ok": ok, "response": text[:200]})
    return {"total": len(results), "passed": sum(1 for r in results if r["ok"]),
            "results": results}