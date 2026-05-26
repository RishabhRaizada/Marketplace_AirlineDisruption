# Frontend — prodDisruption

## Overview

**There is no frontend code in this repository.** This service is a pure backend API. The frontend is a separate project that communicates with this service via REST.

---

## Integration Points

The frontend connects to `api_prod.py` on whichever port it's started on.

### Disruption Check

```
POST /disruption
Content-Type: application/json

{
  "pnr": "ABC123",
  "last_name": "SHARMA",
  "tenant_id": "indigo_mock"
}
```

See `docs/api.md` for full request/response documentation.

---

## Frontend Responsibilities

The frontend is responsible for:

1. **Collecting** PNR, last name, and tenant ID from the user
2. **Calling** `POST /disruption`
3. **Routing** the response based on `flow`:
   - `"recovery"` → show selected flight + seat + reasoning
   - `"messaging"` → show personalized messages per channel (SMS, WhatsApp, Email)
4. **Handling** error/ineligible responses gracefully:
   - `status: "error"` → show error message with `reason` code
   - `status: "ineligible"` → inform passenger they are not eligible for auto-recovery
   - `status: "ignored"` → this event type is not handled

---

## CORS

The backend currently allows all origins (`allow_origins=["*"]`). Before production, the backend will restrict this to the actual frontend domain. The frontend team must provide the production domain to update this setting.

---

## Response Field Reference

For the recovery flow, the frontend should display:

| Field | Path | Notes |
|---|---|---|
| Origin | `event.origin` | Airport code |
| Destination | `event.destination` | Airport code |
| Original flight | `event.flight_number` | |
| New flight | `result.selected_flight.flight_number` | |
| New departure | `result.selected_flight.utcDeparture` | UTC timestamp |
| Seat number | `result.selected_seat.seat_number` | e.g., "12A" |
| Seat class | `result.selected_seat.travel_class` | "Y" = Economy, "C" = Business |
| Reasoning | `result.reasoning.flight_reason` | Human-readable explanation |

For the messaging flow, the frontend should:

| Field | Path | Notes |
|---|---|---|
| Message group ID | `result.selected_group_id` | For tracking/analytics |
| SMS message | Find `result.messages[]` where `channel == "sms"` | |
| WhatsApp message | Find `result.messages[]` where `channel == "whatsapp"` | |
| Email message | Find `result.messages[]` where `channel == "email"` | |

---

## Admin Panel Integration

The admin panel (separate project) manages:
- Active Azure AI agent IDs → stored in Cosmos DB `agents` collection
- Active prompts → stored in Cosmos DB `prompts` collection
- Message templates → stored in Cosmos DB `templates` collection
- Tenant configurations → stored in Cosmos DB `tenants` collection

The admin panel writes directly to Cosmos DB. This service reads from it on every request (no caching currently).

The `GET /admin/config/{tenant_id}` endpoint can be used by the admin panel to verify the current active configuration.

The `POST /admin/update-config` endpoint currently **does not work** (raises an exception) — all config updates must go through the admin panel's direct Cosmos DB writes.
