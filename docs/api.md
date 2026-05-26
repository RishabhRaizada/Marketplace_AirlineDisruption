# API Reference — prodDisruption

All endpoints are served by `api_prod.py` via FastAPI.

Base URL (local): `http://localhost:<port>`

---

## POST `/disruption`

The main endpoint. Handles all flight disruption scenarios.

### Request

```json
{
  "pnr": "ABC123",
  "last_name": "SHARMA",
  "tenant_id": "indigo_mock"
}
```

| Field | Type | Required | Description |
|---|---|---|---|
| `pnr` | string | yes | Passenger Name Record (booking reference) |
| `last_name` | string | yes | Passenger's last name (case-insensitive match) |
| `tenant_id` | string | yes | Tenant identifier (`indigo_mock`, `airline_mock`) |

### Response — Recovery Flow (Cancelled Flight)

```json
{
  "status": "success",
  "flow": "recovery",
  "event": {
    "event_type": "flight_cancelled",
    "flight_number": "6E-101",
    "origin": "DEL",
    "destination": "BOM",
    "scheduled_departure_time": "2024-03-15T08:30:00",
    "scheduled_arrival_time": "2024-03-15T10:30:00",
    "utc_scheduled_departure": "2024-03-15T03:00:00Z",
    "utc_scheduled_arrival": "2024-03-15T05:00:00Z"
  },
  "result": {
    "cdp_summary": {
      "student": 0,
      "highspender": true,
      "business": 2,
      "leisure": 1
    },
    "selected_flight": {
      "flight_uid": "...",
      "flight_number": "6E-201",
      "origin": "DEL",
      "destination": "BOM",
      "utcDeparture": "2024-03-15T06:00:00Z",
      "isStretch": true,
      "min_business_fare": 8500
    },
    "selected_seat": {
      "seat_number": "3A",
      "travel_class": "C",
      "seat_type": ["LEGROOM", "WINDOW"]
    },
    "reasoning": {
      "flight_reason": "High spender profile — selected earliest available business-class flight",
      "seat_reason": "Business class window seat with legroom for high spender comfort"
    }
  }
}
```

### Response — Messaging Flow (Delayed Flight)

```json
{
  "status": "success",
  "flow": "messaging",
  "event": {
    "event_type": "flight_delayed",
    "flight_number": "6E-101",
    "origin": "DEL",
    "destination": "BOM",
    ...
  },
  "result": {
    "selected_group_id": "MSG-0002",
    "reason": "Student persona detected — delay compensation messaging selected",
    "messages": [
      {
        "id": "MSG-0002-SMS",
        "channel": "sms",
        "message": "Dear Student, your flight DEL → BOM is delayed. We apologize..."
      },
      {
        "id": "MSG-0002-WA",
        "channel": "whatsapp",
        "message": "Dear Student, your flight DEL → BOM is delayed. We apologize..."
      },
      {
        "id": "MSG-0002-EMAIL",
        "channel": "email",
        "message": "Dear Student, your flight DEL → BOM is delayed. We apologize..."
      }
    ]
  }
}
```

### Response — Ineligible Passenger

```json
{
  "final": true,
  "status": "ineligible",
  "reason": "NOT_ELIGIBLE_FOR_AUTORECOVERY"
}
```

*Returned directly from the MCP server without running an agent. Auto-recovery is only available to HIGHSPENDER and STUDENT passengers.*

### Error Responses

**MCP-level errors** (returned from MCP server, forwarded as-is):

| status | reason | Description |
|---|---|---|
| `error` | `TENANT_PNR_LASTNAME_REQUIRED` | Missing required input |
| `error` | `EVENT_NOT_FOUND` | No disruption event for this PNR |
| `error` | `LAST_NAME_MISMATCH` | PNR found but last name doesn't match |
| `error` | `SEGKEY_NOT_FOUND` | Cancelled flight missing segKey for recovery |
| `error` | `AIRLINE_API_FAILURE` | External airline API call failed |
| `ignored` | `UNSUPPORTED_EVENT_TYPE` | Event type is not `flight_cancelled` or `flight_delayed` |

**FastAPI-level errors** (HTTP 500):

```json
{
  "detail": "error message string"
}
```

Thrown when MCP communication fails, Azure AI agent crashes, or JSON parsing fails.

---

## POST `/admin/update-config`

Updates the runtime configuration for a tenant. In the current implementation, this endpoint **raises an exception** because configuration must be managed through the admin panel (which writes directly to Cosmos DB).

```json
{
  "tenant_id": "indigo_mock",
  "recovery_agent_id": "asst_xxx",
  "message_agent_id": "asst_yyy",
  "system_prompt": "Additional system instructions...",
  "text": "Additional user instructions..."
}
```

| Field | Type | Required | Description |
|---|---|---|---|
| `tenant_id` | string | yes | Target tenant |
| `recovery_agent_id` | string | no | Azure AI agent ID for recovery flow |
| `message_agent_id` | string | no | Azure AI agent ID for messaging flow |
| `system_prompt` | string | no | Text prepended to base system prompt |
| `text` | string | no | Text appended after base user prompt |

**Current behavior:** Raises `Exception("Runtime config must be updated through the admin panel / Cosmos DB")` — HTTP 500 returned.

**Historical:** In `api_main.py`, this endpoint updated an in-memory dict. That behaviour was removed in the multi-tenant refactor.

---

## GET `/admin/config/{tenant_id}`

Returns the current runtime configuration for a tenant as read from Cosmos DB.

### Response

```json
{
  "recovery_agent_id": "asst_0ywRqan9UlWxRSM3bcgpJmDh",
  "message_agent_id": "asst_BiR67fIqqynuIHTI3VBTM7rr",
  "prompt_append": "",
  "addon_system_prompt": "...",
  "addon_user_prompt": "..."
}
```

| Field | Description |
|---|---|
| `recovery_agent_id` | Active recovery agent ID from Cosmos `agents` collection |
| `message_agent_id` | Active messaging agent ID from Cosmos `agents` collection |
| `prompt_append` | Legacy field, always `""` |
| `addon_system_prompt` | Active system prompt from Cosmos `prompts` collection |
| `addon_user_prompt` | Active user prompt text from Cosmos `prompts` collection |

**Note:** `tenant_id` path parameter is accepted but not used in config resolution — all tenants share the same active agent/prompt configuration.

---

## MCP Tool: `handle_disruption`

Not a REST endpoint — called internally by `api_prod.py` via JSON-RPC 2.0.

**MCP server:** `http://localhost:8004/mcp`  
**Tool name:** `handle_disruption`

### Parameters
```json
{
  "tenant_id": "indigo_mock",
  "pnr": "ABC123",
  "last_name": "SHARMA"
}
```

### Response Shapes

**Recovery success:**
```json
{
  "final": true,
  "status": "success",
  "flow": "recovery",
  "event": { ... },
  "profile": [ ... ],
  "original_flight": { ... },
  "recovery": {
    "available_flights": [ ... ],
    "available_seats": [ ... ]
  }
}
```

**Messaging success:**
```json
{
  "final": true,
  "status": "success",
  "flow": "messaging",
  "event": { ... },
  "profile": [ ... ],
  "messages": {
    "total_messages": 12,
    "messages": [ ... ]
  }
}
```

**All error/ineligible responses:**
```json
{
  "final": true,
  "status": "error" | "ineligible" | "ignored",
  "reason": "REASON_CODE"
}
```

---

## CORS Policy

```python
allow_origins=["*"]
allow_credentials=True
allow_methods=["*"]
allow_headers=["*"]
```

Currently fully open. Must be restricted to known frontend origins before production deployment.

---

## Agent Prompts (Embedded in API Layer)

The agent prompts are not a REST API but are critical to the system behaviour. They live in `api_prod.py → run_agent()`.

### Recovery Agent Prompt — Key Rules

| Priority | Condition | Action |
|---|---|---|
| 0 (override) | `original_flight.cabin_class == "Business"` | Must select business class flight + seat |
| 1a | `STUDENT > 0` | Cheapest economy flight, cheapest economy seat |
| 1b | `HIGHSPENDERHIGHFREQ or HIGHSPENDERLOWFREQ` | Comfort-first, price irrelevant |
| 2 | Business journey | Time + comfort |
| 2 | Leisure journey | Cost + flexibility |

**Flight scoring:**
- `+40` NonStop
- `+25` Earlier UTC arrival than original
- `+20` Departure closest to original
- `-15` fillingFast == true
- STUDENT: score = `-min_economy_fare` (negate — pick cheapest)
- HIGHSPENDER: `+40` isStretch, `+30` min_business_fare exists

**Seat scoring:**
- STUDENT: prefer `travel_class == "Y"`, ignore comfort features
- HIGHSPENDER: `+40` class C, `+25` LEGROOM, `+20` XL, `+15` AISLE/WINDOW

**Output format:**
```json
{
  "cdp_summary": { "student": 0, "highspender": true, "business": 2, "leisure": 1 },
  "selected_flight": { ... },
  "selected_seat": { ... },
  "reasoning": { "flight_reason": "...", "seat_reason": "..." }
}
```

### Messaging Agent Prompt — Persona Rules

| Priority | Condition | Persona |
|---|---|---|
| 1 | `defense_flag == 1` | DEFENCE |
| 2 | `Doctor flag == 1` | DOCTOR |
| 3 | `student_flag >= 1` | STUDENT |
| 4 | default | GENERAL |

The agent selects a single `group_id` and returns all channels for that group. Placeholders `{var1}` (origin) and `{var2}` (destination) are replaced in all messages.
