# Database — prodDisruption

## Overview

The system uses **Azure Cosmos DB** with the **MongoDB API** (not native Cosmos API). All access goes through PyMongo. The database name is `flight_operations`.

**Connection string:** `mongodb+srv://{user}:{password}@aionospluggable.global.mongocluster.cosmos.azure.com/?retryWrites=true&w=majority`

Credentials are loaded from `.env`:
- `COSMOS_DB_USERNAME` → `pluggableagent`
- `COSMOS_DB_PASSWORD` → `AIONOS@1234`
- `COSMOS_DB_HOST` → `aionospluggable.global.mongocluster.cosmos.azure.com`
- `COSMOS_DB_NAME` → `flight_operations`

**Warning:** The `.env` file contains plaintext credentials and is gitignored. Never commit it.

---

## Collections

### `tenants`

Stores API endpoint configuration per airline tenant.

**Schema:**
```json
{
  "tenant_id": "indigo_mock",
  "flight_api": "https://mock-flightdetails-api.niceflower-39d2e00e.southindia.azurecontainerapps.io",
  "cdp_api": "https://cdp-mock-api.niceflower-39d2e00e.southindia.azurecontainerapps.io/",
  "disruption_api": "https://disruption-mock-api1.niceflower-39d2e00e.southindia.azurecontainerapps.io/"
}
```

**Access:** `fetch_tenant_config(tenant_id)` — query: `{"tenant_id": tenant_id}`

**Managed by:** `seed_tenants.py` (upsert on `tenant_id`)

**Notes:**
- `flight_api` — base URL for `/flight-search` and `/seat-map/{segKey}`
- `cdp_api` — base URL for `/cdp/user-lookup`
- `disruption_api` — base URL for `/disruptions/{pnr}`
- No trailing slash on `flight_api`, trailing slash present on others — be consistent when constructing URLs

---

### `agents`

Registry of Azure AI Agents. Flags determine which agents are active for each flow.

**Schema:**
```json
{
  "agent_id": "asst_0ywRqan9UlWxRSM3bcgpJmDh",
  "name": "Recovery Agent v2",
  "description": "Selects optimal recovery flight and seat based on CDP profile",
  "is_active_recovery": true,
  "is_active_messaging": false
}
```

**Access:** `fetch_active_agents()` — query: `{$or: [{is_active_recovery: true}, {is_active_messaging: true}]}`

**Projection:** `{agent_id, name, description, is_active_recovery, is_active_messaging}`

**Notes:**
- Only one agent should have `is_active_recovery: true` at a time; if multiple do, `get_runtime_config()` will use the last one encountered
- Similarly for `is_active_messaging`
- Agent hot-swap: change flags in this collection — no restart needed

---

### `prompts`

Stores system and user prompts that are injected by the admin panel. The active prompt is prepended/appended to the base hardcoded prompts in `api_prod.py`.

**Schema:**
```json
{
  "name": "Recovery Prompt Override",
  "is_active": true,
  "system_prompt": "Additional instructions prepended to the base prompt...",
  "user_prompt": "Additional text appended after the base prompt...",
  "type": "recovery"
}
```

**Access:**
- `fetch_active_prompt_payload()` — query: `{"is_active": True}`
- `fetch_recovery_prompt()` — query: `{"type": "recovery", "is_active": True}` → system_prompt only
- `fetch_messaging_prompt()` — query: `{"type": "messaging", "is_active": True}` → user_prompt only

**Notes:**
- The `type` field (`"recovery"` or `"messaging"`) is not currently used in the main flow — `get_runtime_config()` uses `fetch_active_prompt_payload()` which does not filter by type
- The admin panel should ensure only one document has `is_active: true`
- Legacy field aliases accepted: `systemPrompt` → `system_prompt`, `userPrompt` / `text` → `user_prompt`

---

### `templates`

Stores message templates for the messaging flow (delay notifications).

**Schema:**
```json
{
  "name": "Default Delay Templates v1",
  "is_active": true,
  "content": {
    "raw_content": "{\"MESSAGES\": [...]}"
  }
}
```

The `content.raw_content` field is a **JSON string** (not a nested object) containing the actual message data.

**Inner structure of `raw_content`:**
```json
{
  "MESSAGES": [
    {
      "group_id": "MSG-0001",
      "delay_count": 1,
      "channels": [
        {
          "id": "MSG-0001-SMS",
          "channel": "sms",
          "message": "Your flight {var1} to {var2} is delayed..."
        },
        {
          "id": "MSG-0001-WA",
          "channel": "whatsapp",
          "message": "Your flight {var1} to {var2} is delayed..."
        },
        {
          "id": "MSG-0001-EMAIL",
          "channel": "email",
          "message": "Your flight {var1} to {var2} is delayed..."
        }
      ]
    }
  ]
}
```

**Access:** `fetch_active_template()` — query: `{"is_active": True}`, fallback: `{}`

**Placeholder convention:** `{var1}` = origin, `{var2}` = destination (replaced by AI agent, not Python code)

**Notes:**
- The `delay_count` field in message groups is available to the AI agent for selecting appropriate messages based on how many times the flight has been delayed
- `group_id` convention: `MSG-XXXX` where XXXX is zero-padded number
- Channel ID convention: `MSG-XXXX-SMS`, `MSG-XXXX-WA`, `MSG-XXXX-EMAIL`

---

### `settings`

General API settings document.

**Schema:**
```json
{
  "id": "api_settings",
  ...additional fields as needed...
}
```

**Access:** `fetch_api_settings()` — query: `{"id": "api_settings"}`

**Current usage:** Fetched but not actively used in the main request flow. Available for future configuration needs.

---

### `flight_data` (Legacy / Batch)

Contains pre-loaded flight search data. Used by `load_flights()` utility in `cosmos_data_fetcher.py`.

**Not used in the live request flow** — the live flow calls the airline API directly. This collection may support batch analysis or admin tooling.

**Document structure:** Nested under `content.extracted_data` or directly as `data.trips[].journeysAvailable[]`.

---

### `available_seats` (Legacy / Batch)

Contains pre-loaded seat map data. Used by `load_seatmaps()` utility.

**Not used in the live request flow.** Same pattern as `flight_data`.

**Document structure:** Nested under `content.extracted_data` or `data.seatMaps[]` or `seatMaps[]`.

---

## Connection Pattern

A new connection is opened and closed for each database operation. This is acceptable for a low-throughput admin/config data pattern (tenants, agents, prompts are read infrequently relative to the number of requests — but currently read on every request).

```python
def _get_db():
    client = MongoClient(COSMOS_DB_URI, serverSelectionTimeoutMS=5000)
    return client, client[COSMOS_DB_NAME]
```

**Known performance issue:** `get_runtime_config()` is called on every `/disruption` request and makes two Cosmos DB connections (`fetch_active_agents()` + `fetch_active_prompt_payload()`). For high throughput, these should be cached with a short TTL (e.g., 30 seconds).

---

## Access Patterns Summary

| Collection | Read frequency | Write mechanism |
|---|---|---|
| `tenants` | Per request (on service client calls) | `seed_tenants.py` / admin panel |
| `agents` | Per request (in `get_runtime_config`) | Admin panel |
| `prompts` | Per request (in `get_runtime_config`) | Admin panel |
| `templates` | Per request (messaging flow only) | Admin panel |
| `settings` | Not currently in main flow | Admin panel |
| `flight_data` | Batch/utility only | Data ingestion pipeline |
| `available_seats` | Batch/utility only | Data ingestion pipeline |

---

## Seeding / Migration

To seed tenant records:
```bash
python seed_tenants.py
```

This performs an upsert — safe to re-run. It inserts `indigo_mock` and `airline_mock` tenants.

There are no migration scripts. Schema changes require manual updates via MongoDB compass or the admin panel.

---

## Indexes

No explicit indexes are defined in code. The following fields are queried and should have indexes for production performance:

| Collection | Recommended Index |
|---|---|
| `tenants` | `{"tenant_id": 1}` (unique) |
| `agents` | `{"is_active_recovery": 1, "is_active_messaging": 1}` |
| `prompts` | `{"is_active": 1}`, `{"type": 1, "is_active": 1}` |
| `templates` | `{"is_active": 1}` |
| `settings` | `{"id": 1}` |
