"""Cloudflare router — /api/cf/*."""
from typing import Optional, List, Dict, Any
from fastapi import APIRouter, HTTPException, Depends, Header, Request
from pydantic import BaseModel

from auth import session_is_valid
from cloudflare import (
    Cloudflare, get_client, save_token, load_token, clear_token,
    generate_dkim_keypair, setup_mail_wizard, verify_records, dns_lookup,
)

router = APIRouter(prefix="/api/cf", tags=["cloudflare"])


def admin_guard(request: Request, authorization: Optional[str] = Header(None)):
    if request.cookies.get("mta_session") and session_is_valid(request.cookies["mta_session"]):
        return True
    if authorization and authorization.startswith("Bearer "):
        tok = authorization[7:]
        if not tok.startswith("mta_") and session_is_valid(tok):
            return True
    raise HTTPException(403, "admin session required")


class ConnectRequest(BaseModel):
    # Accept both ``token`` and ``api_token`` for client compatibility — the
    # Cloudflare dashboard UI historically uses ``token`` while the documented
    # canonical field is ``api_token``. Either is required.
    api_token: Optional[str] = None
    token: Optional[str] = None

    def resolved_token(self) -> Optional[str]:
        return self.api_token or self.token


class WizardRequest(BaseModel):
    zone_id: str
    hostname: str
    ip: str
    dkim_selector: str = "mta1"


class VerifyRequest(BaseModel):
    zone_name: str
    expected: Dict[str, str]


class RecordCreate(BaseModel):
    type: str
    name: str
    content: str
    ttl: int = 1
    priority: Optional[int] = None


class RecordUpdate(BaseModel):
    type: Optional[str] = None
    name: Optional[str] = None
    content: Optional[str] = None
    ttl: Optional[int] = None
    priority: Optional[int] = None


@router.post("/connect")
async def api_connect(body: ConnectRequest, _=Depends(admin_guard)):
    token = body.resolved_token()
    if not token:
        raise HTTPException(422, "api_token or token is required")
    cf = Cloudflare(token)
    try:
        info = await cf.verify_token()
    except Exception as e:
        raise HTTPException(400, f"invalid token: {e}")
    finally:
        await cf.close()
    save_token(token)
    return {"connected": True, "status": info.get("status"), "id": info.get("id")}


@router.post("/disconnect")
async def api_disconnect(_=Depends(admin_guard)):
    clear_token()
    return {"ok": True}


@router.get("/status")
async def api_status(_=Depends(admin_guard)):
    tok = load_token()
    return {"connected": tok is not None}


@router.get("/zones")
async def api_zones(_=Depends(admin_guard)):
    cf = get_client()
    if not cf:
        raise HTTPException(400, "not connected")
    try:
        zones = await cf.list_zones()
    finally:
        await cf.close()
    return [{"id": z["id"], "name": z["name"], "status": z["status"]}
            for z in zones]


@router.get("/zones/{zone_id}")
async def api_zone(zone_id: str, _=Depends(admin_guard)):
    cf = get_client()
    if not cf:
        raise HTTPException(400, "not connected")
    try:
        z = await cf.get_zone(zone_id)
    finally:
        await cf.close()
    return z


@router.get("/zones/{zone_id}/records")
async def api_records(zone_id: str, type: Optional[str] = None,
                      _=Depends(admin_guard)):
    cf = get_client()
    if not cf:
        raise HTTPException(400, "not connected")
    try:
        recs = await cf.list_records(zone_id, type)
    finally:
        await cf.close()
    return recs


@router.post("/zones/{zone_id}/records", status_code=201)
async def api_record_create(zone_id: str, body: RecordCreate,
                            _=Depends(admin_guard)):
    cf = get_client()
    if not cf:
        raise HTTPException(400, "not connected")
    try:
        rec = await cf.create_record(zone_id, body.type, body.name,
                                     body.content, body.ttl, body.priority)
    finally:
        await cf.close()
    return rec


@router.put("/zones/{zone_id}/records/{rec_id}")
async def api_record_update(zone_id: str, rec_id: str, body: RecordUpdate,
                            _=Depends(admin_guard)):
    cf = get_client()
    if not cf:
        raise HTTPException(400, "not connected")
    fields = {k: v for k, v in body.model_dump(exclude_none=True).items()}
    try:
        rec = await cf.update_record(zone_id, rec_id, **fields)
    finally:
        await cf.close()
    return rec


@router.delete("/zones/{zone_id}/records/{rec_id}")
async def api_record_delete(zone_id: str, rec_id: str,
                            _=Depends(admin_guard)):
    cf = get_client()
    if not cf:
        raise HTTPException(400, "not connected")
    try:
        rec = await cf.delete_record(zone_id, rec_id)
    finally:
        await cf.close()
    return rec


@router.post("/wizard/setup-mail")
async def api_wizard_setup(body: WizardRequest, _=Depends(admin_guard)):
    cf = get_client()
    if not cf:
        raise HTTPException(400, "not connected — call /api/cf/connect first")
    try:
        result = await setup_mail_wizard(
            body.zone_id, body.hostname, body.ip, body.dkim_selector, cf)
    except Exception as e:
        raise HTTPException(400, f"wizard failed: {e}")
    finally:
        await cf.close()
    # Persist DKIM private key
    from pathlib import Path
    dkim_dir = Path("/opt/kumomta/etc/policy/dkim")
    dkim_dir.mkdir(parents=True, exist_ok=True)
    if "dkim_private_key" in result:
        (dkim_dir / f"{body.dkim_selector}.{body.hostname}.pem").write_text(
            result["dkim_private_key"])
        (dkim_dir / f"{body.dkim_selector}.{body.hostname}.pem").chmod(0o600)
    return {"created": {k: v for k, v in result.items()
                         if k not in ("dkim_private_key", "dkim_public_pem")},
            "dkim_stored_at": str(dkim_dir / f"{body.dkim_selector}.{body.hostname}.pem")}


@router.post("/verify")
async def api_verify(body: VerifyRequest, _=Depends(admin_guard)):
    return await verify_records(body.zone_name, body.expected)


@router.get("/dns-lookup")
async def api_dns_lookup(name: str, rtype: str = "A",
                         _=Depends(admin_guard)):
    return {"name": name, "type": rtype, "records": await dns_lookup(name, rtype)}


@router.post("/generate-dkim")
async def api_generate_dkim(selector: str = "mta1",
                            domain: Optional[str] = None,
                            _=Depends(admin_guard)):
    return await generate_dkim_keypair(selector, domain)