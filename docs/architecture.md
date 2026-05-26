# Architecture — prodDisruption

## System Overview

prodDisruption is a **two-process, multi-tenant AI pipeline** that handles airline flight disruptions using Azure AI Agents. The two processes are:

| Process | File | Port | Protocol | Role |
|---|---|---|---|---|
| MCP Server | `server.py` | 8004 | Streamable HTTP (JSON-RPC) | Data orchestration |
| FastAPI Server | `api_prod.py` | configurable | HTTP/REST | AI agent execution + REST API |

These two processes must both be running for the system to work. They communicate over localhost.

---

## High-Level Data Flow

```
Frontend / Client
       │
       │  POST /disruption {pnr, last_name, tenant_id}
       ▼
┌─────────────────────────────────────────────────┐
│            api_prod.py  (FastAPI)               │
│                                                 │
│  1. execute_mcp_tool("handle_disruption", ...)  │
│  2. run_agent(flow, tenant_id, mcp_data)        │
│  3. Return structured JSON                       │
└─────────────┬───────────────────────────────────┘
              │  JSON-RPC POST http://localhost:8004/mcp
              ▼
┌─────────────────────────────────────────────────┐
│             server.py  (FastMCP)                │
│                                                 │
│  handle_disruption(tenant_id, pnr, last_name)  │
│                                                 │
│  ┌────────────────────────────────────────────┐│
│  │  asyncio.gather():                         ││
│  │   • CDP profile lookup (cdp_client.py)     ││
│  │   • Flight search (airline_client.py)      ││
│  │   • Seat maps per flight (parallel)        ││
│  └────────────────────────────────────────────┘│
│                                                 │
│  Returns: flow + event + profile + recovery     │
│           OR flow + event + profile + messages  │
└─────────────────────────────────────────────────┘
              │
              │  Async HTTP (httpx)
              ▼
┌──────────────────┐  ┌──────────────────┐  ┌───────────────────┐
│  Disruption API  │  │   Airline API    │  │    CDP API        │
│  /disruptions/   │  │  /flight-search  │  │ /cdp/user-lookup  │
│  {pnr}           │  │  /seat-map/{key} │  │                   │
└──────────────────┘  └──────────────────┘  └───────────────────┘
              │
              │  Read-only
              ▼
┌─────────────────────────────────────────────────┐
│           Azure Cosmos DB                       │
│           (MongoDB API)                         │
│           Database: flight_operations           │
│                                                 │
│  Collections:                                   │
│   • tenants      — API URLs per tenant          │
│   • agents       — active Azure AI agent IDs    │
│   • prompts      — active system/user prompts   │
│   • templates    — message templates            │
│   • settings     — API settings                 │
│   • flight_data  — (legacy, used by cosmos_     │
│   • available_   │   data_fetcher utilities)    │
│     seats        │                              │
└─────────────────────────────────────────────────┘
              │
              │  Azure AI Agents SDK
              ▼
┌─────────────────────────────────────────────────┐
│           Azure AI Foundry                      │
│  Endpoint: marketplace-aifoundry.services.      │
│            ai.azure.com/api/projects/proj-default│
│                                                 │
│  Agents:                                        │
│   • Recovery Agent   (selects flight + seat)    │
│   • Messaging Agent  (selects message group)    │
└─────────────────────────────────────────────────┘
```

---

## Two Disruption Flows

### Flow 1: Recovery (Cancelled Flights)

Triggered when `event_type == "flight_cancelled"`.

```
1.  Fetch disruption event by PNR           → disruption_client.py
2.  Normalize event (extract segKey, etc.)  → server.py inline normalize_event()
3.  Verify last_name matches event          → server.py
4.  Check auto-recovery eligibility        → tools/validator.py → cdp_client.py
    (must be HIGHSPENDER or STUDENT)
5.  Parallel:
    a. CDP profile lookup                   → cdp_client.py
    b. Flight search by segKey              → airline_client.py
6.  Parallel: seat map per available flight → airline_client.py
7.  Return to api_prod.py:
    { flow: "recovery", profile, event,
      recovery: { available_flights, available_seats } }
8.  Run Azure AI Recovery Agent             → api_prod.py run_agent()
    (selects best flight + seat using CDP scoring rules)
9.  Return final response to client
```

**Output shape:**
```json
{
  "status": "success",
  "flow": "recovery",
  "event": { "event_type": "flight_cancelled", "flight_number": "...", ... },
  "result": {
    "cdp_summary": { "student": 0, "highspender": true, "business": 2, "leisure": 1 },
    "selected_flight": { ... },
    "selected_seat": { ... },
    "reasoning": { "flight_reason": "...", "seat_reason": "..." }
  }
}
```

### Flow 2: Messaging (Delayed Flights)

Triggered when `event_type == "flight_delayed"`.

```
1.  Fetch disruption event by PNR           → disruption_client.py
2.  Normalize event                         → server.py
3.  Verify last_name                        → server.py
4.  CDP profile lookup (sequential)         → cdp_client.py
5.  Fetch all message templates             → tools/message_fetcher.py → Cosmos DB
6.  Return to api_prod.py:
    { flow: "messaging", profile, event, messages }
7.  Run Azure AI Messaging Agent            → api_prod.py run_agent()
    (selects best message group + personalizes based on CDP persona)
8.  Return final response to client
```

**Output shape:**
```json
{
  "status": "success",
  "flow": "messaging",
  "event": { "event_type": "flight_delayed", "flight_number": "...", ... },
  "result": {
    "selected_group_id": "MSG-0001",
    "reason": "...",
    "messages": [
      { "id": "MSG-0001-SMS", "channel": "sms", "message": "..." },
      { "id": "MSG-0001-WA", "channel": "whatsapp", "message": "..." },
      { "id": "MSG-0001-EMAIL", "channel": "email", "message": "..." }
    ]
  }
}
```

---

## Multi-Tenancy Model

Each request carries a `tenant_id`. The system resolves tenant-specific API endpoints at call time:

```
tenant_id  →  get_tenant_config(tenant_id)
           →  Cosmos DB "tenants" collection (primary)
           →  FALLBACK_TENANTS dict (fallback, development only)
           →  { flight_api, cdp_api, disruption_api }
```

Tenant config is per-request resolution — there is no tenant state held in memory.

**Active tenants:**
- `indigo_mock` — IndiGo mock environment
- `airline_mock` — Generic airline mock environment

Both currently point to the same mock API base URLs in Azure Container Apps (South India region).

---

## Azure AI Agent Lifecycle

For each disruption request:

1. `AIProjectClient` is instantiated with `PROJECT_ENDPOINT` and Azure credentials
2. A new thread is created (`client.agents.threads.create()`)
3. The prompt (with all MCP data embedded as JSON) is posted as a user message
4. An agent run is started with `temperature=0.1, top_p=0.1` (near-deterministic)
5. The system polls every 1 second for `RunStatus.COMPLETED` (timeout: 120s)
6. The last assistant message is extracted and parsed with `safe_json_from_agent()`
7. The client context is exited (thread is not reused between requests)

Agent IDs are **dynamically resolved** from Cosmos DB `agents` collection at request time via `config/runtime_config.py → get_runtime_config()`. This means the admin panel can hot-swap agents without restarting the service.

---

## MCP Communication Protocol

`api_prod.py → execute_mcp_tool()` sends a JSON-RPC 2.0 request to the MCP server and parses the **Server-Sent Events (SSE) response stream**:

```json
// Request
{
  "jsonrpc": "2.0",
  "id": "ui-call",
  "method": "tools/call",
  "params": {
    "name": "handle_disruption",
    "arguments": { "pnr": "...", "last_name": "...", "tenant_id": "..." }
  }
}
```

The response is a stream of `data:` lines. The parser looks for:
1. `result.structuredContent.content[].type == "json"` (preferred)
2. `result.content[].text` → parsed JSON → `content[].type == "json"` (fallback)

---

## Concurrency Model

| Layer | Concurrency Strategy |
|---|---|
| MCP server (`server.py`) | `asyncio.gather()` — native async |
| Service clients | `httpx.AsyncClient` singleton (shared) |
| FastAPI server | Synchronous (uses `requests` for MCP call) |
| Azure Agent polling | Blocking sleep loop (synchronous) |

The shared `httpx.AsyncClient` in `services/http_client.py` has:
- `max_connections=100`
- `max_keepalive_connections=20`
- Timeout from `config/settings.py → TIMEOUT` (default: 30s)

---

## Configuration Layers

There are three layers of configuration, applied in this priority order (highest wins):

1. **Runtime admin config** — injected from Cosmos DB `agents` and `prompts` collections at request time via `get_runtime_config()`
2. **Base prompts** — hardcoded inside `run_agent()` in `api_prod.py`
3. **Environment variables** — loaded via `config/settings.py` from `.env`

Admin system prompts are **prepended** to the base prompt. Admin user text is **appended** at the end.

---

## File Dependency Graph

```
api_prod.py
  ├── config/runtime_config.py
  │     └── config/cosmos_data_fetcher.py
  └── (Azure AI Agents SDK)

server.py
  ├── services/disruption_client.py
  │     ├── tenant/tenant_config.py
  │     │     └── config/cosmos_data_fetcher.py
  │     └── services/http_client.py
  ├── services/cdp_client.py
  │     ├── config/settings.py
  │     ├── tenant/tenant_config.py
  │     └── services/http_client.py
  ├── services/airline_client.py
  │     ├── tenant/tenant_config.py
  │     └── services/http_client.py
  ├── tools/validator.py          ⚠ BUG: calls find_users without tenant_id
  │     └── services/cdp_client.py
  └── tools/message_fetcher.py
        └── config/cosmos_data_fetcher.py

config/cosmos_data_fetcher.py
  └── (pymongo → Azure Cosmos DB)

services/http_client.py
  └── (httpx)
```
