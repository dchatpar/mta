"""Cloudflare API client + DNS automation."""
import base64
import hashlib
import secrets
from pathlib import Path
from typing import Optional, List, Dict, Any
import httpx

CF_API_BASE = "https://api.cloudflare.com/client/v4"
TOKEN_PATH = Path("/opt/mta/data/cf-token.enc")
FERNET_KEY_PATH = Path("/opt/mta/data/.fernet-key")

# Use a simple XOR-based encryption with a key file (Fernet-equivalent for at-rest).
# Not the strongest crypto but the API token is the secret; CF tokens are
# scoped + revocable.

def _load_key() -> bytes:
    FERNET_KEY_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not FERNET_KEY_PATH.exists():
        FERNET_KEY_PATH.write_bytes(secrets.token_bytes(32))
        FERNET_KEY_PATH.chmod(0o600)
    return FERNET_KEY_PATH.read_bytes()


def encrypt_token(plain: str) -> bytes:
    key = _load_key()
    pt = plain.encode()
    nonce = secrets.token_bytes(16)
    cipher = bytearray()
    for i, b in enumerate(pt):
        cipher.append(b ^ key[(i + nonce[i % 16]) % len(key)])
    return base64.b64encode(nonce + bytes(cipher))


def decrypt_token(blob: bytes) -> str:
    key = _load_key()
    raw = base64.b64decode(blob)
    nonce, cipher = raw[:16], raw[16:]
    plain = bytearray()
    for i, b in enumerate(cipher):
        plain.append(b ^ key[(i + nonce[i % 16]) % len(key)])
    return bytes(plain).decode()


def save_token(token: str) -> None:
    TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_PATH.write_bytes(encrypt_token(token))
    TOKEN_PATH.chmod(0o600)


def load_token() -> Optional[str]:
    if not TOKEN_PATH.exists():
        return None
    try:
        return decrypt_token(TOKEN_PATH.read_bytes())
    except Exception:
        return None


def clear_token() -> None:
    if TOKEN_PATH.exists():
        TOKEN_PATH.unlink()


class Cloudflare:
    def __init__(self, token: str):
        self.token = token
        self._client = httpx.AsyncClient(
            base_url=CF_API_BASE,
            headers={"Authorization": f"Bearer {token}",
                     "Content-Type": "application/json"},
            timeout=20,
        )

    async def close(self):
        await self._client.aclose()

    async def _ok(self, resp):
        data = resp.json()
        if not data.get("success", False):
            errs = data.get("errors", [])
            raise Exception(f"CF API error: {errs}")
        return data.get("result", data)

    async def verify_token(self) -> Dict[str, Any]:
        r = await self._client.get("/user/tokens/verify")
        return await self._ok(r)

    async def list_zones(self) -> List[Dict[str, Any]]:
        r = await self._client.get("/zones?per_page=50")
        return await self._ok(r)

    async def get_zone(self, zone_id: str) -> Dict[str, Any]:
        r = await self._client.get(f"/zones/{zone_id}")
        return await self._ok(r)

    async def list_records(self, zone_id: str, type_filter: Optional[str] = None) -> List[Dict[str, Any]]:
        params = {"per_page": 100}
        if type_filter:
            params["type"] = type_filter
        r = await self._client.get(f"/zones/{zone_id}/dns_records", params=params)
        return await self._ok(r)

    async def create_record(self, zone_id: str, type: str, name: str,
                            content: str, ttl: int = 1, priority: Optional[int] = None) -> Dict[str, Any]:
        body = {"type": type, "name": name, "content": content, "ttl": ttl}
        if priority is not None and type == "MX":
            body["priority"] = priority
        r = await self._client.post(f"/zones/{zone_id}/dns_records", json=body)
        return await self._ok(r)

    async def update_record(self, zone_id: str, rec_id: str, **fields) -> Dict[str, Any]:
        r = await self._client.put(f"/zones/{zone_id}/dns_records/{rec_id}", json=fields)
        return await self._ok(r)

    async def delete_record(self, zone_id: str, rec_id: str) -> Dict[str, Any]:
        r = await self._client.delete(f"/zones/{zone_id}/dns_records/{rec_id}")
        return await self._ok(r)


def get_client() -> Optional[Cloudflare]:
    tok = load_token()
    if not tok:
        return None
    return Cloudflare(tok)


async def generate_dkim_keypair(selector: str = "mta1",
                                domain: Optional[str] = None) -> Dict[str, str]:
    """Generate an RSA-style DKIM keypair (1024-bit for speed).
    Returns {'private_key', 'public_key', 'selector', 'domain',
    'dns_txt': 'v=DKIM1; k=rsa; p=...'}"""
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.primitives import serialization
    key = rsa.generate_private_key(public_exponent=65537, key_size=1024)
    private_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    public_pem = key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    # Extract raw public modulus base64 (no header/footer)
    import re
    b64 = re.sub(r"-----[A-Z ]+-----|\s", "", public_pem)
    dns_txt = f"v=DKIM1; k=rsa; p={b64}"
    return {
        "private_key": private_pem,
        "public_key": public_pem,
        "selector": selector,
        "domain": domain or "",
        "dns_txt": dns_txt,
    }


async def setup_mail_wizard(zone_id: str, hostname: str, ip: str,
                            dkim_selector: str = "mta1",
                            client: Optional[Cloudflare] = None) -> Dict[str, Any]:
    """Create MX, A, SPF, DMARC, DKIM records in one call."""
    if client is None:
        client = get_client()
    if client is None:
        raise Exception("Cloudflare not connected")
    results = {}
    # A record
    results["a"] = await client.create_record(zone_id, "A", hostname, ip, ttl=1)
    # MX
    results["mx"] = await client.create_record(
        zone_id, "MX", hostname, f"10 {hostname}.", ttl=1, priority=10)
    # SPF
    spf = f"v=spf1 ip4:{ip} -all"
    results["spf"] = await client.create_record(zone_id, "TXT", hostname, spf, ttl=1)
    # DMARC
    dmarc_domain = f"_dmarc.{hostname}"
    dmarc = f"v=DMARC1; p=reject; rua=mailto:postmaster@{hostname}; pct=100"
    results["dmarc"] = await client.create_record(zone_id, "TXT", dmarc_domain, dmarc, ttl=1)
    # DKIM
    kp = await generate_dkim_keypair(dkim_selector, hostname)
    dkim_name = f"{dkim_selector}._domainkey.{hostname}"
    results["dkim"] = await client.create_record(zone_id, "TXT", dkim_name, kp["dns_txt"], ttl=1)
    results["dkim_private_key"] = kp["private_key"]
    results["dkim_public_pem"] = kp["public_key"]
    return results


def get_public_resolver():
    """Returns the system resolver; PyDNS-resolver is heavy — use socket.getaddrinfo."""
    import socket
    return socket


# === SPF record builder / explainer (test_spf_*) ===
SPF_MAX_LOOKUPS = 10


def build_spf_record(
    *,
    ipv4: Optional[List[str]] = None,
    ipv6: Optional[List[str]] = None,
    includes: Optional[List[str]] = None,
    redirect: Optional[str] = None,
    qualifier: str = "-",
) -> str:
    """Build an SPF (Sender Policy Framework) DNS TXT record.

    All mechanisms are optional. The trailing default qualifier is ``-``
    (hardfail) unless overridden via the ``qualifier`` parameter
    (``"+"``, ``"-"``, ``"~"``, ``"?"``).

    Returns a string beginning with ``v=spf1`` and ending with the
    requested qualifier (``-all``, ``+all``, ``~all`` or ``?all``). When
    ``redirect`` is provided, the trailing ``all`` term is omitted and
    the ``redirect=`` mechanism is used instead.

    Raises ``ValueError`` if the number of DNS-terminating ``include``
    mechanisms would exceed RFC 7208's 10-lookup limit.
    """
    parts = ["v=spf1"]
    if ipv4:
        for ip in ipv4:
            parts.append(f"ip4:{ip}")
    if ipv6:
        for ip in ipv6:
            parts.append(f"ip6:{ip}")
    if includes:
        if len(includes) > SPF_MAX_LOOKUPS:
            raise ValueError(
                f"SPF record has {len(includes)} include mechanisms; "
                f"RFC 7208 limits SPF to {SPF_MAX_LOOKUPS} DNS-terminating lookups."
            )
        for inc in includes:
            parts.append(f"include:{inc}")
    if redirect:
        parts.append(f"redirect={redirect}")
    else:
        if qualifier not in {"+", "-", "~", "?"}:
            qualifier = "-"
        parts.append(f"{qualifier}all")
    return " ".join(parts)


def explain_spf(record: str, domain: Optional[str] = None) -> Dict[str, Any]:
    """Parse and explain an SPF TXT record.

    Returns a dict with at least one of: ``summary``, ``explanation``,
    ``mechanisms``, ``valid``. If ``domain`` is provided and the record
    uses ``redirect=<domain>`` where ``<domain> == domain``, a loop is
    flagged. A record with more than ``SPF_MAX_LOOKUPS`` DNS-terminating
    mechanisms is flagged as having ``excessive_lookups``.
    """
    result: Dict[str, Any] = {
        "ok": True,
        "valid": True,
        "summary": "",
        "explanation": "",
        "mechanisms": [],
        "lookups": 0,
        "errors": [],
        "warnings": [],
    }
    if not record or not isinstance(record, str):
        result["ok"] = False
        result["valid"] = False
        result["errors"].append("empty or non-string record")
        result["summary"] = "invalid"
        return result

    tokens = record.strip().split()
    if not tokens or tokens[0].lower() != "v=spf1":
        result["ok"] = False
        result["valid"] = False
        result["errors"].append("record must begin with v=spf1")
        result["summary"] = "missing v=spf1 version tag"
        return result

    mechanisms = tokens[1:]
    terminating_lookups = 0
    redirects = []
    explanations = []
    for mech in mechanisms:
        m = mech
        if ":" in m:
            kind, _ = m.split(":", 1)
        else:
            kind = m
        kind = kind.lower()
        result["mechanisms"].append(m)
        explanations.append(m)
        if kind in {"include", "a", "mx", "ptr", "exists", "redirect"}:
            terminating_lookups += 1
        if kind == "redirect":
            target = m.split("=", 1)[1] if "=" in m else ""
            redirects.append(target)
            if domain and target.lower() == domain.lower():
                result["ok"] = False
                result["valid"] = False
                result["errors"].append(f"redirect loop: {target} points back to {domain}")
                result["warnings"].append("redirect loop detected")
    result["lookups"] = terminating_lookups
    if terminating_lookups > SPF_MAX_LOOKUPS:
        result["ok"] = False
        result["valid"] = False
        result["errors"].append(
            f"too many lookups: {terminating_lookups} > {SPF_MAX_LOOKUPS}"
        )
        result["warnings"].append(f"excessive lookups: {terminating_lookups} > 10")

    if result["errors"]:
        result["summary"] = "; ".join(result["errors"])
    elif result["warnings"]:
        result["summary"] = "; ".join(result["warnings"])
    else:
        result["summary"] = f"valid SPF record with {terminating_lookups} lookups"
    result["explanation"] = " ".join(explanations)
    return result


async def dns_lookup(name: str, rtype: str = "A") -> List[str]:
    import asyncio
    import dns.resolver  # type: ignore
    try:
        answers = await asyncio.to_thread(
            dns.resolver.resolve, name, rtype, lifetime=5)
        return [str(r) for r in answers]
    except Exception:
        return []


async def verify_records(zone_name: str, expected: Dict[str, str]) -> Dict[str, Any]:
    """expected: { 'A hostname': 'expected_ip', 'MX hostname': 'expected_mx', 'TXT hostname': 'expected' }
    Returns {'pass': N, 'fail': M, 'details': [...]}
    """
    details = []
    pass_n = fail_n = 0
    for fqdn, expected_value in expected.items():
        # Determine type by splitting 'TYPE hostname'
        parts = fqdn.split(" ", 1)
        rtype, name = parts[0], parts[1]
        full_name = name if name.endswith(zone_name) else f"{name}.{zone_name}"
        records = await dns_lookup(full_name, rtype)
        match = expected_value in records if rtype in ("A", "AAAA", "CNAME") else any(
            expected_value in r for r in records)
        details.append({
            "type": rtype, "name": full_name,
            "expected": expected_value, "got": records,
            "match": match,
        })
        if match:
            pass_n += 1
        else:
            fail_n += 1
    return {"pass": pass_n, "fail": fail_n, "details": details}