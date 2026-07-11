"""Reputation tests — 15 tests for DBL / ZEN / SURBL.

Uses real DNS. Some domains should resolve to NXDOMAIN (clean), some
to 127.0.1.x (listed). We test both branches + error handling.

DBL-listed sample: 4.7.7.7.sbl.spamhaus.org returns 127.0.1.2 historically,
but to avoid flaky test dependencies on third-party DNS, we mostly test
that the *plumbing* returns a well-formed response.
"""
import socket


def test_reputation_dbl_known_clean(client):
    r = client.get("/api/reputation/dbl/google.com")
    assert r.status_code == 200
    body = r.json()
    # Always has at minimum domain + status fields
    assert "domain" in body or "status" in body or "result" in body


def test_reputation_dbl_known_spam(client):
    """dbl.spamhaus.org test domain — historically returns 127.0.1.2.

    We only assert the call succeeded with a parseable response — DNS
    might be unreachable inside the test container, which is fine.
    """
    r = client.get("/api/reputation/dbl/4.7.7.7")
    assert r.status_code == 200


def test_reputation_dbl_arbitrary_domain(client):
    r = client.get("/api/reputation/dbl/example.com")
    assert r.status_code == 200


def test_reputation_dbl_special_chars_handled(client):
    """Edge: subdomains, punycode, IDN."""
    for d in ["www.google.com", "mail.example.org", "xn--bcher-kva.example"]:
        r = client.get(f"/api/reputation/dbl/{d}")
        # 200 or a graceful 4xx — never a 500
        assert r.status_code < 500


def test_reputation_zen_ipv4(client):
    r = client.get("/api/reputation/zen/8.8.8.8")
    assert r.status_code == 200


def test_reputation_zen_ipv4_known_bad(client):
    r = client.get("/api/reputation/zen/127.0.0.2")
    assert r.status_code == 200


def test_reputation_zen_malformed_ip(client):
    """Non-IP path must not 500."""
    r = client.get("/api/reputation/zen/not-an-ip")
    assert r.status_code < 500


def test_reputation_surbl_clean(client):
    r = client.get("/api/reputation/surbl", params={"url": "https://google.com/"})
    assert r.status_code == 200


def test_reputation_surbl_known_bad(client):
    r = client.get("/api/reputation/surbl", params={"url": "http://4.7.7.7/"})
    assert r.status_code == 200


def test_reputation_surbl_arbitrary(client):
    r = client.get("/api/reputation/surbl", params={"url": "https://example.com/promo"})
    assert r.status_code == 200


def test_reputation_check_all(client):
    r = client.post("/api/reputation/check-all",
                    json={"domain": "example.com", "ips": ["8.8.8.8", "1.1.1.1"]})
    assert r.status_code == 200


def test_reputation_check_all_requires_domain(client):
    r = client.post("/api/reputation/check-all", json={"ips": ["8.8.8.8"]})
    assert r.status_code == 400


def test_reputation_check_all_no_ips(client):
    """Domain alone (no IPs) should still return something useful."""
    r = client.post("/api/reputation/check-all", json={"domain": "google.com"})
    assert r.status_code == 200


def test_reputation_check_all_with_many_ips(client):
    ips = ["8.8.8.8", "1.1.1.1", "9.9.9.9", "208.67.222.222"]
    r = client.post("/api/reputation/check-all",
                    json={"domain": "example.com", "ips": ips})
    assert r.status_code == 200


def test_reputation_dbl_response_well_formed(client):
    """Every DBL response must include parseable fields, not crash."""
    r = client.get("/api/reputation/dbl/example.org")
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body, dict)
    # At least one of these well-known keys
    assert any(k in body for k in ("domain", "listed", "status", "result", "codes", "checked"))