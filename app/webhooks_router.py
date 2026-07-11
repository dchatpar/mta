"""Webhooks router — /api/v1/webhooks."""
from typing import List, Optional
from fastapi import APIRouter, HTTPException, Depends, Header, Request
from pydantic import BaseModel, HttpUrl

from auth import session_is_valid
from webhooks import (
    register_webhook, list_webhooks, delete_webhook, get_webhook,
    delivery_log, retry_due,
)

router = APIRouter(prefix="/api/v1/webhooks", tags=["webhooks"])


def admin_guard(request: Request, authorization: Optional[str] = Header(None)):
    if request.cookies.get("mta_session") and session_is_valid(request.cookies["mta_session"]):
        return True
    if authorization and authorization.startswith("Bearer "):
        tok = authorization[7:]
        if not tok.startswith("mta_") and session_is_valid(tok):
            return True
    raise HTTPException(403, "admin session required")


class WebhookCreate(BaseModel):
    url: HttpUrl
    events: List[str]
    description: str = ""


@router.post("", status_code=201)
async def api_register(body: WebhookCreate, _=Depends(admin_guard)):
    valid_events = {"message.accepted", "message.delivered", "message.deferred",
                    "message.bounced", "message.complaint", "*"}
    for e in body.events:
        if e not in valid_events:
            raise HTTPException(400, f"invalid event: {e}")
    return register_webhook(str(body.url), body.events, body.description)


@router.get("")
async def api_list(_=Depends(admin_guard)):
    return list_webhooks()


@router.delete("/{webhook_id}")
async def api_delete(webhook_id: int, _=Depends(admin_guard)):
    if not delete_webhook(webhook_id):
        raise HTTPException(404, "webhook not found")
    return {"ok": True}


@router.get("/deliveries")
async def api_deliveries(webhook_id: Optional[int] = None, limit: int = 100,
                         _=Depends(admin_guard)):
    return delivery_log(webhook_id, limit)


class FireRequest(BaseModel):
    event: str
    data: dict = {}


@router.post("/test-fire", status_code=200)
async def api_test_fire(body: FireRequest, _=Depends(admin_guard)):
    """Manually fire a webhook event (for QA/integration testing)."""
    from webhooks import fire_event
    delivery_ids = await fire_event(body.event, body.data)
    return {"fired": True, "delivery_ids": delivery_ids}


@router.post("/retry-due")
async def api_retry(_=Depends(admin_guard)):
    await retry_due()
    return {"ok": True}