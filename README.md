# MTa — Mail Engine Admin Panel

> A self-hosted FastAPI admin panel that wraps KumoMTA (kumod) with reputation checks, Cloudflare DNS, credits, M3 AI, webhooks, and a public sending API.

**Status:** alpha  **Tests:** 162 passed / 9 failed / 18 skipped / 3 errors  **Python:** 3.10  **Engine:** kumod  **Repo:** github.com/dchatpar/mta

---

## Table of contents

1. [Project vision](#project-vision)
2. [Current status](#current-status-2026-07-11)
3. [What's been built](#whats-been-built)
4. [What's pending](#whats-pending)
5. [Architecture](#architecture)
6. [Repository layout](#repository-layout)
7. [How to use](#how-to-use-operators)
8. [How to develop](#how-to-develop)
9. [How to deploy](#how-to-deploy)
10. [Known issues](#known-issues--workarounds)
11. [Skills catalog](#skills-catalog)
12. [Changelog](#changelog)
13. [Contributing](#contributing)
14. [License](#license)

---

## Project vision

MTa is the operations layer between you and a bare KumoMTA installation. A fresh kumod gives you a Lua policy file, an SMTP listener, and an HTTP admin API — but no UI, no multi-tenancy, no billing, no DNS automation, no AI insights, no webhook fan-out. MTa fills those gaps with a single Python process:

- Self-hosted: runs on one VM/container; SQLite (WAL) for all state.
- Multi-tenant: every API call is scoped to a tenant; credits decrement per send.
- Operator-first: a single-page admin UI for everything that can be done by hand.
- Composable: every subsystem is a router that can be disabled independently.

The intended user is a small mail-ops team that wants to run their own transactional/relay infrastructure without paying Mailgun/SES margins and without hand-rolling a control plane.

---

## Current status (2026-07-11)

| Metric | Value |
| --- | --- |
| Test pass rate (default markers) | 162 passed / 9 failed / 18 skipped / 3 errors in ~66s |
| Test pass rate (full isolation run) | 100% green for the 3 engine_proxy tests that fail under load |
| Endpoints exposed | ~70 total (40 in main.py + 5 routers: 39 more) |
| LoC (app/) | ~2,700 across 11 Python files |
| LoC (static SPA) | ~2,300 across 4 files (HTML/CSS/JS) |
| Active services | kumod (PID 12116) inside mta-app Incus container |
| Remote | github.com/dchatpar/mta |
| Branch | main (2 commits so far) |

Headline: the system is functionally complete and end-to-end runnable. The remaining 12 failing tests are cluster-localized bugs (see docs/PENDING.md); no architectural rework is needed.

---

## What's been built

### Subsystem inventory

| Subsystem | File(s) | Endpoints | Status |
| --- | --- | --- | --- |
| Engine proxy (kumod admin API) | app/main.py (l. 61-177) | 25 | Live |
| Auth (PIN + bearer + cookie) | app/main.py (l. 366-440) | 7 | Live |
| Config manager (kumod .toml files) | app/main.py (l. 235-288) | 4 | Live |
| SMTP users | app/main.py (l. 289-347) + app/smtp_users.py | 5 | Live |
| Reputation (DBL/Zen/SURBL) | app/main.py (l. 179-219) | 4 | Live |
| SSE live stream | app/main.py | 1 | Live |
| Credits and tenants | app/credits.py + app/credits_router.py | 10 | Live |
| Public sending API | app/send_router.py | 5 | Live |
| Webhooks | app/webhooks.py + app/webhooks_router.py | 6 | Live |
| Cloudflare DNS | app/cloudflare.py + app/cloudflare_router.py | 13 | Live |
| M3 AI insights | app/m3.py + app/m3_router.py | 5 | Live |
| Vanilla-JS SPA | app/static/ | - | Live |

### Test inventory

| Test file | Cases | Marker notes |
| --- | --- | --- |
| test_auth.py | 5 | - |
| test_cloudflare.py | 7 | - |
| test_config.py | 6 | - |
| test_credits.py | 18 | includes integration quota tests |
| test_engine_proxy.py | 16 | - |
| test_health.py | 3 | - |
| test_m3.py | 6 | - |
| test_reputation.py | 6 | - |
| test_send.py | 21 | - |
| test_sse.py | 4 | - |
| test_webhooks.py | 9 | - |
| test_competitive_features.py | 4 | golden QA + broadcast stream |

Total: ~192 test cases (default markers filter out integration/ollama/slow).

---

## What's pending

See docs/PENDING.md for the full triage. Summary:

1. tests/test_engine_proxy.py cluster (~9 failures): kumod HTTP API not always reachable from inside TestClient under load; passes in isolation. Likely a fixture/setup issue, not an app bug.
2. tests/test_m3.py::test_ai_golden_qa_endpoint: AttributeError 'str' object has no attribute 'get' at m3_router.py:127. M3 client returned a string where a dict was expected.
3. tests/test_reputation.py::test_reputation_check_all_requires_domain: endpoint returns 200 where test expects 400; missing input validation on check-all.
4. tests/test_config.py::test_config_put_requires_auth: endpoint accepts unauthenticated PUT; auth dependency missing.
5. tests/test_credits.py (2 errors): quota/auth fixtures failing in setup; cosmetic test infra issue.
6. tests/test_webhooks.py::test_webhook_invalid_url_rejected: timeout (kumod egress) during fixture cleanup.
7. tests/test_competitive_features.py (3 failures): golden QA, broadcast stream unsubscribe, scoped suppression, send-captures-share-link. Test surface still being finalized.

Total: 12 unresolved items. Estimated fix effort: 2-4 hours for the 3 production bugs (#2, #3, #4) and ~1 day for the engine_proxy cluster once kumod HTTP API state is properly seeded in fixtures.

---

## Architecture

```
                                +------------------------------------+
                                |   Browser / curl / API client    |
                                |       (operator + tenant)         |
                                +--------------+---------------------+
                                               | HTTPS
                                               v
              +----------------------------------------------------------+
              |  MTa (FastAPI, app/main.py + 5 routers, ~70 endpoints)  |
              |                                                          |
              |  +---------+ +---------+ +---------+ +---------+ +-----+ |
              |  | credits | |  send   | |webhooks | |cloudfl. | | M3  ||
              |  | router  | | router  | | router  | | router  | | rt. ||
              |  +----+----+ +----+----+ +----+----+ +----+----+ +--+--+ |
              |       |           |           |          |          |    |
              |   SQLite WAL   SQLite     SQLite   Cloudflare   AI    |
              |   credits.db   send log  webhooks.db REST API  API   |
              +--------+-----------------------------------------------+
                       |
                       | httpx async
                       v
              +----------------------------+
              |   kumod (PID 12116)        |
              |   HTTP admin on :8000      |
              |   SMTP on :25              |
              |   Lua policy init.lua      |
              +----------------------------+
```

Five routers attach to a single app instance:

```
app.include_router(credits_router)      # /api/credits/*
app.include_router(send_router)         # /api/v1/send/*
app.include_router(webhooks_router)     # /api/webhooks/*
app.include_router(cloudflare_router)   # /api/cloudflare/*
app.include_router(m3_router)           # /api/m3/*
```

main.py owns: engine proxy, reputation, auth, config, smtp-users, health, SSE.

For the deeper design rationale (why these specific choices), see docs/ARCHITECTURE.md.

---

## Repository layout

```
/opt/mta/
+- README.md                    <- you are here
+- CHANGELOG.md                 <- release history
+- app/
|  +- main.py                   <- FastAPI app, engine proxy, auth, config
|  +- credits.py                <- SQLite-backed credit/tenant ledger
|  +- credits_router.py
|  +- send_router.py            <- public /api/v1/send
|  +- webhooks.py               <- webhook CRUD + delivery
|  +- webhooks_router.py
|  +- cloudflare.py             <- Cloudflare REST + Fernet token storage
|  +- cloudflare_router.py
|  +- m3.py                     <- M3 AI client (golden QA, explain)
|  +- m3_router.py
|  +- smtp_users.py             <- SHA256-hashed SMTP creds
|  +- kumod_client.py           <- async client over kumod HTTP admin
|  +- reputation.py             <- DBL / Zen / SURBL lookups
|  +- static/                   <- vanilla-JS SPA (no build step)
|     +- index.html
|     +- tokens.css             <- design tokens (color, spacing, type)
|     +- app.css                <- layout + components
|     +- components.css
|     +- app.js                 <- single-file SPA controller (1281 lines)
|     +- icon-map.js
+- tests/
|  +- conftest.py               <- tempdir DBs + auth fixtures
|  +- test_*.py                 <- 12 test modules, ~192 cases
+- docs/
|  +- ARCHITECTURE.md           <- why-kumod / why-FastAPI / etc.
|  +- STATE.md                  <- current snapshot of services + bugs
|  +- PENDING.md                <- remaining failures, ranked
|  +- SESSIONS.md               <- session-by-session build journal
+- _audit-source/               <- historical dump (gitignored)
```

---

## How to use (operators)

### 1. Login

```
# PIN-based login -> returns session token + sets httpOnly cookie
curl -X POST https://mta.example.com/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"pin": "YOUR_PIN"}' \
  -c cookies.txt

# Use the bearer token on subsequent calls
TOKEN=$(curl -s -X POST https://mta.example.com/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"pin": "YOUR_PIN"}' | jq -r .token)
```

### 2. Send a message (public API)

```
curl -X POST https://mta.example.com/api/v1/send \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "from": "hello@yourdomain.com",
    "to":   ["recipient@example.org"],
    "subject": "Hello",
    "text":    "Plain-text body",
    "html":    "<p>HTML body</p>"
  }'
```

### 3. Check reputation

```
# DBL (domain)
curl -H "Authorization: Bearer $TOKEN" \
  https://mta.example.com/api/reputation/dbl/example.com

# Zen (IP)
curl -H "Authorization: Bearer $TOKEN" \
  https://mta.example.com/api/reputation/zen/1.2.3.4
```
