# Debugging — prodDisruption

## Log Format

The MCP server (`server.py`) uses Python's `logging` module at `INFO` level. Log messages follow `KEY=VALUE` format for easy grep/parsing:

```
TENANT=indigo_mock PNR=ABC123 LAST_NAME=SHARMA
FLIGHT_SEARCH_RESULT_COUNT=5
SEATS_FOUND_FOR_FLIGHT=SEG123 COUNT=48
SEATMAP_FETCH_FAILED=SEG456 ERROR=404 Not Found
TOTAL_SEATS_FOUND=96
AIRLINE_API_ERROR
```

The FastAPI server logs using `print()` (in `cosmos_data_fetcher.py`) — a known inconsistency.

---

## Common Failures and Fixes

### 1. `RuntimeError: No valid MCP JSON found`

**Where:** `api_prod.py → execute_mcp_tool()`

**Cause:** The MCP server returned a response that didn't contain a `json` content block. This can happen when:
- The MCP server itself threw an exception and returned a non-standard response
- The SSE stream was empty or malformed
- FastMCP changed its response format

**Debug steps:**
1. Check that `server.py` is running: `curl http://localhost:8004/mcp`
2. Check MCP server logs for exceptions
3. Add temporary `print(response.text)` in `execute_mcp_tool()` to see raw response

---

### 2. `RuntimeError: MCP call failed`

**Where:** `api_prod.py → execute_mcp_tool()`

**Cause:** MCP server returned non-200 HTTP status.

**Debug steps:**
1. Is `server.py` running on port 8004?
2. Check MCP server stderr for startup errors
3. Verify `MCP_URL = "http://localhost:8004/mcp"` matches actual port

---

### 3. `TimeoutError: Agent timeout` (after 120s)

**Where:** `api_prod.py → run_agent()`

**Cause:** Azure AI agent run didn't complete within 120 seconds.

**Debug steps:**
1. Check Azure AI Foundry portal for stuck/failed runs
2. Verify the agent ID in Cosmos DB `agents` collection is valid
3. Check Azure service health for outages
4. The prompt may be too long — check if `mcp_data` is unusually large

---

### 4. `RuntimeError: Agent returned no text messages`

**Where:** `api_prod.py → run_agent()`

**Cause:** The agent run completed but produced no assistant message text. Can happen with Azure agent errors or content policy violations.

**Debug steps:**
1. Check Azure AI Foundry portal for the specific run and thread
2. Review the full prompt for content that might trigger safety filters
3. Check if `run.status` is actually `COMPLETED` or an error state

---

### 5. `ValueError: No JSON object found in agent output`

**Where:** `api_prod.py → safe_json_from_agent()`

**Cause:** Agent returned text that contained no JSON object (no `{` or `}`). The raw text is included in the error message.

**Debug steps:**
1. Print `raw` in `run_agent()` before calling `safe_json_from_agent(raw)` to see agent output
2. Check if prompt instructions clearly say "return ONLY valid JSON"
3. Temperature 0.1 should prevent this but it can still happen on edge cases

---

### 6. `TypeError` on `validate_request()` call

**Where:** `server.py → handle_disruption()` → `tools/validator.py → check_user_autorecovery_eligibility()`

**Cause:** Known bug — `validator.py` calls `find_users(last_name, email_or_phone)` with 2 args, but `cdp_client.find_users()` requires 3 args `(tenant_id, last_name, email_or_phone)`.

**Fix:** Update `validate_request()` signature to accept `tenant_id` and pass it through:
```python
def validate_request(tenant_id: str, last_name: str, email_or_phone: str):
    return check_user_autorecovery_eligibility(tenant_id, last_name, email_or_phone)

def check_user_autorecovery_eligibility(tenant_id, last_name, email_or_phone):
    users = find_users(tenant_id, last_name, email_or_phone)
    ...
```
Also update the call in `server.py`:
```python
eligibility = validate_request(
    tenant_id,
    last_name,
    event["passenger"]["email"] or event["passenger"]["mobile"]
)
```

---

### 7. `Exception: Unknown tenant: {tenant_id}`

**Where:** `tenant/tenant_config.py → get_tenant_config()`

**Cause:** The `tenant_id` is not in Cosmos DB and not in `FALLBACK_TENANTS`.

**Fix options:**
1. Add the tenant to Cosmos DB via `seed_tenants.py` or admin panel
2. Add to `FALLBACK_TENANTS` in `tenant_config.py` as a temporary measure

---

### 8. `Exception: Cosmos DB flight fetch failed` / `Cosmos DB seat fetch failed`

**Where:** `config/cosmos_data_fetcher.py`

**Cause:** Cosmos DB connectivity issue or malformed document.

**Debug steps:**
1. Check `.env` credentials are correct
2. Check network access to `aionospluggable.global.mongocluster.cosmos.azure.com`
3. Verify the collection exists and has documents: use MongoDB Compass or Azure portal
4. `serverSelectionTimeoutMS=5000` — connection will fail fast if unreachable

---

### 9. Agent returns non-JSON or wrong JSON structure

**Symptom:** `json.JSONDecodeError` or `KeyError` when processing agent result.

**Cause:** Agent hallucinated or didn't follow the strict output format.

**Debug:** `temperature=0.1, top_p=0.1` makes this unlikely but not impossible. Check:
1. Is the prompt injection from admin prompt (`system_prompt`, `text`) overriding the format instructions?
2. Is `mcp_data` so large that it's pushing critical instructions out of context?

---

### 10. `LAST_NAME_MISMATCH`

**Where:** `server.py → handle_disruption()`

**Cause:** The last name in the request doesn't match `user_info.USR_LASTNAME` in the disruption event.

**Note:** Comparison is case-insensitive (`event["last_name"].lower() != last_name.lower()`). If still failing, the disruption API returned an event for a different passenger.

---

### 11. `NOT_ELIGIBLE_FOR_AUTORECOVERY`

**Where:** `server.py → handle_disruption()` (cancellation flow)

**Cause:** Passenger's CDP profile shows neither HIGHSPENDER nor STUDENT flags.

**Note:** This is expected business logic, not an error. The passenger is not entitled to auto-recovery. Return this status to the frontend as-is.

---

### 12. Empty seat maps / no available seats

**Symptom:** `recovery.available_seats == []` even though flights exist.

**Cause options:**
- All seats on available flights are non-assignable
- `get_seat_map()` is indexing `seat_maps[0]` — if the flight has no seat maps, returns `[]`
- Seat map API returned 4xx (logged as `SEATMAP_FETCH_FAILED` but not fatal)

**Debug:** Check MCP logs for `SEATMAP_FETCH_FAILED` entries.

---

## Log Grep Recipes

```bash
# Watch all disruption requests
grep "TENANT=" server.log

# Find seatmap failures
grep "SEATMAP_FETCH_FAILED" server.log

# Find airline API errors
grep "AIRLINE_API_ERROR" server.log

# Count flights found per request
grep "FLIGHT_SEARCH_RESULT_COUNT" server.log

# Count total seats aggregated
grep "TOTAL_SEATS_FOUND" server.log
```

---

## Useful Admin Checks

```bash
# Check current runtime config (agents + prompts) for a tenant
curl http://localhost:8000/admin/config/indigo_mock

# Manually test MCP tool
curl -X POST http://localhost:8004/mcp \
  -H "Accept: application/json, text/event-stream" \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc": "2.0",
    "id": "debug-1",
    "method": "tools/call",
    "params": {
      "name": "handle_disruption",
      "arguments": {"pnr": "ABC123", "last_name": "SHARMA", "tenant_id": "indigo_mock"}
    }
  }'
```

---

## Azure AI Foundry Debugging

To inspect agent runs in the Azure portal:
1. Go to Azure AI Foundry → your project
2. Navigate to "Agents" → find the agent by ID
3. Look at "Threads" and "Runs" for recent activity
4. Check run status, failure reasons, and message content

Agent IDs can be found via:
```bash
curl http://localhost:8000/admin/config/indigo_mock
```
Look for `recovery_agent_id` and `message_agent_id`.
