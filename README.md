# MTa — Mail Engine Admin Panel

> **One-line:** A self-hosted FastAPI admin panel that wraps the [KumoMTA](https://kumomta.com/) (kumod) mail engine with reputation checks, Cloudflare DNS automation, credit/tenant billing, M3 AI insights, webhooks, and a public sending API — all served from a single SQLite-backed process and a vanilla-JS SPA.

**Status:** alpha  
**Tests:** 162 passed / 9 failed / 18 skipped / 3 errors  
**Python:** 3.10  
**Engine:** kumod  
**Repo:** github.com/dchatpar/mta  

---

## Table of contents

1. [Project vision](#project-vision)
2. [Current status](#current-status-2026-07-11)
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

MTa is the **operations layer** that sits between you and a bare KumoMTA installation. A fresh kumod install gives you a Lua policy file, an SMTP listener, and an HTTP admin API — but no UI, no multi-tenancy, no billing, no DNS automation, no AI insights, and no webhook fan-out. MTa
