# Roadmap — prodDisruption

This document tracks known bugs, technical debt, and planned improvements. Mark items resolved when fixed and add to `docs/changelog.md`.

---

## Critical Bugs (Fix Before Production)

### BUG-001: `validate_request()` missing `tenant_id` parameter
**File:** `tools/validator.py:26-32`  
**Status:** Resolved (2026-05-25)  
**Severity:** Critical — will crash the cancellation flow at runtime

`check_user_autorecovery_eligibility()` calls `find_users(last_name, email_or_phone)` with 2 args, but `cdp_client.find_users()` requires `(tenant_id, last_name, email_or_phone)`.

**Fix:**
```python
# tools/validator.py
def validate_request(tenant_id: str, last_name: str, email_or_phone: str):
    return check_user_autorecovery_eligibility(tenant_id, last_name, email_or_phone)

def check_user_autorecovery_eligibility(tenant_id: str, last_name: str, email_or_phone: str):
    try:
        users = find_users(tenant_id, last_name, email_or_phone)
    ...
```

Also update the call in `server.py:114-117`:
```python
eligibility = validate_request(
    tenant_id,
    last_name,
    event["passenger"]["email"] or event["passenger"]["mobile"]
)
```

---

### BUG-002: `httpx` missing from `requirements.txt`
**File:** `requirements.txt`  
**Status:** Open  
**Severity:** High — will fail on fresh installation

`services/http_client.py` imports `httpx` but it's not listed in `requirements.txt`. Will cause `ModuleNotFoundError` on clean install.

**Fix:** Add `httpx` to `requirements.txt`.

---

### BUG-003: Duplicate `requests` in `requirements.txt`
**File:** `requirements.txt:8,10`  
**Status:** Open  
**Severity:** Low — harmless but indicates lack of hygiene

**Fix:** Remove the duplicate `requests` line.

---

## Technical Debt

### DEBT-001: Legacy server files in repository
**Files:** `server_old.py`, `server_old1.0.py`, `api_main.py`  
**Status:** Open  

These files are superseded by `server.py` and `api_prod.py`. They add noise, can confuse AI agents, and may be accidentally used.

**Action:** Delete after confirming production stability. Archive in git history.

---

### DEBT-002: Unused `tools/event_normalizer.py`
**File:** `tools/event_normalizer.py`  
**Status:** Open  

Defines `normalize_event()` with a different field schema than `server.py`'s inline implementation. Not imported anywhere.

**Action:** Either align with `server.py`'s schema and import it, or delete it.

---

### DEBT-003: Commented-out code in service clients
**Files:** `services/airline_client.py:73-142`, `services/disruption_client.py:41-82`  
**Status:** Open  

Old synchronous implementations preserved as comments. Add noise and mislead readers.

**Action:** Delete the commented-out blocks.

---

### DEBT-004: Commented-out code in `runtime_config.py`
**File:** `config/runtime_config.py:1-19`  
**Status:** Open  

Old in-memory dict implementation. Now superseded by Cosmos DB reading.

**Action:** Delete the commented block.

---

### DEBT-005: `fetch_recovery_prompt()` and `fetch_messaging_prompt()` not in `__all__`
**File:** `config/cosmos_data_fetcher.py:295-319`  
**Status:** Open  

Two functions defined but not exported and not used. Either integrate them into the prompt injection flow or remove them.

**Action:** If the plan is to move prompts to Cosmos DB (instead of hardcoding in `api_prod.py`), integrate these functions. Otherwise delete.

---

### DEBT-006: Per-request Cosmos DB connections with no caching
**File:** `config/runtime_config.py:31-51`  
**Status:** Open  

`get_runtime_config()` is called on every `/disruption` request. It makes 2 Cosmos DB connections (`fetch_active_agents` + `fetch_active_prompt_payload`). Under load, this creates unnecessary latency.

**Action:** Add a short TTL cache (e.g., 30 seconds) to `get_runtime_config()`. Agent/prompt changes take effect within the TTL window.

---

### DEBT-007: `tenant_id` not used in `get_runtime_config()`
**File:** `config/runtime_config.py:31`  
**Status:** Open  

`get_runtime_config(tenant_id)` accepts `tenant_id` but ignores it. All tenants share the same active agents and prompts.

**Action:** When per-tenant agent support is needed, query Cosmos DB with `{"tenant_id": tenant_id}` filter on the `agents` collection.

---

### DEBT-008: CORS fully open
**File:** `api_prod.py:138-144`  
**Status:** Open — must fix before production

`allow_origins=["*"]` accepts requests from any origin.

**Action:** Replace with the actual frontend domain(s) before production deployment.

---

### DEBT-009: `print()` in `cosmos_data_fetcher.py` instead of structured logging
**File:** `config/cosmos_data_fetcher.py` (multiple locations)  
**Status:** Open  

Uses `print(f"[cosmos_data_fetcher] ...")` instead of the project's logging pattern.

**Action:** Replace with `logging.getLogger("cosmos-data-fetcher")` and use `logger.error()`.

---

### DEBT-010: No health check endpoint
**Status:** Open  

No `/health` or `/ping` endpoint exists. Required for load balancers, container orchestrators, and monitoring.

**Action:** Add to `api_prod.py`:
```python
@app.get("/health")
def health():
    return {"status": "ok"}
```

---

### DEBT-011: Only first seat map used per flight
**File:** `services/airline_client.py:47`  
**Status:** Open  

`get_seat_map()` indexes `seat_maps[0]` — if a flight has multiple seat maps (e.g., for different cabin classes), only the first is used.

**Action:** Iterate all seat maps and aggregate seats.

---

### DEBT-012: CDP API key is global, not tenant-specific
**File:** `services/cdp_client.py:13`  
**Status:** Open  

Uses global `CDP_API_KEY` from `config/settings.py` regardless of tenant. In a real multi-tenant scenario, each airline would have its own CDP API credentials.

**Action:** Add `cdp_api_key` to the tenant config schema in Cosmos DB and use it in `cdp_client.py`.

---

## Planned Improvements

### FEAT-001: Per-tenant agent configuration
Allow different Azure AI agents per tenant (not just one globally active agent per flow). Store tenant-specific agent IDs in the `tenants` Cosmos collection.

### FEAT-002: Prompt management via Cosmos DB
Move the full recovery and messaging prompts to Cosmos DB `prompts` collection (using `type: "recovery"` / `type: "messaging"` filtering). Use `fetch_recovery_prompt()` and `fetch_messaging_prompt()` already defined in `cosmos_data_fetcher.py`. This enables prompt changes without code deployment.

### FEAT-003: `flight_diverted` event type
Add a third disruption flow for diverted flights. Would likely be a hybrid of recovery (needs new flight selection) and messaging (needs customer notification).

### FEAT-004: Auto-recovery eligibility for all passengers
Currently, only HIGHSPENDER and STUDENT passengers get auto-recovery. Consider extending to all passengers with a configurable eligibility rule.

### FEAT-005: Seat map caching
Seat maps are static within a scheduling period. Cache them in Cosmos DB or Redis to avoid redundant API calls during high-load periods.

### FEAT-006: Request idempotency
If the same PNR is submitted twice rapidly, two agent runs are started. Add a request deduplication layer (cache recent PNR/tenant combinations for 60 seconds).

### FEAT-007: Structured logging to Azure Monitor
Replace Python `logging` with structured JSON logs forwarded to Azure Monitor / Application Insights for searchable telemetry.

### FEAT-008: Test suite
No tests exist. Add pytest-based unit tests for:
- `safe_json_from_agent()` — JSON extraction edge cases
- `normalize_event()` — field mapping correctness
- `normalize_bool()` / `normalize_student()` in validator
- `extract_flights()` / `collect_seatmaps()` — data flattening

And integration tests for the `/disruption` endpoint with mock MCP responses.

---

### FEAT-009: SaaS Accelerator + Marketplace metered billing
**Status:** Planned — required before Azure Marketplace listing or self-serve onboarding

Today, tenant onboarding is operator-driven (`seed_tenants.py` + Cosmos upsert + manual APIM subscription key generation). For Marketplace listing and self-serve airline signup, integrate the Azure SaaS Accelerator as the subscription lifecycle owner and the billing aggregator.

**Components to add:**
- **`saas-accelerator` Container App** — hosts the Azure SaaS Accelerator (Microsoft reference impl), serving:
  - Marketplace landing page (subscription activation webhook from Azure Marketplace)
  - Tenant provisioning into the `tenants` Cosmos collection (replaces manual `seed_tenants.py`)
  - Post-activation redirect into the existing Admin Panel
  - Metered usage submission to the Marketplace Metering API
- **`saas_subscriptions` Cosmos collection** — joins `tenant_id` ↔ Marketplace `subscriptionId` ↔ `planId` ↔ `metered_dimensions[]` ↔ `status`. Read by both Admin Panel (plan tier, entitlements) and SaaS Accelerator (billing target resolution).
- **APIM → SaaS Accelerator metering pipeline:** APIM emits per-request usage events (`{tenant_id, dimension: "disruption_request", quantity: 1, timestamp}`) to a SaaS Accelerator ingestion endpoint via an APIM policy. SaaS Accelerator aggregates, batches, and submits to `POST /api/usageEvent` against the Marketplace Metering API (resolving `tenant_id` → `subscriptionId` via the new collection).

**Why this shape (vs APIM → Marketplace Metering API directly):**
- APIM does not natively know the Marketplace `subscriptionId` for a given `tenant_id`; SaaS Accelerator owns that mapping anyway because it handles activation.
- Aggregation, retry, and dedup logic live in one service instead of being smeared across APIM policies.
- The same service that activates a subscription is the one that bills against it — single source of truth for subscription state.

**Trigger:** Either of:
- First Azure Marketplace listing requirement, or
- More than 5 tenants making manual onboarding operationally painful.

**Dependencies / pre-work:**
- Resolve BLOCKER-1 (auth) — Marketplace JWT flow assumes the platform already validates identity per request.
- Resolve DEBT-008 (CORS) — Marketplace landing page must be on a known origin.
- DEBT-006 caching of `get_runtime_config()` should land first; per-request plan/entitlement lookups will otherwise add Cosmos load.

**Out of scope for this FEAT (deliberately):**
- Per-tenant pricing model design (plan tiers, dimensions, included quotas) — product decision, not architectural.
- Migrating existing manually-provisioned tenants — they keep their current path; this only governs new self-serve signups.
