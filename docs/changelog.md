# Changelog — prodDisruption

This file tracks all significant changes to the system. AI agents and human developers must append an entry here whenever features are added, architecture changes, dependencies change, APIs change, migrations occur, or deployment changes happen.

**Format:**
```markdown
## [YYYY-MM-DD] — Short title

**Type:** feature | bugfix | refactor | dependency | api-change | migration | deployment

**Files changed:** list the modified files

**Summary:** 2-3 sentences describing what changed and why.

**Breaking changes:** yes/no — and if yes, what callers/tenants are affected.
```

---

## [2026-05-25] — Production architecture document for Microsoft Architecture Review

**Type:** feature (documentation)

**Files changed:** `docs/production-architecture.md` (new), `CLAUDE.md` (documentation index)

**Summary:** Added a consolidated production architecture document reflecting the currently deployed Azure topology — three Container Apps (`api-prod`, `api-admin`, `mcp-server`), APIM Standard v2, Static Web Apps for the admin SPA, Key Vault, Cosmos DB (MongoDB API), and Azure AI Foundry. Document is structured for Microsoft Azure Architecture review (Well-Architected Framework alignment, named SKUs, network/identity topology, multi-tenancy, observability, DR, cost). Complements the existing `architecture.md` (AS-IS) and `future-architecture.md` (TO-BE).

**Breaking changes:** No.

---

## [2024-03-08] — Initial service scaffolding

**Type:** feature

**Files changed:** `server_old.py`, `services/`, `tools/`, `config/settings.py`, `tenant/tenant_config.py`, `.env`

**Summary:** Initial implementation of the MCP server (`server_old.py`) and supporting service clients. Single synchronous `handle_disruption` tool with sequential seat map fetching. Two-tenant setup (`indigo_mock`, `airline_mock`) pointing to Azure Container Apps mock APIs.

**Breaking changes:** No — initial version.

---

## [2024-03-09] — Thread pool parallelism for seat maps

**Type:** refactor

**Files changed:** `server_old1.0.py`

**Summary:** Replaced sequential seat map fetching with `ThreadPoolExecutor` parallelism (`max_workers=20`). CDP profile and flight search also parallelized with futures. Server file renamed to `server_old1.0.py` preserving previous version.

**Breaking changes:** No — same MCP API contract.

---

## [2024-03-10] — Async migration and runtime config

**Type:** refactor + feature

**Files changed:** `server.py`, `api_prod.py`, `api_main.py`, `config/runtime_config.py`, `services/http_client.py`, `services/airline_client.py`, `services/cdp_client.py`, `services/disruption_client.py`

**Summary:** Full async migration of the MCP server using `asyncio.gather()` instead of `ThreadPoolExecutor`. Service clients migrated from `requests` to `httpx.AsyncClient` (shared singleton). FastAPI layer (`api_prod.py`) created alongside `api_main.py` (which remains as reference). Runtime config layer introduced, connecting to Cosmos DB for active agent resolution.

**Breaking changes:** Service client functions are now `async` — callers must `await` them.

---

## [2024-03-11] — Multi-tenant Cosmos DB config

**Type:** feature

**Files changed:** `tenant/tenant_config.py`, `config/cosmos_data_fetcher.py`, `seed_tenants.py`

**Summary:** Tenant API URLs moved from hardcoded settings to Cosmos DB `tenants` collection. `get_tenant_config()` now tries Cosmos DB first with fallback to `FALLBACK_TENANTS` dict. `seed_tenants.py` added to populate initial tenant records.

**Breaking changes:** No — fallback dict ensures backward compatibility.

---

## [2024-04-16] — Admin prompt injection and production API hardening

**Type:** feature + refactor

**Files changed:** `api_prod.py`, `config/cosmos_data_fetcher.py`

**Summary:** Admin prompt injection added to `run_agent()` — `system_prompt` is prepended, `text` field is appended to base prompts. `update_runtime_config()` now raises an exception (config must be managed via admin panel / Cosmos DB). `cosmos_data_fetcher.py` expanded with `fetch_recovery_prompt()`, `fetch_messaging_prompt()`, and `fetch_tenant_config()`. `AdminConfig` Pydantic model updated to include `system_prompt` and `text` fields.

**Breaking changes:** `POST /admin/update-config` now returns HTTP 500 instead of updating config — callers must use the admin panel instead.

---

## [2026-05-25] — Architecture and flow diagrams created

**Type:** feature

**Files changed:** `docs/diagrams.md`

**Summary:** Full architecture and flow diagram suite added. Covers system architecture (stakeholder + technical), Recovery flow (cancelled flights), Messaging flow (delayed flights), all error/edge-case paths, admin config hot-swap flow, config priority layers, and process startup sequence. Mermaid diagrams embedded for GitHub/Notion rendering; Excalidraw interactive version at https://excalidraw.com/#json=5XivTgCo7aPz01NUBRe9U,Wo7f9Dag1owT5gMlGE0sIw.

**Breaking changes:** No — documentation only.

---

## [2026-05-07] — Comprehensive documentation system created

**Type:** feature

**Files changed:** `CLAUDE.md`, `docs/architecture.md`, `docs/backend.md`, `docs/api.md`, `docs/database.md`, `docs/deployment.md`, `docs/debugging.md`, `docs/conventions.md`, `docs/ai-rules.md`, `docs/roadmap.md`, `docs/changelog.md`, `docs/frontend.md`

**Summary:** Full modular documentation system created by analyzing the complete codebase. `CLAUDE.md` serves as the master AI context file. Individual `docs/` files cover each subsystem. Known bugs and technical debt catalogued in `docs/roadmap.md`. AI agent rules formalized in `docs/ai-rules.md`.

**Breaking changes:** No code changes — documentation only.

---

## [2026-05-25] — Fix BUG-001: validate_request missing tenant_id and async

**Type:** bugfix

**Files changed:** `tools/validator.py`, `server.py`

**Summary:** `validate_request()` and `check_user_autorecovery_eligibility()` were missing the `tenant_id` parameter required by `cdp_client.find_users()`. Both functions were also synchronous but called an async function without `await`, causing `find_users()` to return a coroutine object instead of user data — meaning the eligibility check silently passed all passengers through. Both functions are now `async`, accept `tenant_id` as the first argument, and `await` the CDP call. The call site in `server.py:handle_disruption()` is updated to `await validate_request(tenant_id, ...)`.

**Breaking changes:** No — this fixes the cancellation flow; callers inside `server.py` are updated in the same commit.
