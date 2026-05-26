# AI Agent Rules — prodDisruption

This document is directed at AI coding agents (Claude, GitHub Copilot, etc.) working on this codebase. Read it before making any changes.

---

## 0. Highest Priority Rules

These override everything else:

1. **Never hardcode agent IDs.** They come from Cosmos DB via `get_runtime_config()`. The `api_main.py` file shows the old (wrong) pattern — do not replicate it.

2. **Never bypass the MCP boundary.** `api_prod.py` talks to `server.py` via JSON-RPC. Do not import `server.py` functions directly into `api_prod.py`.

3. **Never create a new Cosmos DB access file.** All DB reads go through `config/cosmos_data_fetcher.py`.

4. **Never write synchronous HTTP calls in service clients.** They are async (`httpx`). Do not add `requests.get()` calls to `airline_client.py`, `cdp_client.py`, or `disruption_client.py`.

5. **Every `handle_disruption` return must use the content wrapper.** The parser in `execute_mcp_tool()` depends on the exact `{"content": [{"type": "json", "json": {...}}]}` format.

---

## 1. Implementation Patterns to Preserve

### Pattern: Tenant-Aware API Calls
```python
# CORRECT — always resolve tenant config first
config = get_tenant_config(tenant_id)
base_url = config["flight_api"]
response = await client.post(f"{base_url}/flight-search", ...)

# WRONG — never hardcode base URLs in service functions
response = await client.post("https://hardcoded-url/flight-search", ...)
```

### Pattern: MCP Return Wrapper
```python
# CORRECT
return {
    "content": [{
        "type": "json",
        "json": {
            "final": True,
            "status": "success",
            "flow": "recovery",
            ...
        }
    }]
}

# WRONG — returning bare dict
return {"status": "success", "flow": "recovery", ...}
```

### Pattern: Async Parallel I/O in MCP Server
```python
# CORRECT — parallel
profile, flights = await asyncio.gather(
    find_users(tenant_id, last_name, email_or_phone),
    search_flights(tenant_id, seg_key)
)

# WRONG — sequential when parallel is possible
profile = await find_users(...)
flights = await search_flights(...)
```

### Pattern: Agent JSON Extraction
```python
# CORRECT — always use safe_json_from_agent()
raw = msg.text_messages[0].text.value
return safe_json_from_agent(raw)

# WRONG — direct json.loads() without handling fences
return json.loads(msg.text_messages[0].text.value)
```

### Pattern: Cosmos DB Access
```python
# CORRECT — use _find_one() for single documents
doc = _find_one("settings", {"id": "api_settings"}, {"_id": 0})

# CORRECT — use _get_db() for multi-doc queries, always close client
client, db = _get_db()
try:
    results = list(db["agents"].find({...}, {...}))
finally:
    client.close()

# WRONG — never create MongoClient directly outside cosmos_data_fetcher.py
from pymongo import MongoClient
client = MongoClient(...)  # anywhere else in the codebase
```

---

## 2. Architectural Constraints

| Constraint | Reason |
|---|---|
| MCP server = port 8004 | FastAPI hardcodes `MCP_URL = "http://localhost:8004/mcp"` |
| MCP transport = `streamable-http` | The SSE parser in `execute_mcp_tool()` expects this |
| Agent temperature = 0.1 | Ensures deterministic, rule-following responses |
| Agent timeout = 120s | Matches Azure AI Foundry agent SLA expectations |
| Cosmos DB connection per operation | Acceptable for low-frequency config reads; must not be changed to persistent connection without testing |
| `allow_origins=["*"]` | Temporary; will break if locked down without knowing the frontend origin |

---

## 3. Naming Conventions (Summary)

- Fetch functions (DB): `fetch_*` in `cosmos_data_fetcher.py`
- Config resolvers: `get_*` in `runtime_config.py` / `tenant_config.py`
- External API calls: async functions in `services/`
- Error reason codes: `UPPER_SNAKE_CASE` strings
- Tenant IDs: `lowercase_underscore` (e.g., `indigo_mock`)
- Agent flows: lowercase strings `"recovery"` | `"messaging"`
- Prompt variables in f-strings: embed data as `json.dumps(data, indent=2)`

---

## 4. File Organization Rules

### Adding a new feature: where does it go?

| What you're adding | Where it goes |
|---|---|
| New REST endpoint | `api_prod.py` |
| New MCP tool | `server.py` |
| New Cosmos DB query | `config/cosmos_data_fetcher.py` |
| New external API service | `services/{name}_client.py` |
| New data transformation | `tools/{name}.py` |
| New tenant | `seed_tenants.py` + `tenant/tenant_config.py FALLBACK_TENANTS` |
| New env var | `config/settings.py` + update `docs/deployment.md` |
| New agent prompt | Inside `run_agent()` in `api_prod.py` |

### What goes in `__all__` of `cosmos_data_fetcher.py`
Every public function that is imported by other modules. Private helpers (`_get_db`, `_find_one`, `_find_many`) use the underscore prefix and are excluded.

---

## 5. Safe Refactoring Guidelines

### Safe to change without downstream impact:
- Internal logic of `normalize_event()` — provided the output keys remain the same
- Internal logic of `safe_json_from_agent()` — provided it still returns a parsed dict
- Adding new log statements
- Adding new helper functions within existing files
- Docstrings and comments

### Requires checking all callers:
- Any change to MCP response shape (dict keys inside `json`) — `execute_mcp_tool()` parser and `api_prod.py` both depend on it
- Changing `get_runtime_config()` return shape — `run_agent()` calls `.get("recovery_agent_id")` etc.
- Changing `get_tenant_config()` return shape — all three service clients call it
- Changing `fetch_active_template()` return shape — `message_fetcher.py` depends on it
- Changing the `find_users()` signature — `server.py`, `validator.py` call it

### Requires deliberate migration plan:
- Changing the MCP transport protocol
- Changing the FastMCP version (may change the SSE format)
- Changing from per-operation to pooled Cosmos DB connections
- Changing from `asyncio` to thread pool concurrency in `server.py`
- Changing `temperature`/`top_p` on agent runs (affects output quality)

---

## 6. Files/Modules Requiring Extra Caution

| File | Risk | Notes |
|---|---|---|
| `api_prod.py → execute_mcp_tool()` | SSE parsing logic is fragile | Any change to MCP transport format will break this |
| `api_prod.py → safe_json_from_agent()` | Core output parser | Must handle all edge cases of Azure AI Agent output |
| `api_prod.py → run_agent()` | Prompts are business logic | Changes to scoring rules affect recovery quality |
| `config/cosmos_data_fetcher.py` | All DB access | Wrong query can return wrong agent/prompt |
| `config/runtime_config.py` | Agent ID resolution | If this returns `None`, the agent run will fail |
| `server.py → handle_disruption()` | Main orchestration | Any change here affects both flows |
| `tools/validator.py` | Has a known bug | Do not add more callers until the `tenant_id` bug is fixed |

---

## 7. How to Extend APIs

### Adding a new REST endpoint to `api_prod.py`:

```python
class NewRequest(BaseModel):
    required_field: str
    optional_field: str | None = None

@app.post("/new-endpoint")
def handle_new_endpoint(req: NewRequest):
    try:
        # ... implementation
        return {"status": "success", "result": ...}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
```

Rules:
- Always define a Pydantic model
- Always wrap in try/except
- Always raise `HTTPException(status_code=500, detail=str(e))`
- Document in `docs/api.md`

### Adding a new MCP tool to `server.py`:

```python
@mcp.tool()
async def new_tool(tenant_id: str, param: str):
    # ... implementation
    return {
        "content": [{
            "type": "json",
            "json": {
                "final": True,
                "status": "success",
                ...
            }
        }]
    }
```

Rules:
- Must be `async`
- Must return the content wrapper
- Must handle all error cases and return error dicts (never raise to top level)
- Document the tool in `docs/api.md`

### Adding a new flow type:

1. Add `elif event["event_type"] == "new_type":` block in `server.py → handle_disruption()`
2. Add `elif flow == "new_flow":` block in `api_prod.py → run_agent()`
3. Write the agent prompt for the new flow
4. Return `"flow": "new_flow"` in the MCP response
5. Document in `docs/backend.md` and `docs/changelog.md`

---

## 8. How to Add Frontend Features

This repository contains no frontend code. The frontend connects to `api_prod.py` REST endpoints. To support a new frontend feature:

1. Define the new REST endpoint in `api_prod.py`
2. Document the request/response schema in `docs/api.md`
3. If the feature requires new data from Cosmos DB, add the fetch function to `cosmos_data_fetcher.py`
4. If the feature requires a new agent behaviour, add/modify the prompt in `run_agent()`

---

## 9. Backward Compatibility

### API Response Compatibility
- Do not remove fields from existing response shapes
- Adding new optional fields to responses is safe
- Changing the type of an existing field (e.g., `flow` from `str` to `list`) is breaking

### Tenant Compatibility
- `FALLBACK_TENANTS` exists for backward compatibility during Cosmos DB migrations
- Do not remove existing tenant entries from `FALLBACK_TENANTS` without confirming they exist in Cosmos DB

### Prompt Compatibility
- The `system_prompt` and `text` admin fields in `AdminConfig` are used by the admin panel
- Do not rename these fields without updating the admin panel

### Agent ID Compatibility
- Agent IDs (`asst_xxxxx`) come from Azure AI Foundry and must match registered agents
- Old agent IDs in `api_main.py` (`asst_0ywRqan9UlWxRSM3bcgpJmDh`, `asst_BiR67fIqqynuIHTI3VBTM7rr`) are the original development agents — they may still be valid

---

## 10. Change Tracking Obligation

After any non-trivial change, append to `docs/changelog.md`:

```markdown
## [YYYY-MM-DD] — Short title

**Type:** feature | bugfix | refactor | dependency | api-change | migration | deployment

**Files changed:** list files

**Summary:** What changed and why.

**Breaking changes:** yes/no.
```

Also update the relevant `docs/*.md` file for the affected subsystem.
