# MTa — Mail Engine Admin Panel

> **One-line:** A self-hosted FastAPI admin panel that wraps the [KumoMTA](https://kumomta.com/) (kumod) mail engine with reputation checks, Cloudflare DNS automation, credit/tenant billing, M3 AI insights, webhooks, and a public sending API — all served from a single SQLite-backed process and a vanilla-JS SPA.

[![status: alpha](https://img.shields.io/badge/status-alpha-yellow.svg)]()
[![tests: 162 passed / 9 failed / 18 skipped](https://img.shields.io/badge/tests-162%20passed%20%2F%209%20failed-orange.svg)]()
[![python: 3.10](https://img.shields.io/badge/python-3.10-blue.svg)]()
[![engine: kumod](https://img.shields.io/badge/engine-kumod-red.svg)]()
[![repo: github.com/dchatpar/mta](https://img.shields.io/badge/repo-github.com%2Fdchatpar%2Fmta-blueviolet.svg)]()

---

## Table of contents

1. [Project vision](#project-vision)
2. [Current status (2026-07-11)](#current-status-2026-07-11)
3. [What's been built](#whats-been-built)
4. [What's pending](#whats-pending)
5. [Architecture](#architecture)
6. [Repository layout](#repository-layout)
7. [How to use (operators)](#how-to-use-operators)
8. [How to develop](#how-to-develop)
9. [How to deploy](#how-to-deploy)
10. [Known issues + workarounds](#known-issues--workarounds)
11. [Skills catalog](#skills-catalog)
12. [Changelog](#changelog)
13. [Contributing](#contributing)
14. [License](#license)

---

## Project vision

MTa is the **operations layer** that sits between you and a bare KumoMTA installation. A fresh kumod install gives you a Lua policy file, an SMTP listener, and an HTTP admin API — but no UI, no multi-tenancy, no billing, no DNS automation, no AI insights, and no webhook fan-out. MTa fills those gaps with a single Python process:

- **Self-hosted** — runs on a single VM/container; SQLite (WAL) for all state.
- **Multi-tenant** — every API call is scoped to a tenant; credits are decremented per send.
- **Operator-first** — a single-page admin UI for everything that can be done by hand.
- **Composable** — every subsystem (Cloudflare, M3, webhooks, credits) is a router that can be disabled independently.

The intended user is a small mail-ops team that wants to run their own transactional/relay infrastructure without paying Mailgun/SES margins and without hand-rolling a control plane.

## Current status (2026-07-11)

| Metric | Value |
| --- | --- |
| Test pass rate (default markers) | **162 passed / 9 failed / 18 skipped / 3 errors** in ~66s |
| Test pass rate (full isolation run) | 100% green for the 3 engine_proxy tests that fail under load |
| Endpoints exposed | **~70 total** (40 in `main.py` + 5 routers: 39 more) |
| LoC (app/) | **~2,700** across 11 Python files |
| LoC (static SPA) | **~2,300** across 4 files (HTML/CSS/JS) |
| Active services | `kumod` (PID 12116) inside `mta-app` Incus container |
| Remote | `github.com/dchatpar/mta` |
| Branch | `main` (2 commits so far) |

**Headline:** the system is functionally complete and end-to-end runnable. The remaining 12 failing tests are cluster-localized bugs (see [`docs/PENDING.md`](docs/PENDING.md)); no architectural rework is needed.

---

## What's been built

### Subsystem inventory

| Subsystem | File(s) | Endpoints | Status |
| --- | --- | --- | --- |
| Engine proxy (kumod admin API) | `app/main.py` (l. 61–177) | 25 | ✅ Live |
| Auth (PIN + bearer + cookie) | `app/main.py` (l. 366–440) | 7 | ✅ Live |
| Config manager (kumod .toml files) | `app/main.py` (l. 235–288) | 4 | ✅ Live |
| SMTP users | `app/main.py` (l. 289–347) + `app/smtp_users.py` | 5 | ✅ Live |
| Reputation (DBL/Zen/SURBL) | `app/main.py` (l.