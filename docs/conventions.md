# Conventions — prodDisruption

## Language and Runtime

- **Python 3.11+** is required
- Use `str | None` not `Optional[str]` (PEP 604 union syntax)
- Use `list[dict]` not `List[Dict]` (built-in generics, PEP 585)
- All service clients (`airline_client.py`, `cdp_client.py`, `disruption_client.py`) must be `async`
- All MCP server code (`server.py`) must be `async` where I/O is involved

---

## File Organization

```
prodDisruption/
├── api_prod.py           # FastAPI server — REST endpoints + agent orchestration
├── server.py             # MCP server — data orchestration
├── config/
│   ├── __init__.py
│   ├── cosmos_data_fetcher.py  # ALL Cosmos DB access
│   ├── runtime_config.py       # Runtime config resolution
│   └── settings.py             # Environment variable loading
├── tenant/
│   └── tenant_config.py        # Tenant API URL resolution
├── services/
│   ├── http_client.py          # Shared httpx.AsyncClient
│   ├── airline_client.py       # Airline API (flights + seats)
│   ├── cdp_client.py           # Customer Data Platform API
│   └── disruption_client.py    # Disruption event API
├── tools/
│   ├── validator.py            # Auto-recovery eligibility check
│   └── message_fetcher.py      # Message template loader
├── seed_tenants.py             # One-shot DB seeder
├── requirements.txt
├── .env                        # (gitignored)
└── docs/                       # Documentation (this directory)
```

**Rules:**
- New Cosmos DB queries go in `config/cosmos_data_fetcher.py` — nowhere else
- New external HTTP service integrations go in `services/` as a new `*_client.py` file
- New utility functions that operate on data (not I/O) go in `tools/`
- New REST endpoints go in `api_prod.py`
- New MCP tools go in `server.py`

---

## Naming Conventions

### Functions
- `fetch_*()` — reads from Cosmos DB (in `cosmos_data_fetcher.py`)
- `get_*()` — resolves configuration or derives a value (not necessarily I/O)
- `find_*()` — queries an external API to search for records
- `search_*()` — queries an external API for search results
- `normalize_*()` — transforms raw data into a structured format
- `build_*()` — constructs a dict/payload from existing data
- `validate_*()` / `check_*()` — performs a validation and returns result dict
- `load_*()` — loads bulk data (usually for batch operations)
- `collect_*()` — aggregates data from multiple sources into a list
- `execute_*()` — performs an external call (MCP, HTTP)
- `run_*()` — orchestrates a multi-step workflow (agent execution)
- `handle_*()` — top-level event handler (MCP tool, API endpoint)
- `seed()` — one-shot database population script

### Variables
- `raw` — unprocessed API response
- `event` — normalized disruption event dict
- `profile` — CDP user profile list
- `flights` — list of available flight dicts
- `seats` / `all_seats` — list of available seat dicts
- `mcp_data` — the full context dict returned by the MCP tool
- `agent_output` — parsed JSON result from the Azure AI agent
- `runtime` — result of `get_runtime_config()`
- `tenant_config` — result of `get_tenant_config()`
- `seg_key` — flight segment identifier (comes from disruption event, used for flight search)
- `flight_uid` — unique flight identifier used in agent prompt (from `journeyKey` or `segKey`)

### Constants
- `UPPER_SNAKE_CASE` for module-level constants (`MCP_URL`, `PROJECT_ENDPOINT`, `FALLBACK_TENANTS`)
- `UPPER_SNAKE_CASE` for environment variable names in `.env`

### Cosmos DB Collections
- `snake_case` collection names: `flight_data`, `available_seats`, `settings`, `prompts`, `agents`, `templates`, `tenants`

### API Error Codes
- `UPPER_SNAKE_CASE` for reason codes in MCP error responses
- Current codes: `TENANT_PNR_LASTNAME_REQUIRED`, `EVENT_NOT_FOUND`, `LAST_NAME_MISMATCH`, `SEGKEY_NOT_FOUND`, `AIRLINE_API_FAILURE`, `NOT_ELIGIBLE_FOR_AUTORECOVERY`, `UNSUPPORTED_EVENT_TYPE`

---

## MCP Response Structure

All `handle_disruption` returns must use this wrapper:

```python
return {
    "content": [{
        "type": "json",
        "json": {
            "final": True,
            "status": "success" | "error" | "ineligible" | "ignored",
            # ... additional fields
        }
    }]
}
```

This is not negotiable — `execute_mcp_tool()` in `api_prod.py` parses this exact shape.

---

## Error Handling

### In `server.py` (MCP layer)
Return structured error dicts — never raise exceptions to the top level:
```python
return {
    "content": [{
        "type": "json",
        "json": {
            "final": True,
            "status": "error",
            "reason": "DESCRIPTIVE_REASON_CODE"
        }
    }]
}
```

Wrap code that calls external APIs in `try/except`:
```python
try:
    ...
except Exception as e:
    logger.exception("AIRLINE_API_ERROR")
    return { "content": [{"type": "json", "json": {"status": "error", "reason": "AIRLINE_API_FAILURE", "details": str(e)}}] }
```

### In `api_prod.py` (FastAPI layer)
Wrap the entire endpoint handler in `try/except Exception as e: raise HTTPException(status_code=500, detail=str(e))`.

### In `cosmos_data_fetcher.py`
All functions should catch exceptions, log them with `print()` (or migrate to `logger`), and return a safe default (`{}`, `[]`, `None`, `""`).

---

## Logging

Use `logger.info()` / `logger.error()` / `logger.exception()` in `server.py`:

```python
logger.info("TENANT=%s PNR=%s LAST_NAME=%s", tenant_id, pnr, last_name)
logger.info("FLIGHT_SEARCH_RESULT_COUNT=%d", len(flights))
logger.error("SEATMAP_FETCH_FAILED=%s ERROR=%s", flight_segkey, str(e))
logger.exception("AIRLINE_API_ERROR")  # includes stack trace
```

Do **not** use `print()` in `server.py` or service clients. `cosmos_data_fetcher.py` currently uses `print()` — migrate to `logger` when refactoring.

---

## Async Conventions

- Use `asyncio.gather()` for independent parallel I/O (not `ThreadPoolExecutor`)
- Use `return_exceptions=True` on `asyncio.gather()` when partial failure is acceptable (e.g., seat map fetch)
- The shared `httpx.AsyncClient` in `services/http_client.py` must not be replaced with `requests` in async contexts

---

## Agent Prompt Style

- Always lead prompts with `CRITICAL RULES` or `ABSOLUTE RULES` in `UPPER_CASE`
- Embed all data as `json.dumps(data, indent=2)` within the prompt
- Use `{{` and `}}` (escaped braces) for literal curly braces in f-strings
- Always end prompts with explicit `OUTPUT FORMAT` section showing exact JSON structure
- Always include explicit `FAIL IF:` conditions for invalid states

---

## Pydantic Models (FastAPI)

- Define all request models as `class Xxx(BaseModel)` near the top of `api_prod.py`
- Use `field_name: type | None = None` for optional fields
- No default values for required fields

---

## Dead Code Policy

Commented-out code blocks in service files (`airline_client.py`, `disruption_client.py`, `runtime_config.py`) are legacy versions. They should be **deleted** in a cleanup PR, not uncommented. Before deleting, confirm the replacement code is working in production.

Old server files (`server_old.py`, `server_old1.0.py`, `api_main.py`) should be deleted after confirming `server.py` and `api_prod.py` are stable in production.
