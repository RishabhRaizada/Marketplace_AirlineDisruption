# CLAUDE.md — Master AI Context File
# prodDisruption · Flight Disruption Intelligence Platform

> **For every Claude session working on this project:** Read this file in full before making any changes. It is the single source of truth for architecture, constraints, and AI agent rules.

---

## 1. Project Overview

**prodDisruption** is a multi-tenant, AI-powered flight disruption management system built on **Azure AI Foundry**. When a passenger's flight is cancelled or delayed, this system:

1. Fetches the disruption event from the airline's disruption API
2. Looks up the passenger's CDP (Customer Data Platform) profile to determine their persona (Student, High Spender, Doctor, Defence, General)
3. Routes the request through an Azure AI Agent that either selects the optimal recovery flight + seat (cancellations) or generates personalized delay notification messages (delays)
4. Returns a structured JSON response to the calling frontend

The system is **two-process**: an MCP server (port 8004) handles data orchestration, and a FastAPI server (port configurable) handles Azure AI agent execution and exposes the REST API.

**Language:** Python 3.11+  
**Framework:** FastAPI (REST API) + FastMCP (MCP server)  
**AI Orchestration:** Azure AI Agents SDK (`azure-ai-agents`)  
**Database:** Azure Cosmos DB (MongoDB API) — database: `flight_operations`  
**Auth:** Azure DefaultAzureCredential with CLI fallback  

---

## 2. Documentation Index

| Document | Contents |
|---|---|
| [docs/architecture.md](docs/architecture.md) | System design, data flow diagrams, two-process model |
| [docs/production-architecture.md](docs/production-architecture.md) | Production Azure architecture (3 Container Apps + APIM + SWA + Key Vault) — for Microsoft Architecture review |
| [docs/backend.md](docs/backend.md) | Service-by-service breakdown, all modules |
| [docs/api.md](docs/api.md) | All REST endpoints, request/response schemas |
| [docs/database.md](docs/database.md) | Cosmos DB collections, schemas, query patterns |
| [docs/deployment.md](docs/deployment.md) | How to run locally, env vars, Azure deployment |
| [docs/debugging.md](docs/debugging.md) | Common failures, error codes, log patterns |
| [docs/conventions.md](docs/conventions.md) | Coding style, naming, file organization |
| [docs/ai-rules.md](docs/ai-rules.md) | Rules all AI agents must follow in this codebase |
| [docs/roadmap.md](docs/roadmap.md) | Known bugs, technical debt, planned improvements |
| [docs/changelog.md](docs/changelog.md) | Change history — append every feature/arch change |
| [docs/frontend.md](docs/frontend.md) | Frontend integration notes (no frontend in repo) |

---

## 3. Critical Architecture Rules

These rules define how the system must work. Never violate them without a deliberate refactor.

### Rule 1: Two-Process Boundary
The MCP server (`server.py`) and the FastAPI server (`api_prod.py`) are **separate processes communicating via JSON-RPC over HTTP**. The FastAPI server must never directly call airline, CDP, or disruption services — all data orchestration lives in `server.py`.

### Rule 2: Tenant Isolation
Every external API call must resolve the base URL through `tenant/tenant_config.py → get_tenant_config(tenant_id)`. Never hardcode API base URLs in service clients. Cosmos DB is the source of truth for tenant configs; `FALLBACK_TENANTS` dict is a development safety net only.

### Rule 3: Agent IDs Come From Cosmos DB
Agent IDs are read from the `agents` Cosmos collection at runtime via `config/runtime_config.py → get_runtime_config()`. Never hardcode agent IDs in `api_prod.py` (see `api_main.py` for what the old pattern looked like — do not revert to it).

### Rule 4: MCP Response Schema
Every `handle_disruption` return must be wrapped as:
```python
{"content": [{"type": "json", "json": { ... }}]}
```
The `api_prod.py → execute_mcp_tool()` parser depends on this exact structure.

### Rule 5: Async in MCP, Sync in FastAPI
`server.py` uses `asyncio.gather()` for parallel I/O. Service clients (`airline_client.py`, `cdp_client.py`, `disruption_client.py`) are async. The FastAPI layer (`api_prod.py`) is synchronous — it calls the MCP server via `requests.post()`.

### Rule 6: Admin Config Is Cosmos DB Only
`update_runtime_config()` intentionally raises an exception. Configuration changes (agent IDs, prompts) must go through the external admin panel that writes to Cosmos DB. Never restore the old in-memory dict pattern.

---

## 4. AI Agent Instructions

Instructions for any AI (Claude or otherwise) modifying this codebase:

### Before Making Changes
1. Read `CLAUDE.md` (this file) in full
2. Read the relevant `docs/` file for the subsystem you are modifying
3. Check `docs/changelog.md` for recent changes that might affect your work
4. Review `docs/roadmap.md` for known bugs before introducing new code in affected areas

### When Adding a New API Endpoint
1. Add it to `api_prod.py` (never to `server.py` — MCP tools are not REST endpoints)
2. Define a Pydantic `BaseModel` for the request body
3. Wrap the handler body in try/except and raise `HTTPException(status_code=500, detail=str(e))`
4. Document it in `docs/api.md`

### When Adding a New Flow (e.g., flight_diverted)
1. Add an `elif event["event_type"] == "flight_diverted":` block in `server.py → handle_disruption`
2. Follow the existing return structure: `{"content": [{"type": "json", "json": {..., "flow": "<name>"}}]}`
3. Add the flow name to the agent routing logic in `api_prod.py → run_agent()`
4. Add the prompt for the new flow inside `run_agent()`
5. Document the new flow in `docs/backend.md` and add an entry to `docs/changelog.md`

### When Modifying Prompts
- Prompts live inside `run_agent()` in `api_prod.py` (recovery and messaging prompts)
- The `admin_system` and `admin_user` fields from `get_runtime_config()` are prepended/appended to the base prompt at runtime from Cosmos DB
- Never remove the admin prompt injection logic — it is used by the admin panel
- Keep all JSON output format instructions explicit and strict

### When Modifying the Database Layer
- All Cosmos DB access goes through `config/cosmos_data_fetcher.py`
- Add new fetch functions there; do not create new files for DB access
- Every new public function must be added to `__all__`
- Always use the `_find_one()` helper for single-document reads; use `_get_db()` for queries requiring `find()` or aggregation
- Always close the client in a `finally` block

### When Adding a New Tenant
1. Add to `seed_tenants.py → TENANTS` list
2. Add to `tenant/tenant_config.py → FALLBACK_TENANTS` dict as a backup
3. Run `python seed_tenants.py` to upsert into Cosmos DB

### Preserving Agent Output Parsing
The `safe_json_from_agent()` function in `api_prod.py` handles agents that return JSON wrapped in markdown code fences. Never bypass it. If Azure AI agents change their output format, fix `safe_json_from_agent()`, not the callers.

---

## 5. How to Document Future Updates

Every time a Claude session makes a non-trivial change, it must append an entry to `docs/changelog.md` in this format:

```markdown
## [YYYY-MM-DD] — Short title

**Type:** feature | bugfix | refactor | dependency | api-change | migration | deployment

**Files changed:** list the modified files

**Summary:** 2-3 sentences describing what changed and why.

**Breaking changes:** yes/no — and if yes, what callers/tenants are affected.
```

Additionally:
- If you change an API endpoint, update `docs/api.md`
- If you change the database schema, update `docs/database.md`
- If you fix a known bug from `docs/roadmap.md`, mark it resolved there
- If you introduce new technical debt, add it to `docs/roadmap.md`

---

## 6. Coding and Implementation Constraints

- **Python 3.11+** — use `str | None` union syntax, not `Optional[str]`
- **No new synchronous HTTP calls in `server.py`** — always use `httpx.AsyncClient` via `services/http_client.py`
- **No new synchronous code in service clients** — `airline_client.py`, `cdp_client.py`, `disruption_client.py` must remain async
- **Do not add `print()` calls** — use `logger.info()` / `logger.error()` / `logger.exception()`
- **Do not restore commented-out code** — dead code in `airline_client.py`, `disruption_client.py`, `runtime_config.py` is intentional history and should be cleaned up, not uncommented
- **Temperature and top_p for agents are fixed at 0.1** — this is deliberate (deterministic scoring)
- **Agent timeout is 120 seconds** — do not increase without understanding the cost implications
- **CORS is currently open (`allow_origins=["*"]`)** — do not lock it down without confirming the frontend origin

---

## 7. How New Features Should Be Integrated

### New Event Type
1. Add the event type string constant as a comment in `server.py` near the existing type checks
2. Add a new `elif` block in `handle_disruption()` in `server.py`
3. Add the corresponding agent flow + prompt in `run_agent()` in `api_prod.py`
4. Add tests (when test infrastructure is established)

### New Tenant Onboarding
1. Get three API base URLs from the airline: `flight_api`, `cdp_api`, `disruption_api`
2. Add to `seed_tenants.py` and `FALLBACK_TENANTS`
3. Run `python seed_tenants.py`
4. Register Azure AI agents for the tenant and activate them in the `agents` Cosmos collection

### New CDP Persona
Personas are resolved inside the AI agent prompts (not in Python code). To add a new persona:
1. Update the messaging prompt in `run_agent()` to include the new persona detection rule
2. Add the persona to the recovery prompt's CDP priority section
3. Document in `docs/backend.md`

### New Admin Feature
The admin panel writes to Cosmos DB. This service reads from it. For new admin-configurable fields:
1. Add the field to the relevant Cosmos collection
2. Expose it via a new `fetch_*` function in `cosmos_data_fetcher.py`
3. Surface it in `get_runtime_config()` in `runtime_config.py`
4. Inject it in `run_agent()` or `handle_disruption()` as appropriate
