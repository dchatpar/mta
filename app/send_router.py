"""Public Sending API — /api/v1/send*, /api/v1/messages.

Validates with Pydantic, resolves tenant via API key, checks credits,
calls the mail engine /api/engine/inject, decrements balance, logs usage.
"""
import csv
import io
import json
import time
import uuid
from typing import List, Optional, Dict, Any
from fastapi import APIRouter, HTTPException, Depends, Header, Request
from pydantic import BaseModel, Field, EmailStr, field_validator
import httpx

from credits import (
    authenticate_api_key, check_quota, decrement_balance, record_usage,
    generate_api_key,
)
from kumod_client import KumodClient, KumodOffline

router = APIRouter(prefix="/api/v1", tags=["send"])
engine: Optional[KumodClient] = None  # injected at startup


def init_engine(client: KumodClient):
    global engine
    engine = client


async def tenant_from_key(authorization: Optional[str] = Header(None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "missing Authorization: Bearer <key>")
    key = authorization[7:]
    tenant = authenticate_api_key(key)
    if not tenant:
        raise HTTPException(401, "invalid API key")
    return tenant


class Attachment(BaseModel):
    filename: str = Field(..., min_length=1, max_length=255)
    content_type: str = "application/octet-stream"
    content_b64: str = Field(..., description="Base64-encoded attachment bytes")


class SendRequest(BaseModel):
    recipients: List[EmailStr] = Field(..., min_length=1, max_length=1000)
    sender: EmailStr = Field(..., description="RFC5322 From address")
    subject: str = Field(..., min_length=1, max_length=998)
    content_type: str = Field("text/html", pattern=r"^(text/plain|text/html)$")
    body: str = Field(..., min_length=1, max_length=1_000_000)
    tags: List[str] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    scheduled_at: Optional[float] = None
    attachments: List[Attachment] = Field(default_factory=list)

    @field_validator("recipients")
    @classmethod
    def _unique_recipients(cls, v):
        if len(v) > len(set(v)):
            raise ValueError("duplicate recipients not allowed")
        return v


class BatchItem(BaseModel):
    sender: EmailStr
    recipients: List[EmailStr] = Field(..., min_length=1)
    subject: str = Field(..., min_length=1)
    body: str = Field(..., min_length=1)
    content_type: str = "text/html"
    tags: List[str] = []


class BatchRequest(BaseModel):
    items: List[BatchItem] = Field(..., min_length=1, max_length=10000)


class TemplateSendRequest(BaseModel):
    template: str = Field(..., min_length=1)
    recipients: List[EmailStr] = Field(..., min_length=1, max_length=1000)
    variables: Dict[str, Any] = Field(default_factory=dict)


# Simple in-process message store for status checks
MESSAGES: Dict[str, Dict[str, Any]] = {}


def _cost_for(recipients: int, attachments: int = 0) -> int:
    base = recipients
    attach = max(1, attachments) if attachments else 0
    return base + attach


def _build_envelope(sender: str, recipients: List[str], subject: str,
                    body: str, content_type: str, tags: List[str],
                    metadata: Dict[str, Any], attachments: List[Dict]) -> Dict[str, Any]:
    boundary = "----=_NextPart_" + uuid.uuid4().hex
    parts = []
    if attachments:
        ct = f'multipart/mixed; boundary="{boundary}"'
        parts.append(f'Content-Type: {ct}\r\n')
        parts.append("\r\n--" + boundary + "\r\n")
        parts.append(f"Content-Type: {content_type}; charset=utf-8\r\n\r\n")
        parts.append(body + "\r\n")
        for att in attachments:
            import base64
            try:
                decoded = base64.b64decode(att["content_b64"])
            except Exception:
                decoded = att["content_b64"].encode()
            parts.append(f"\r\n--{boundary}\r\n")
            parts.append(
                f'Content-Type: {att.get("content_type", "application/octet-stream")}; '
                f'name="{att["filename"]}"\r\n'
            )
            parts.append(
                f'Content-Disposition: attachment; filename="{att["filename"]}"\r\n'
            )
            parts.append("Content-Transfer-Encoding: base64\r\n\r\n")
            parts.append(base64.b64encode(decoded).decode() + "\r\n")
        parts.append(f"\r\n--{boundary}--\r\n")
        content = "".join(parts)
    else:
        content = body

    content_bytes = content.encode("utf-8") if isinstance(content, str) else content
    return {
        "envelope_sender": sender,
        "recipients": recipients,
        "content": content_bytes,
        "subject": subject,
        "tags": tags,
        "metadata": metadata,
    }


@router.post("/send", status_code=202)
async def api_send(body: SendRequest, request: Request,
                   tenant=Depends(tenant_from_key)):
    quota = check_quota(tenant, recipients=len(body.recipients))
    if not quota["ok"]:
        raise HTTPException(quota["http"], quota)

    envelope = _build_envelope(
        sender=body.sender, recipients=body.recipients, subject=body.subject,
        body=body.body, content_type=body.content_type, tags=body.tags,
        metadata=body.metadata,
        attachments=[a.model_dump() for a in body.attachments],
    )
    message_id = uuid.uuid4().hex
    try:
        result = await engine.inject(envelope["envelope_sender"],
                                     envelope["recipients"],
                                     envelope["content"])
    except KumodOffline as e:
        record_usage(tenant.id, "/api/v1/send", len(body.recipients),
                     0, "engine_offline", message_id)
        raise HTTPException(503, f"engine unavailable: {e}")
    except Exception as e:
        record_usage(tenant.id, "/api/v1/send", len(body.recipients),
                     0, "error", message_id)
        raise HTTPException(502, f"engine error: {e}")

    cost = _cost_for(len(body.recipients), len(body.attachments))
    new_balance = decrement_balance(tenant.id, cost)
    record_usage(tenant.id, "/api/v1/send", len(body.recipients), cost,
                 "ok", message_id)

    MESSAGES[message_id] = {
        "message_id": message_id,
        "tenant_id": tenant.id,
        "sender": body.sender,
        "recipients": body.recipients,
        "subject": body.subject,
        "tags": body.tags,
        "metadata": body.metadata,
        "accepted_count": len(body.recipients),
        "cost": cost,
        "balance_after": new_balance,
        "ts": time.time(),
        "engine_response": result,
        "status": "accepted",
    }
    # Fire webhook asynchronously
    try:
        from webhooks import fire_event
        await fire_event("message.accepted", MESSAGES[message_id])
    except Exception:
        pass

    return {
        "message_id": message_id,
        "accepted_count": len(body.recipients),
        "cost": cost,
        "balance_after": new_balance,
        "engine_response": result,
    }


@router.post("/send/batch", status_code=202)
async def api_send_batch(body: BatchRequest, request: Request,
                         tenant=Depends(tenant_from_key)):
    total_recipients = sum(len(i.recipients) for i in body.items)
    quota = check_quota(tenant, recipients=total_recipients)
    if not quota["ok"]:
        raise HTTPException(quota["http"], quota)

    accepted = 0
    cost = 0
    errors = []
    for idx, item in enumerate(body.items):
        msg_id = uuid.uuid4().hex
        envelope = _build_envelope(
            sender=item.sender, recipients=item.recipients,
            subject=item.subject, body=item.body,
            content_type=item.content_type, tags=item.tags,
            metadata={"batch_index": idx}, attachments=[],
        )
        try:
            result = await engine.inject(envelope["envelope_sender"],
                                         envelope["recipients"],
                                         envelope["content"])
            accepted += len(item.recipients)
            cost += len(item.recipients)
            MESSAGES[msg_id] = {
                "message_id": msg_id, "tenant_id": tenant.id,
                "sender": item.sender, "recipients": item.recipients,
                "subject": item.subject, "accepted_count": len(item.recipients),
                "ts": time.time(), "engine_response": result, "status": "accepted",
            }
        except Exception as e:
            errors.append({"index": idx, "error": str(e)})
            MESSAGES[msg_id] = {
                "message_id": msg_id, "tenant_id": tenant.id,
                "status": "error", "error": str(e), "ts": time.time(),
            }
    if cost:
        decrement_balance(tenant.id, cost)
    record_usage(tenant.id, "/api/v1/send/batch", accepted, cost,
                 "partial" if errors else "ok")
    return {
        "accepted_count": accepted,
        "error_count": len(errors),
        "errors": errors[:10],
        "cost": cost,
        "balance_after": (decrement_balance(0, 0) if False else 0) or 0,
    }


@router.post("/send/template", status_code=202)
async def api_send_template(body: TemplateSendRequest,
                            tenant=Depends(tenant_from_key)):
    """Render a named template + send. Templates are simple
    `{var}` substitutions over (subject + body)."""
    templates = _load_templates()
    tpl = templates.get(body.template)
    if not tpl:
        raise HTTPException(404, f"template '{body.template}' not found")
    try:
        subject = tpl["subject"].format(**body.variables)
        text_body = tpl["body"].format(**body.variables)
    except KeyError as e:
        raise HTTPException(400, f"missing template variable: {e}")

    quota = check_quota(tenant, recipients=len(body.recipients))
    if not quota["ok"]:
        raise HTTPException(quota["http"], quota)

    envelope = _build_envelope(
        sender=tpl.get("sender", "noreply@example.com"),
        recipients=body.recipients, subject=subject, body=text_body,
        content_type=tpl.get("content_type", "text/html"),
        tags=[body.template], metadata={"template": body.template},
        attachments=[],
    )
    msg_id = uuid.uuid4().hex
    try:
        result = await engine.inject(envelope["envelope_sender"],
                                     envelope["recipients"],
                                     envelope["content"])
    except KumodOffline as e:
        raise HTTPException(503, f"engine unavailable: {e}")

    cost = len(body.recipients)
    decrement_balance(tenant.id, cost)
    record_usage(tenant.id, "/api/v1/send/template", len(body.recipients),
                 cost, "ok", msg_id)
    MESSAGES[msg_id] = {
        "message_id": msg_id, "tenant_id": tenant.id,
        "subject": subject, "recipients": body.recipients,
        "accepted_count": len(body.recipients), "ts": time.time(),
        "engine_response": result, "status": "accepted",
    }
    return {"message_id": msg_id, "accepted_count": len(body.recipients),
            "cost": cost}


@router.get("/messages/{message_id}")
async def api_message_status(message_id: str):
    if message_id not in MESSAGES:
        raise HTTPException(404, "message not found")
    return MESSAGES[message_id]


@router.get("/messages")
async def api_messages_list(tenant_id: Optional[int] = None, limit: int = 50,
                            request: Request = None):
    items = list(MESSAGES.values())
    if tenant_id is not None:
        items = [m for m in items if m.get("tenant_id") == tenant_id]
    items.sort(key=lambda m: m.get("ts", 0), reverse=True)
    return items[:limit]


def _load_templates() -> Dict[str, Dict[str, Any]]:
    """Built-in templates. Real impl would store in DB."""
    return {
        "welcome": {
            "subject": "Welcome to {product}, {name}!",
            "body": "<h1>Hi {name},</h1><p>Welcome to <b>{product}</b>.</p>",
            "sender": "hello@example.com",
            "content_type": "text/html",
        },
        "receipt": {
            "subject": "Receipt #{order_id}",
            "body": "<p>Thanks for your order. Total: ${amount}.</p>",
            "sender": "billing@example.com",
            "content_type": "text/html",
        },
        "password_reset": {
            "subject": "Reset your password",
            "body": "<p>Click <a href='{reset_url}'>here</a> to reset your password.</p>",
            "sender": "noreply@example.com",
            "content_type": "text/html",
        },
    }