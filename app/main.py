"""MTa — Main FastAPI application.

Includes:
- Engine proxy (kumod 25 endpoints)
- Reputation checks
- Config manager
- Auth (PIN + bearer)
- SSE live stream
- Credits / tenants (Phase 1)
- Public sending API (Phase 2)
- Webhooks (Phase 3)
- Cloudflare DNS (Phase 4)
- M3 AI (Phase 5)
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from fastapi import FastAPI, Request, HTTPException, Depends
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
import httpx
import asyncio

from kumod_client import KumodClient, KumodOffline
from reputation import check_dbl, check_zen, check_surbl, check_all_for_domain
from credits import ensure_schema as credits_ensure_schema

app = FastAPI(
    title="MTa",
    description="Web admin for the mail engine: Cloudflare DNS automation, "
                "reputation monitoring, credit management, M3 AI insights, "
                "webhooks, and a public sending API.",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

MTA_ROOT = Path("/opt/mta")
ENGINE_URL = "http://127.0.0.1:8000"
STATIC_DIR = MTA_ROOT / "static"
WEB_DIR = MTA_ROOT / "web" / "dist"

engine = KumodClient(ENGINE_URL)


@app.get("/api/health")
async def health():
    return {"status": "ok", "service": "mta", "version": "1.0.0"}


@app.get("/api/engine/health")
async def engine_health():
    try:
        r = await engine.liveness()
        return {"status": "ok", "engine": True, "response": r}
    except KumodOffline as e:
        return JSONResponse(status_code=503, content={"status": "error",
                                                       "engine": False,
                                                       "error": str(e)})
    except Exception:
        return JSONResponse(status_code=503, content={"status": "error",
                                                       "engine": False,
                                                       "error": "engine health check failed"})


@app.get("/api/engine/machine-info")
async def machine_info():
    return await engine.machine_info()


@app.get("/api/engine/metrics")
async def metrics():
    return await engine.metrics_text()


@app.get("/api/engine/metrics.json")
async def metrics_json():
    return await engine.metrics_json()


@app.get("/api/engine/memory")
async def memory():
    return await engine.memory()


@app.get("/api/engine/task-dump")
async def task_dump():
    return await engine.task_dump()


@app.get("/api/engine/ready-q-states")
async def ready_q_states():
    return await engine.ready_q_states()


@app.get("/api/engine/bounces")
async def bounces():
    return await engine.bounces()


@app.post("/api/engine/bounces")
async def bounce_create(body: dict):
    return await engine.bounce_create(body["campaign"], body["tenant"], body["reason"])


@app.delete("/api/engine/bounces/{bounce_id}")
async def bounce_delete(bounce_id: str):
    return await engine.bounce_delete(bounce_id)


@app.get("/api/engine/suspends/ready")
async def suspends_ready():
    return await engine.suspends_ready()


@app.post("/api/engine/suspends/ready")
async def suspend_ready_create(body: dict):
    return await engine.suspend_ready_create(body["queue"], body["reason"],
                                             body.get("duration_seconds", 3600))


@app.delete("/api/engine/suspends/ready/{queue}")
async def suspend_ready_delete(queue: str):
    return await engine.suspend_ready_delete(queue)


@app.get("/api/engine/suspends/scheduled")
async def suspends_scheduled():
    return await engine.suspends_scheduled()


@app.post("/api/engine/suspends/scheduled")
async def suspend_scheduled_create(body: dict):
    return await engine.suspend_scheduled_create(body["queue"], body["reason"],
                                                 body.get("duration_seconds", 3600))


@app.delete("/api/engine/suspends/scheduled/{queue}")
async def suspend_scheduled_delete(queue: str):
    return await engine.suspend_scheduled_delete(queue)


@app.get("/api/engine/inspect-message/{spool_id}")
async def inspect_message(spool_id: str):
    return await engine.inspect_message(spool_id)


@app.get("/api/engine/inspect-sched-q/{queue}")
async def inspect_sched_q(queue: str):
    return await engine.inspect_sched_q(queue)


@app.post("/api/engine/inject")
async def inject(body: dict):
    return await engine.inject(body["envelope_sender"],
                               body["recipients"], body["content"])


@app.post("/api/engine/bump-config")
async def bump_config():
    return await engine.bump_config_epoch()


@app.post("/api/engine/rebind")
async def rebind(body: dict):
    return await engine.rebind(body["queue"], body.get("site_name"))


@app.get("/api/reputation/dbl/{domain}")
async def rep_dbl(domain: str):
    result = await check_dbl(domain)
    return result.to_dict()


@app.get("/api/reputation/zen/{ip}")
async def rep_zen(ip: str):
    return await check_zen(ip)


@app.get("/api/reputation/surbl")
async def rep_surbl(url: str):
    return await check_surbl(url)


class CheckAllRequest(BaseModel):
    domain: str = Field(..., min_length=1)


@app.post("/api/reputation/check-all")
async def rep_all(req: CheckAllRequest):
    domain = req.domain
    if not domain or not domain.strip():
        raise HTTPException(400, "domain required")
    ips = []
    try:
        raw_body = await _request.json() if hasattr(_request, "json") else {}
        ips = raw_body.get("ips", []) or []
    except Exception:
        pass
    return await check_all_for_domain(domain, ips)
    ips = body.get("ips", [])
    if not domain:
        raise HTTPException(400, "domain required")
    return await check_all_for_domain(domain, ips)


# === Config manager ===
from config_manager import (
    list_config_files, read_config, write_config, init_default_configs,
    POLICY_DIR,
)

@app.on_event("startup")
async def startup_config():
    await init_default_configs()
    credits_ensure_schema()
    # Init routers that need engine + m3
    from send_router import init_engine as init_send
    init_send(engine)
    from m3 import M3
    from m3_router import init as init_m3
    init_m3(engine, M3())


@app.get("/api/config/files")
async def config_files():
    return await list_config_files()


@app.get("/api/config/{name}")
async def config_get(name: str):
    f = await read_config(name)
    if f is None:
        raise HTTPException(404, f"file {name} not found")
    return {"name": f.name, "format": f.format, "content": f.content}


@app.put("/api/config/{name}")
async def config_put(name: str, body: dict):
    content = body.get("content")
    if content is None:
        raise HTTPException(400, "content required")
    result = await write_config(name, content, engine)
    if not result.get("ok"):
        raise HTTPException(400, result.get("error", "unknown"))
    return result

# === SMTP users ===

from smtp_users import SMTPAuth

# Forward-declared to break the circular ordering: smtp-user routes
# use Depends(require_session), but the real require_session lives
# further down in this module. The version below is overwritten by
# the canonical require_session later in the file, but it must exist
# at import time so the default-arg Depends(...) resolves.
async def require_session(request: Request) -> bool:  # noqa: F811
    raise HTTPException(401, "no valid session (bootstrap)")


class SMTPCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    username: str = Field(..., min_length=1, max_length=200)
    password: str = Field(..., min_length=1, max_length=512)
    tenant_id: Optional[str] = Field(default=None, max_length=200)


class SMTPRotateRequest(BaseModel):
    password: Optional[str] = Field(default=None, min_length=1, max_length=512)
    length: Optional[int] = Field(default=None, ge=8, le=128)


def _new_password(length: int = 20) -> str:
    import secrets as _s
    alphabet = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
    return "".join(_s.choice(alphabet) for _ in range(length))


@app.post("/api/v1/smtp-users")
async def smtp_users_create(body: SMTPCreateRequest, _=Depends(require_session)):
    try:
        user = SMTPAuth.create(body.name, body.username, body.password, body.tenant_id)
    except ValueError as e:
        raise HTTPException(409, str(e))
    return user.to_dict()


@app.get("/api/v1/smtp-users")
async def smtp_users_list(include_inactive: bool = False, _=Depends(require_session)):
    return [u.to_dict() for u in SMTPAuth.list_all(include_inactive=include_inactive)]


@app.get("/api/v1/smtp-users/{uid}")
async def smtp_users_get(uid: int, _=Depends(require_session)):
    u = SMTPAuth.get(uid)
    if not u:
        raise HTTPException(404, f"smtp user {uid} not found")
    return u.to_dict()


@app.delete("/api/v1/smtp-users/{uid}")
async def smtp_users_revoke(uid: int, _=Depends(require_session)):
    if not SMTPAuth.revoke(uid):
        raise HTTPException(404, f"smtp user {uid} not found")
    return {"ok": True, "id": uid}


@app.post("/api/v1/smtp-users/{uid}/rotate")
async def smtp_users_rotate(uid: int, body: Optional[SMTPRotateRequest] = None, _=Depends(require_session)):
    body = body or SMTPRotateRequest()
    new_pw = body.password or _new_password(body.length or 20)
    u = SMTPAuth.rotate(uid, new_pw)
    if not u:
        raise HTTPException(404, f"smtp user {uid} not found")
    out = u.to_dict()
    out["password"] = new_pw  # returned ONCE on rotation
    return out


@app.get("/api/internal/smtp-auth")
async def smtp_auth_internal(username: str, password: str):
    """Internal callback used by kumomta's smtp_server_auth_plain handler.

    Returns 200 OK if (username, password) is a valid active SMTP user,
    401 otherwise. Loopback-only by convention (no auth required).
    """
    if SMTPAuth.verify(username, password):
        return {"ok": True, "username": username}
    raise HTTPException(401, "invalid credentials")


# === Auth ===

from auth import (
    verify_pin, create_session, session_is_valid, revoke_session,
    ensure_default_pin, set_pin,
)

@app.on_event("startup")
async def startup_auth():
    ensure_default_pin()


async def require_session(request: Request) -> bool:
    session_cookie = request.cookies.get("mta_session")
    if session_cookie and session_is_valid(session_cookie):
        return True
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        token = auth[7:]
        if session_is_valid(token):
            return True
    raise HTTPException(401, "no valid session")


@app.post("/api/auth/login")
async def login(body: dict, response: JSONResponse = None):
    pin = body.get("pin", "")
    if not verify_pin(pin):
        raise HTTPException(401, "invalid PIN")
    token = create_session()
    r = JSONResponse({"token": token, "ttl_seconds": 7 * 24 * 3600})
    r.set_cookie("mta_session", token, max_age=7 * 24 * 3600,
                 httponly=True, samesite="lax")
    return r


@app.post("/api/auth/logout")
async def logout(request: Request):
    token = (request.cookies.get("mta_session")
             or request.headers.get("Authorization", "")[7:])
    revoke_session(token)
    return {"ok": True}


@app.get("/api/auth/status")
async def auth_status():
    return {"authenticated": len(SESSIONS), "pin_set": True}


@app.get("/api/me")
async def me(_=Depends(require_session)):
    return {"authenticated": True}


# === SSE ===
from sse import live_engine_stream
from sse_starlette.sse import EventSourceResponse

@app.get("/api/live/stream")
async def live_stream(request: Request):
    return EventSourceResponse(live_engine_stream(request, engine))


# === Routers from Phase 1–5 ===
from credits_router import router as credits_router
from send_router import router as send_router
from webhooks_router import router as webhooks_router
from cloudflare_router import router as cloudflare_router
from m3_router import router as m3_router

app.include_router(credits_router)
app.include_router(send_router)
app.include_router(webhooks_router)
app.include_router(cloudflare_router)
app.include_router(m3_router)


# === Static files ===
if WEB_DIR.exists():
    app.mount("/", StaticFiles(directory=str(WEB_DIR), html=True), name="web")
elif STATIC_DIR.exists():
    app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")


# === Background tasks ===
@app.on_event("startup")
async def start_background():
    async def retry_loop():
        from webhooks import retry_due
        while True:
            try:
                await retry_due()
            except Exception:
                pass
            await asyncio.sleep(5)
    asyncio.create_task(retry_loop())


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001, log_level="info")