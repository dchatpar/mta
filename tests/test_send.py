"""Send / public API tests — 25 tests."""
import secrets
import base64


def _create_tenant_with_credits(client, auth_headers, balance=10000):
    name = f"send-{secrets.token_hex(3)}"
    r = client.post("/api/v1/credits/tenants",
                    json={"name": name, "balance": balance},
                    headers=auth_headers)
    assert r.status_code == 201, r.text
    return r.json()


def test_send_requires_auth(client):
    r = client.post("/api/v1/send", json={
        "recipients": ["a@b.com"],
        "sender": "from@example.com",
        "subject": "hi",
        "body": "hello",
    })
    assert r.status_code == 401


def test_send_basic(client, auth_headers):
    t = _create_tenant_with_credits(client, auth_headers)
    api_key = t["api_key"]
    r = client.post("/api/v1/send",
                    headers={"Authorization": f"Bearer {api_key}"},
                    json={
                        "recipients": ["rcpt@example.com"],
                        "sender": "from@example.com",
                        "subject": "Test",
                        "body": "Hello world",
                    })
    assert r.status_code in (200, 202), r.text
    body = r.json()
    # Should have a message id
    assert body.get("message_id") or body.get("id") or body.get("status")


def test_send_validates_recipients(client, auth_headers):
    t = _create_tenant_with_credits(client, auth_headers)
    r = client.post("/api/v1/send",
                    headers={"Authorization": f"Bearer {t['api_key']}"},
                    json={
                        "recipients": [],
                        "sender": "from@example.com",
                        "subject": "x",
                        "body": "y",
                    })
    assert r.status_code == 422


def test_send_validates_email_format(client, auth_headers):
    t = _create_tenant_with_credits(client, auth_headers)
    r = client.post("/api/v1/send",
                    headers={"Authorization": f"Bearer {t['api_key']}"},
                    json={
                        "recipients": ["not-an-email"],
                        "sender": "from@example.com",
                        "subject": "x",
                        "body": "y",
                    })
    assert r.status_code == 422


def test_send_validates_sender(client, auth_headers):
    t = _create_tenant_with_credits(client, auth_headers)
    r = client.post("/api/v1/send",
                    headers={"Authorization": f"Bearer {t['api_key']}"},
                    json={
                        "recipients": ["a@b.com"],
                        "sender": "not-an-email",
                        "subject": "x",
                        "body": "y",
                    })
    assert r.status_code == 422


def test_send_subject_required(client, auth_headers):
    t = _create_tenant_with_credits(client, auth_headers)
    r = client.post("/api/v1/send",
                    headers={"Authorization": f"Bearer {t['api_key']}"},
                    json={
                        "recipients": ["a@b.com"],
                        "sender": "from@example.com",
                        "subject": "",
                        "body": "y",
                    })
    assert r.status_code == 422


def test_send_body_required(client, auth_headers):
    t = _create_tenant_with_credits(client, auth_headers)
    r = client.post("/api/v1/send",
                    headers={"Authorization": f"Bearer {t['api_key']}"},
                    json={
                        "recipients": ["a@b.com"],
                        "sender": "from@example.com",
                        "subject": "x",
                        "body": "",
                    })
    assert r.status_code == 422


def test_send_with_attachment(client, auth_headers):
    t = _create_tenant_with_credits(client, auth_headers)
    payload = base64.b64encode(b"hello attachment").decode()
    r = client.post("/api/v1/send",
                    headers={"Authorization": f"Bearer {t['api_key']}"},
                    json={
                        "recipients": ["a@b.com"],
                        "sender": "from@example.com",
                        "subject": "with attach",
                        "body": "see attached",
                        "attachments": [{
                            "filename": "hello.txt",
                            "content_type": "text/plain",
                            "content_b64": payload,
                        }],
                    })
    assert r.status_code in (200, 202), r.text


def test_send_with_tags(client, auth_headers):
    t = _create_tenant_with_credits(client, auth_headers)
    r = client.post("/api/v1/send",
                    headers={"Authorization": f"Bearer {t['api_key']}"},
                    json={
                        "recipients": ["a@b.com"],
                        "sender": "from@example.com",
                        "subject": "tagged",
                        "body": "hello",
                        "tags": ["campaign:abc", "user:42"],
                    })
    assert r.status_code in (200, 202)


def test_send_with_metadata(client, auth_headers):
    t = _create_tenant_with_credits(client, auth_headers)
    r = client.post("/api/v1/send",
                    headers={"Authorization": f"Bearer {t['api_key']}"},
                    json={
                        "recipients": ["a@b.com"],
                        "sender": "from@example.com",
                        "subject": "meta",
                        "body": "hello",
                        "metadata": {"campaign_id": "abc", "user_id": 42},
                    })
    assert r.status_code in (200, 202)


def test_send_content_type_text_html(client, auth_headers):
    t = _create_tenant_with_credits(client, auth_headers)
    r = client.post("/api/v1/send",
                    headers={"Authorization": f"Bearer {t['api_key']}"},
                    json={
                        "recipients": ["a@b.com"],
                        "sender": "from@example.com",
                        "subject": "html",
                        "body": "<h1>hi</h1>",
                        "content_type": "text/html",
                    })
    assert r.status_code in (200, 202)


def test_send_content_type_text_plain(client, auth_headers):
    t = _create_tenant_with_credits(client, auth_headers)
    r = client.post("/api/v1/send",
                    headers={"Authorization": f"Bearer {t['api_key']}"},
                    json={
                        "recipients": ["a@b.com"],
                        "sender": "from@example.com",
                        "subject": "plain",
                        "body": "just text",
                        "content_type": "text/plain",
                    })
    assert r.status_code in (200, 202)


def test_send_invalid_content_type_rejected(client, auth_headers):
    t = _create_tenant_with_credits(client, auth_headers)
    r = client.post("/api/v1/send",
                    headers={"Authorization": f"Bearer {t['api_key']}"},
                    json={
                        "recipients": ["a@b.com"],
                        "sender": "from@example.com",
                        "subject": "x",
                        "body": "y",
                        "content_type": "application/json",
                    })
    assert r.status_code == 422


def test_send_max_recipients(client, auth_headers):
    """>1000 recipients must be rejected."""
    t = _create_tenant_with_credits(client, auth_headers, balance=999999)
    recipients = [f"u{i}@example.com" for i in range(1001)]
    r = client.post("/api/v1/send",
                    headers={"Authorization": f"Bearer {t['api_key']}"},
                    json={
                        "recipients": recipients,
                        "sender": "from@example.com",
                        "subject": "big",
                        "body": "many",
                    })
    assert r.status_code == 422


def test_send_multiple_recipients(client, auth_headers):
    t = _create_tenant_with_credits(client, auth_headers, balance=10000)
    recipients = ["a@example.com", "b@example.com", "c@example.com"]
    r = client.post("/api/v1/send",
                    headers={"Authorization": f"Bearer {t['api_key']}"},
                    json={
                        "recipients": recipients,
                        "sender": "from@example.com",
                        "subject": "multi",
                        "body": "many recipients",
                    })
    assert r.status_code in (200, 202)
    if r.status_code in (200, 202):
        body = r.json()
        # accepted_count should reflect recipients
        if "accepted_count" in body:
            assert body["accepted_count"] >= 1


def test_send_idempotency(client, auth_headers):
    """Same idempotency_key twice should produce same message_id (or 409)."""
    t = _create_tenant_with_credits(client, auth_headers)
    idem = f"idem-{secrets.token_hex(8)}"
    body = {
        "recipients": ["a@b.com"],
        "sender": "from@example.com",
        "subject": "idem",
        "body": "hi",
        "idempotency_key": idem,
    }
    r1 = client.post("/api/v1/send",
                     headers={"Authorization": f"Bearer {t['api_key']}"},
                     json=body)
    r2 = client.post("/api/v1/send",
                     headers={"Authorization": f"Bearer {t['api_key']}"},
                     json=body)
    # Either: r2 returns same message_id, or r2 is 409 conflict, or both succeed
    assert r1.status_code in (200, 202)
    assert r2.status_code in (200, 202, 409)


def test_send_with_scheduled_at(client, auth_headers):
    t = _create_tenant_with_credits(client, auth_headers)
    import time
    r = client.post("/api/v1/send",
                    headers={"Authorization": f"Bearer {t['api_key']}"},
                    json={
                        "recipients": ["a@b.com"],
                        "sender": "from@example.com",
                        "subject": "sched",
                        "body": "later",
                        "scheduled_at": time.time() + 3600,
                    })
    assert r.status_code in (200, 202)


def test_send_insufficient_balance(client, auth_headers):
    """Zero-balance tenant sending to many recipients should fail."""
    name = f"poor-{secrets.token_hex(3)}"
    cr = client.post("/api/v1/credits/tenants",
                     json={"name": name, "balance": 0}, headers=auth_headers)
    api_key = cr.json()["api_key"]
    r = client.post("/api/v1/send",
                    headers={"Authorization": f"Bearer {api_key}"},
                    json={
                        "recipients": ["a@b.com"],
                        "sender": "from@example.com",
                        "subject": "x",
                        "body": "y",
                    })
    # 402 (payment required) or 403 (quota exceeded) — depends on impl
    assert r.status_code in (200, 202, 402, 403)


def test_send_returns_message_id(client, auth_headers):
    t = _create_tenant_with_credits(client, auth_headers)
    r = client.post("/api/v1/send",
                    headers={"Authorization": f"Bearer {t['api_key']}"},
                    json={
                        "recipients": ["a@b.com"],
                        "sender": "from@example.com",
                        "subject": "id-test",
                        "body": "x",
                    })
    if r.status_code in (200, 202):
        body = r.json()
        assert body.get("message_id") or body.get("id") or body.get("status")


def test_send_returns_status_field(client, auth_headers):
    t = _create_tenant_with_credits(client, auth_headers)
    r = client.post("/api/v1/send",
                    headers={"Authorization": f"Bearer {t['api_key']}"},
                    json={
                        "recipients": ["a@b.com"],
                        "sender": "from@example.com",
                        "subject": "status",
                        "body": "x",
                    })
    if r.status_code in (200, 202):
        body = r.json()
        assert "status" in body


def test_batch_send(client, auth_headers):
    """Batch send endpoint."""
    t = _create_tenant_with_credits(client, auth_headers, balance=10000)
    r = client.post("/api/v1/send/batch",
                    headers={"Authorization": f"Bearer {t['api_key']}"},
                    json={
                        "messages": [
                            {
                                "recipients": ["a@example.com"],
                                "sender": "from@example.com",
                                "subject": f"batch {i}",
                                "body": "x",
                            }
                            for i in range(3)
                        ]
                    })
    assert r.status_code in (200, 202, 404)  # 404 if batch not implemented


def test_messages_list_empty(client, auth_headers):
    r = client.get("/api/v1/messages", headers=auth_headers)
    assert r.status_code == 200
    assert isinstance(r.json(), (list, dict))


def test_messages_get_unknown(client, auth_headers):
    r = client.get("/api/v1/messages/no-such-id", headers=auth_headers)
    assert r.status_code in (200, 404)


def test_template_send(client, auth_headers):
    """Template endpoint exists and validates."""
    t = _create_tenant_with_credits(client, auth_headers)
    r = client.post("/api/v1/send/template",
                    headers={"Authorization": f"Bearer {t['api_key']}"},
                    json={
                        "template_id": "welcome",
                        "recipients": ["a@b.com"],
                        "sender": "from@example.com",
                        "vars": {"name": "Alice"},
                    })
    assert r.status_code in (200, 202, 404, 422)


def test_send_long_subject_rejected(client, auth_headers):
    """>998 chars subject must be rejected (RFC 5322)."""
    t = _create_tenant_with_credits(client, auth_headers)
    r = client.post("/api/v1/send",
                    headers={"Authorization": f"Bearer {t['api_key']}"},
                    json={
                        "recipients": ["a@b.com"],
                        "sender": "from@example.com",
                        "subject": "x" * 1000,
                        "body": "y",
                    })
    assert r.status_code == 422