# Backend — prodDisruption

## Module Reference

This document covers every Python module in the project, its purpose, key functions, dependencies, and known edge cases.

---

## `api_prod.py` — FastAPI Server (Production)

**Purpose:** The public-facing REST API. Receives disruption requests, calls the MCP server to gather data, runs the Azure AI agent, and returns the result.

**Runs on:** FastAPI + Uvicorn (port configured at startup)

### Key Functions

#### `safe_json_from_agent(text: str) -> dict`
Extracts valid JSON from agent output that may be wrapped in markdown code fences (` ```json ... ``` `). Steps:
1. Strip whitespace
2. If text starts with ` ``` `, split on ` ``` ` and take the second part, strip `json` prefix
3. Find first `{` and last `}`, extract substring
4. Parse with `json.loads()`

**Why it exists:** Azure AI Agents occasionally return JSON wrapped in markdown fences. This function makes the parser robust to that.

**Edge cases:**
- Empty response → raises `ValueError("Agent returned empty response")`
- No `{` or `}` found → raises `ValueError` with the raw text for debugging
- Malformed JSON (valid braces, invalid syntax) → `json.JSONDecodeError` propagates up

#### `execute_mcp_tool(tool_name: str, arguments: dict) -> dict`
Calls the MCP server via JSON-RPC 2.0 over HTTP (Server-Sent Events response).

**Important:** Response parsing has two fallback levels:
1. `result.structuredContent.content[].type == "json"` → return `item["json"]`
2. `result.content[].text` → parse as JSON → look for `content[].type == "json"` → return

If neither yields a JSON item, raises `RuntimeError("No valid MCP JSON found")`.

**Hardcoded:** `MCP_URL = "http://localhost:8004/mcp"` — assumes MCP server is on the same host.

#### `run_agent(flow: str, tenant_id: str, mcp_data: dict) -> dict`
The core Azure AI agent orchestration function.

1. Calls `get_runtime_config(tenant_id)` to get active agent IDs and admin prompts
2. Routes to `recovery_agent_id` or `message_agent_id` based on `flow`
3. Builds the prompt (embedded with MCP data as JSON)
4. Optionally prepends `admin_system` and appends `admin_user` to the prompt
5. Creates a new thread, posts the user message, starts a run
6. Polls every 1 second up to 120 seconds
7. Extracts and parses the last assistant message

**Admin prompt injection** (api_prod.py v1.0.4):
```python
admin_system = runtime.get("system_prompt", "")
admin_user = runtime.get("text", "")
if admin_system:
    prompt = f"{admin_system}\n\n{prompt}"
if admin_user:
    prompt = f"{prompt}\n\nADMIN INSTRUCTIONS:\n{admin_user}"
```
Note: `api_main.py` used `runtime.get("prompt_append")` instead — different key names. `api_prod.py` is the authoritative version.

#### `build_event_payload(mcp_data: dict) -> dict`
Extracts a clean event summary from MCP data for inclusion in the final API response. Reads from `mcp_data["event"]["original_flight"]`.

### Pydantic Models

```python
class DisruptionRequest(BaseModel):
    pnr: str
    last_name: str
    tenant_id: str

class AdminConfig(BaseModel):
    tenant_id: str
    recovery_agent_id: str | None = None
    message_agent_id: str | None = None
    system_prompt: str | None = None
    text: str | None = None
```

### Azure Authentication
```python
try:
    credential = DefaultAzureCredential()
    credential.get_token("https://management.azure.com/.default")
except Exception:
    credential = AzureCliCredential()
```
`DefaultAzureCredential` is tried first (works in Azure-hosted environments with managed identity). Falls back to `AzureCliCredential` for local development (requires `az login`).

**Important:** The credential is instantiated **at module load time**, not per request. This is correct — Azure SDK credential objects are thread-safe and intended to be reused.

### Agent Run Parameters
- `temperature=0.1` — near-deterministic output
- `top_p=0.1` — further reduces output variability
- Timeout: 120 seconds

These are deliberately low to ensure consistent, rule-following agent output.

---

## `server.py` — MCP Server (Current)

**Purpose:** Data orchestration layer. Fetches disruption event, validates the passenger, retrieves CDP profile, searches alternate flights, fetches seat maps, and returns structured data to `api_prod.py`.

**Runs on:** FastMCP with `streamable-http` transport, port 8004, path `/mcp`, stateless.

**Key distinction from old versions:** Uses `asyncio.gather()` for true parallel I/O (not thread pool).

### `normalize_event(raw: dict) -> dict`
Transforms the raw disruption API response into a structured event dict.

```python
{
    "event_type": "flight_cancelled" | "flight_delayed",
    "pnr": "...",
    "last_name": "...",           # from raw["user_info"]["USR_LASTNAME"]
    "segKey": "...",              # used for flight search
    "original_flight": raw,       # full raw response preserved
    "passenger": {
        "email": "...",
        "mobile": "..."
    },
    # if cancelled:
    "cancellation": { "timestamp", "reason", "source" },
    # if delayed:
    "delay": { "delay_count", "delay_duration_minutes", "delay_reason" }
}
```

**Note:** `original_flight: raw` stores the entire raw response. This is intentional — the agent needs full flight details including `cabin_class`, `fare_class`, UTC times, etc.

**Important discrepancy:** `tools/event_normalizer.py` defines a different `normalize_event()` that uses a `flight` key (not `original_flight`) and a different field mapping. That function is **not imported or used** by `server.py`. It is dead code/technical debt.

### `handle_disruption(tenant_id, pnr, last_name)` — The MCP Tool

**Validation chain:**
1. All three parameters required → returns error if missing
2. `fetch_event_by_pnr()` → returns error if not found
3. Last name case-insensitive match → returns error if mismatch

**Cancellation branch:**
1. `validate_request()` for auto-recovery eligibility
   - Must be HIGHSPENDER (high/low freq) OR STUDENT
   - Returns `ineligible` if not eligible
2. Check `segKey` exists on the event
3. `asyncio.gather(find_users(), search_flights())`
4. If no flights returned → return success with empty recovery arrays (agent handles this)
5. `asyncio.gather(*[get_seat_map(f["segKey"]) for f in flights])` — parallel
6. Seat map failures are logged but non-fatal (`return_exceptions=True`)

**Delay branch:**
1. `find_users()` (sequential — no concurrency needed for single call)
2. `fetch_all_messages()` — synchronous, reads from Cosmos DB

**Known limitation:** `validate_request()` in `tools/validator.py` calls `find_users(last_name, email_or_phone)` without `tenant_id`. The `cdp_client.find_users()` signature requires `(tenant_id, last_name, email_or_phone)`. This is a bug that will cause a `TypeError` at runtime when the cancellation flow is triggered.

---

## `config/cosmos_data_fetcher.py` — Cosmos DB Access Layer

**Purpose:** Single module for all Cosmos DB reads. Uses PyMongo against the Azure Cosmos DB MongoDB API endpoint.

### Connection Pattern
A new `MongoClient` is created and closed per operation (not pooled). This is deliberate for a stateless service. The `serverSelectionTimeoutMS=5000` prevents long hangs on connectivity issues.

```python
def _get_db():
    client = MongoClient(COSMOS_DB_URI, serverSelectionTimeoutMS=5000)
    return client, client[COSMOS_DB_NAME]
```

**URI format:** `mongodb+srv://{user}:{password}@{host}/?retryWrites=true&w=majority`

Password is URL-encoded via `urllib.parse.quote_plus`.

### Key Functions

| Function | Collection | Query | Returns |
|---|---|---|---|
| `fetch_api_settings()` | `settings` | `{"id": "api_settings"}` | dict |
| `fetch_active_prompt_payload()` | `prompts` | `{"is_active": True}` | `{name, is_active, system_prompt, user_prompt}` |
| `fetch_active_prompt()` | `prompts` | `{"is_active": True}` | `str` (user_prompt only) |
| `fetch_active_agents()` | `agents` | `{is_active_recovery: true} OR {is_active_messaging: true}` | `list[dict]` |
| `fetch_active_template()` | `templates` | `{"is_active": True}` → fallback to `{}` | dict |
| `fetch_tenant_config(tenant_id)` | `tenants` | `{"tenant_id": tenant_id}` | dict |
| `fetch_recovery_prompt()` | `prompts` | `{"type": "recovery", "is_active": True}` | str (system_prompt) |
| `fetch_messaging_prompt()` | `prompts` | `{"type": "messaging", "is_active": True}` | str (user_prompt) |

**Note:** `fetch_recovery_prompt()` and `fetch_messaging_prompt()` are defined but **not in `__all__`** and not currently called anywhere in the active codebase. They appear to be prepared for a future Cosmos DB-driven prompt system. Do not remove — add to `__all__` when used.

### Shared Utilities for Flight Data
These functions (`extract_flights`, `collect_seatmaps`, `load_flights`, `load_seatmaps`) exist to support potential batch/admin use cases. They are **not called in the main request flow** — the live flow fetches data from external APIs, not from Cosmos DB flight collections.

`normalize_key()` strips and uppercases strings for consistent key comparison.

`recompute_seatmap_availability()` recounts available seats by traversing the deck → compartment → units tree.

---

## `config/runtime_config.py` — Runtime Configuration

**Purpose:** Provides the current active agent IDs and prompts for a given tenant. Reads live from Cosmos DB on each request (no caching).

### `get_runtime_config(tenant_id: str) -> dict`
Returns:
```python
{
    "recovery_agent_id": str | None,
    "message_agent_id": str | None,
    "prompt_append": "",           # legacy field, always ""
    "addon_system_prompt": str,    # from active prompt's system_prompt
    "addon_user_prompt": str       # from active prompt's user_prompt
}
```

**Note:** The `tenant_id` parameter is accepted but **not used** in the current implementation. All tenants share the same active agents and prompts from Cosmos DB. Per-tenant agent routing is a planned improvement.

### `update_runtime_config(tenant_id: str, data: dict)`
Intentionally raises `Exception("Runtime config must be updated through the admin panel / Cosmos DB")`.

The old in-memory version (commented out at the top of the file) used a global `RUNTIME_CONFIG` dict — do not restore this pattern.

---

## `config/settings.py` — Environment Configuration

Loads external API URLs and keys from environment variables, with hardcoded mock API defaults:

| Variable | Default | Description |
|---|---|---|
| `AIRLINE_API_BASE_URL` | mock Azure URL | Airline flight search base URL |
| `CDP_API_BASE_URL` | mock Azure URL | Customer Data Platform base URL |
| `AIRLINE_API_KEY` | `"test-key"` | Airline API authentication key |
| `CDP_API_KEY` | `"cdp-test-key"` | CDP API authentication key |
| `TIMEOUT` | `30` | HTTP request timeout (seconds) |
| `DISRUPTION_API_BASE_URL` | mock Azure URL | Disruption events API base URL |
| `DISRUPTION_API_KEY` | `"disruption-test-key"` | Disruption API key |

**Important:** These defaults point to real mock services hosted in Azure Container Apps (South India). They will work out of the box for development without a `.env` file, but are not production credentials.

---

## `tenant/tenant_config.py` — Tenant Resolution

**Purpose:** Resolves the three API base URLs (flight, CDP, disruption) for a given `tenant_id`.

### `get_tenant_config(tenant_id: str) -> dict`
Resolution order:
1. Cosmos DB `tenants` collection → `fetch_tenant_config(tenant_id)`
2. Hardcoded `FALLBACK_TENANTS` dict
3. Raises `Exception(f"Unknown tenant: {tenant_id}")`

Returns:
```python
{
    "flight_api": "https://...",
    "cdp_api": "https://...",
    "disruption_api": "https://..."
}
```

**Hardcoded fallback tenants:**
- `indigo_mock` — IndiGo mock (Azure Container Apps, South India)
- `airline_mock` — Generic airline mock (same URLs as indigo_mock)

---

## `services/http_client.py` — Shared HTTP Client

A module-level singleton `httpx.AsyncClient`:
```python
client = httpx.AsyncClient(
    timeout=httpx.Timeout(TIMEOUT),   # 30s default
    limits=httpx.Limits(
        max_connections=100,
        max_keepalive_connections=20
    )
)
```

**All async service clients import this single instance.** This is important — creating a new `AsyncClient` per request would be wasteful and could exhaust file descriptors under load.

---

## `services/airline_client.py` — Airline API Client

### `search_flights(tenant_id: str, seg_key: str) -> list`
```
POST {flight_api}/flight-search
Body: {"segKey": seg_key}
Returns: data["flights"] (list of flight dicts)
```

Flight dicts contain: `segKey`, `flight_number`, `origin`, `destination`, `utcDeparture`, `utcArrival`, `isStretch`, `fillingFast`, `NonStop`, `min_economy_fare`, `min_business_fare`.

### `get_seat_map(tenant_id: str, seg_key: str) -> list`
```
GET {flight_api}/seat-map/{seg_key}
Returns: flattened list of assignable seat dicts
```

Seat parsing traverses: `data.seatMaps[0].seatMap.decks.{deck}.compartments.{compartment}.units[]`

Returns per seat:
```python
{
    "seat_number": str,        # designator (e.g., "12A")
    "travel_class": str,       # "Y" (economy) or "C" (business)
    "availability": str,
    "seat_type": list[str]     # property codes: "LEGROOM", "XL", "AISLE", "WINDOW"
}
```

Only `assignable == True` seats are included.

**Important:** The `get_seat_map()` function indexes `seat_maps[0]` — if a flight has multiple seat maps, only the first is used. This is a known limitation.

---

## `services/cdp_client.py` — CDP API Client

### `find_users(tenant_id: str, last_name: str, email_or_phone: str) -> list`
```
POST {cdp_api}/cdp/user-lookup
Headers: Authorization: Bearer {CDP_API_KEY}
Body: {"last_name": last_name, "email_or_phone": email_or_phone}
Returns: data["users"]
```

**Note:** Uses the global `CDP_API_KEY` from `config/settings.py`, not a tenant-specific key. This means all tenants share the same CDP API key, which may not be appropriate for production multi-tenancy.

The `cdp_api` base URL is resolved per tenant via `get_tenant_config()`.

---

## `services/disruption_client.py` — Disruption API Client

### `fetch_event_by_pnr(tenant_id: str, pnr: str) -> dict | None`
```
GET {disruption_api}/disruptions/{pnr}
Headers: Authorization: Bearer {DISRUPTION_API_KEY}
Returns: data["event"] or None (on 404)
```

Returns `None` on 404 — the MCP server treats this as `EVENT_NOT_FOUND`. All other errors propagate as exceptions.

---

## `tools/validator.py` — Auto-Recovery Eligibility

### `validate_request(last_name: str, email_or_phone: str) -> dict`
Wraps `check_user_autorecovery_eligibility()`.

**KNOWN BUG:** `check_user_autorecovery_eligibility()` calls `find_users(last_name, email_or_phone)` (2 args), but `cdp_client.find_users()` requires `(tenant_id, last_name, email_or_phone)` (3 args). This will raise a `TypeError` at runtime during the cancellation flow.

**Fix required:** Update `validate_request()` to accept `tenant_id` and pass it through.

**Eligibility logic:**
- HIGHSPENDERHIGHFREQ OR HIGHSPENDERLOWFREQ → eligible
- STUDENT > 0 → eligible
- Otherwise → ineligible

**Normalization helpers:**
- `normalize_bool(v)` — handles bool, int (1=True), str ("true"/"1"/"yes")
- `normalize_student(v)` — handles bool, int (>0 = True), numeric str

---

## `tools/message_fetcher.py` — Message Template Loader

### `fetch_all_messages() -> dict`
Reads from Cosmos DB `templates` collection (active template). The template stores a JSON string in `content.raw_content` field.

Template JSON structure:
```json
{
  "MESSAGES": [
    {
      "group_id": "MSG-0001",
      "delay_count": 1,
      "channels": [
        { "id": "MSG-0001-SMS", "channel": "sms", "message": "..." },
        { "id": "MSG-0001-WA", "channel": "whatsapp", "message": "..." },
        { "id": "MSG-0001-EMAIL", "channel": "email", "message": "..." }
      ]
    }
  ]
}
```

Returns:
```python
{
    "total_messages": int,
    "messages": [
        {"group_id": "MSG-0001", "delay_count": 1, "id": "...", "channel": "sms", "message": "..."},
        ...
    ]
}
```

Messages contain `{var1}` and `{var2}` placeholders that the AI agent replaces with origin and destination.

---

## `seed_tenants.py` — Database Seeder

One-shot script to upsert tenant records into the Cosmos DB `tenants` collection.

```bash
python seed_tenants.py
```

Uses `upsert=True` — safe to run multiple times. Prints confirmation and current collection state.

---

## Historical Files (Do Not Use)

| File | Status | Notes |
|---|---|---|
| `server_old.py` | Superseded | Synchronous `handle_disruption`, sequential seat maps, no asyncio |
| `server_old1.0.py` | Superseded | Thread pool (`ThreadPoolExecutor`) parallel version |
| `api_main.py` | Superseded | Hardcoded agent IDs, no admin panel, `prompt_append` key |
| `tools/event_normalizer.py` | Unused | Different schema (`flight` key), not imported by `server.py` |

These files should be deleted in a future cleanup pass. They are kept in version history but provide no operational value.
