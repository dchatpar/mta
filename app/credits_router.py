"""FastAPI router for /api/v1/credits/*."""
from typing import Optional, List
from fastapi import APIRouter, HTTPException, Depends, Header, Request
from pydantic import BaseModel, Field

from credits import (
    create_tenant, list_tenants, get_tenant, update_tenant, delete_tenant,
    topup, record_usage, get_usage, authenticate_api_key, check_quota,
    decrement_balance, stats_summary, generate_api_key,
)
from auth import session_is_valid

router = APIRouter(prefix="/api/v1/credits", tags=["credits"])


def _check_session_or_403(request: Request, authorization: Optional[str]):
    """Admin endpoints require session cookie OR bearer session token."""
    if request.cookies.get("mta_session") and session_is_valid(request.cookies["mta_session"]):
        return True
    if authorization and authorization.startswith("Bearer "):
        tok = authorization[7:]
        # Tenant API keys (mta_*) authenticate differently; here we only allow session tokens
        if not tok.startswith("mta_") and session_is_valid(tok):
            return True
    raise HTTPException(403, "admin session required")


async def admin_guard(request: Request, authorization: Optional[str] = Header(None)):
    _check_session_or_403(request, authorization)
    return True


async def tenant_from_key(authorization: Optional[str] = Header(None)):
    """Resolve tenant from Authorization: Bearer mta_<...>."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "missing or invalid Authorization header")
    key = authorization[7:]
    tenant = authenticate_api_key(key)
    if not tenant:
        raise HTTPException(401, "invalid API key")
    return tenant


class TenantCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=80)
    balance: int = Field(1000, ge=0)
    daily_limit: int = Field(0, ge=0)
    monthly_limit: int = Field(0, ge=0)
    rate_limit_per_minute: int = Field(60, ge=1, le=10000)


class TenantUpdate(BaseModel):
    name: Optional[str] = None
    balance: Optional[int] = None
    daily_limit: Optional[int] = None
    monthly_limit: Optional[int] = None
    rate_limit_per_minute: Optional[int] = None
    suspended: Optional[bool] = None


class TopupRequest(BaseModel):
    amount: int = Field(..., gt=0)
    note: Optional[str] = None


@router.post("/tenants", status_code=201)
async def api_create_tenant(body: TenantCreate, _=Depends(admin_guard)):
    try:
        t, plain_key = create_tenant(
            name=body.name, balance=body.balance,
            daily_limit=body.daily_limit,
            monthly_limit=body.monthly_limit,
            rate_limit_per_minute=body.rate_limit_per_minute,
        )
    except Exception as e:
        raise HTTPException(400, str(e))
    return t.to_dict(include_plain_key=plain_key)


@router.get("/tenants")
async def api_list_tenants(_=Depends(admin_guard)):
    return [t.to_dict() for t in list_tenants()]


@router.get("/tenants/{tenant_id}")
async def api_get_tenant(tenant_id: int, _=Depends(admin_guard)):
    t = get_tenant(tenant_id)
    if not t:
        raise HTTPException(404, "tenant not found")
    d = t.to_dict()
    d["usage_30d"] = [u.to_dict() for u in get_usage(tenant_id, 30)]
    return d


@router.put("/tenants/{tenant_id}")
async def api_update_tenant(tenant_id: int, body: TenantUpdate, _=Depends(admin_guard)):
    t = get_tenant(tenant_id)
    if not t:
        raise HTTPException(404, "tenant not found")
    fields = {k: v for k, v in body.model_dump(exclude_none=True).items()}
    return update_tenant(tenant_id, **fields).to_dict()


@router.delete("/tenants/{tenant_id}")
async def api_delete_tenant(tenant_id: int, _=Depends(admin_guard)):
    if not delete_tenant(tenant_id):
        raise HTTPException(404, "tenant not found")
    return {"ok": True}


@router.post("/tenants/{tenant_id}/topup")
async def api_topup(tenant_id: int, body: TopupRequest, _=Depends(admin_guard)):
    t = get_tenant(tenant_id)
    if not t:
        raise HTTPException(404, "tenant not found")
    t = topup(tenant_id, body.amount)
    record_usage(tenant_id, "topup", 0, -body.amount, "topup")
    return t.to_dict()


@router.get("/tenants/{tenant_id}/usage")
async def api_usage(tenant_id: int, days: int = 30, _=Depends(admin_guard)):
    if days < 1 or days > 365:
        raise HTTPException(400, "days must be 1..365")
    return [u.to_dict() for u in get_usage(tenant_id, days)]


@router.get("/stats")
async def api_stats(_=Depends(admin_guard)):
    return stats_summary()


@router.post("/check-quota")
async def api_check_quota(tenant: dict = Depends(tenant_from_key),
                          recipients: int = 1):
    return check_quota(tenant, recipients)


@router.post("/_create-api-key")
async def api_create_api_key(_=Depends(admin_guard)):
    """Helper for testing — generate a fresh key (does NOT persist)."""
    return {"api_key": generate_api_key()}