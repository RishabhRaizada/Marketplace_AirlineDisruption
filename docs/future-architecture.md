# TO-BE: Enterprise Azure Production Architecture — prodDisruption

> **Future State (TO-BE).** For the current AS-IS architecture see `docs/diagrams.md`.

**Version:** 2.0 — Enterprise SaaS Platform  
**Target scale:** 1M+ disruption events/day · Multi-region active-active · Per-tenant AI agent isolation  
**Deployment:** Azure Container Apps (private VNET) + Azure Static Web Apps

---

## Architecture Principles

| Principle | Implementation |
|---|---|
| Async-first, non-blocking | Service Bus queue + Worker Pool (no synchronous AI calls in hot path) |
| Idempotent by default | Redis key `{tenant}:{pnr}:{sha256(request)}` with TTL prevents duplicate jobs |
| No localhost coupling | MCP Gateway is a private Container App, not a co-located process |
| Two-pass AI for recovery | AI selects flight first, seat map fetched only for that flight, then AI selects seat |
| Zero-trust security | Managed Identity everywhere, Key Vault references, private endpoints |
| Per-tenant isolation | Cosmos DB partition key = `tenant_id`, per-tenant AI agents, Redis key namespacing |
| Full observability | Correlation IDs propagated end-to-end, AI token metrics, latency histograms |
| Resilient queuing | Service Bus DLQ + configurable retry with exponential backoff |

---

## Platform Component Registry

| Container App | Ingress | Min/Max Replicas | Role |
|---|---|---|---|
| `api-orchestrator` | External (via APIM) | 2 / 20 | REST API, async dispatch, job status |
| `mcp-gateway` | Internal only | 2 / 10 | Data orchestration, external API fan-out |
| `worker-pool` | Internal + SB trigger | 1 / 50 | AI execution, Service Bus consumers |
| `api-admin` | External (via APIM) | 1 / 5 | Tenant/agent/prompt/config management |
| `event-processor` | External webhook (via APIM) | 1 / 10 | Airline webhook intake, proactive disruption |
| Frontend | Public (Static Web Apps) | N/A | Passenger SPA + Admin SPA |

---

## 1. High-Level Enterprise Architecture

> Full platform from public internet through edge, orchestration, async processing, AI, data, and observability.

```mermaid
flowchart TB
    classDef apim fill:#0078d4,stroke:#005a9e,color:#fff
    classDef aca fill:#3b82f6,stroke:#1d4ed8,color:#fff
    classDef data fill:#0f766e,stroke:#0d5c55,color:#fff
    classDef ai fill:#7c3aed,stroke:#5b21b6,color:#fff
    classDef sec fill:#b45309,stroke:#7c2d12,color:#fff
    classDef obs fill:#15803d,stroke:#14532d,color:#fff
    classDef saas fill:#1e3a5f,stroke:#0f2240,color:#fff
    classDef ext fill:#6b7280,stroke:#4b5563,color:#fff
    classDef edge fill:#0ea5e9,stroke:#0284c7,color:#fff

    subgraph INTERNET["PUBLIC INTERNET"]
        PASSENGERS["Airline Passengers"]
        ADMINS["Airline Admins"]
        AIRLINE_WH["Airline Webhook Sources"]
        MARKETPLACE["Azure Marketplace"]
    end

    subgraph EDGE_LAYER["AZURE EDGE"]
        AFD["Azure Front Door\n+ WAF Policy"]
        APIM["Azure API Management\nJWT Auth · Rate Limiting · Tenant Injection · Trace ID Propagation"]
        SWA["Azure Static Web Apps\nPassenger SPA + Admin SPA"]
    end

    subgraph ACA_ENV["AZURE CONTAINER APPS ENVIRONMENT (Private VNET)"]
        direction TB
        subgraph PUBLIC_ACA["Externally Reachable via APIM"]
            ORCH["api-orchestrator\n(FastAPI Orchestrator)"]
            ADMIN_CA["api-admin\n(Admin Panel Backend)"]
            EVTP["event-processor\n(SegKey Event Processor)"]
        end
        subgraph INTERNAL_ACA["Internal-Only (no public ingress)"]
            MCP_CA["mcp-gateway\n(MCP Gateway)"]
            WORKER_CA["worker-pool\n(Worker Services)"]
        end
    end

    subgraph MESSAGING["AZURE MESSAGING & CACHE"]
        SB["Azure Service Bus\ndisruption-requests · dead-letter · retry"]
        REDIS["Azure Cache for Redis\nIdempotency · PNR Cache · Seat Maps · Job State"]
    end

    subgraph DB_LAYER["AZURE DATA LAYER"]
        COSMOS["Azure Cosmos DB\nflight_operations\n(partitioned by tenant_id)"]
    end

    subgraph AI_LAYER["AZURE AI PLATFORM"]
        AIF["Azure AI Foundry\nGPT-5.1 · Recovery Agent · Messaging Agent\n(per-tenant agent registry)"]
    end

    subgraph EXT_APIS["TENANT EXTERNAL APIS (per tenant)"]
        DIS_EXT["Disruption APIs"]
        CDP_EXT["CDP APIs"]
        ALN_EXT["Airline Flight APIs"]
    end

    subgraph SAAS_CP["AZURE SAAS ACCELERATOR (Control Plane)"]
        ONBOARD["Tenant Onboarding\n& Provisioning"]
        BILLING["Billing &\nSubscription Lifecycle"]
    end

    subgraph OBS["OBSERVABILITY"]
        APPINS["Azure Application Insights\nDistributed Tracing · AI Telemetry"]
        MONITOR["Azure Monitor\nMetrics · Alerts · Dashboards"]
        LAW["Log Analytics Workspace\nCentralized Logs"]
    end

    subgraph SEC["SECURITY"]
        KV["Azure Key Vault\nSecrets · Certs · Keys"]
        MI["Managed Identity\n(all Container Apps)"]
        ACR["Azure Container Registry\nPrivate image repository"]
    end

    PASSENGERS --> AFD
    ADMINS --> AFD
    AIRLINE_WH --> AFD
    MARKETPLACE --> ONBOARD

    AFD --> SWA
    AFD --> APIM
    APIM --> ORCH
    APIM --> ADMIN_CA
    APIM --> EVTP

    ORCH --> SB
    ORCH --> REDIS
    ORCH --> COSMOS
    EVTP --> SB

    WORKER_CA --> SB
    WORKER_CA --> MCP_CA
    WORKER_CA --> AIF
    WORKER_CA --> REDIS
    WORKER_CA --> COSMOS

    MCP_CA --> DIS_EXT
    MCP_CA --> CDP_EXT
    MCP_CA --> ALN_EXT
    MCP_CA --> COSMOS

    ADMIN_CA --> COSMOS
    ONBOARD --> COSMOS
    BILLING --> COSMOS

    ORCH & WORKER_CA & MCP_CA & ADMIN_CA & EVTP --> APPINS
    APPINS --> MONITOR
    APPINS --> LAW
    MONITOR --> LAW

    MI -..-> KV
    ACR -..-> ACA_ENV

    class AFD,APIM,SWA edge
    class ORCH,ADMIN_CA,EVTP,MCP_CA,WORKER_CA aca
    class SB,REDIS messaging
    class COSMOS data
    class AIF ai
    class KV,MI,ACR sec
    class APPINS,MONITOR,LAW obs
    class ONBOARD,BILLING saas
    class DIS_EXT,CDP_EXT,ALN_EXT ext
```

---

## 2. Control Plane vs Data Plane

> The platform is split into two distinct planes. The Control Plane manages configuration, tenants, and agents. The Data Plane executes disruption workflows at runtime.

```mermaid
flowchart LR
    subgraph CP["CONTROL PLANE"]
        direction TB
        subgraph SAAS_ACC["Azure SaaS Accelerator"]
            MP["Azure Marketplace\nOffer Listing"]
            PROV["Tenant Provisioning\nWorkflow"]
            SUBLIFE["Subscription Lifecycle\n(Create / Suspend / Delete)"]
            BILLING_CP["Billing Integration\nAzure Commercial Marketplace"]
        end
        subgraph ADMIN_PLANE["Admin Management Layer"]
            ADMIN_API_CP["api-admin Container App\n(Admin Panel Backend)"]
            TENANT_MGT["Tenant Management\n(onboard, config, API keys)"]
            AGENT_MGT["AI Agent Registry\n(per-tenant agent IDs)"]
            PROMPT_MGT["Prompt Management\n(system / user prompts)"]
            TEMPLATE_MGT["Message Template Management"]
            FEATURE_FLAGS["Feature Flags\n& Runtime Config"]
            AUDIT["Audit Log API"]
        end
        subgraph CP_DATA["Control Plane Data Store"]
            COSMOS_CP["Azure Cosmos DB\ntenants · agents · prompts\ntemplates · settings\naudit_logs"]
            KV_CP["Azure Key Vault\ntenant API keys · secrets"]
        end
    end

    subgraph DP["DATA PLANE (Runtime)"]
        direction TB
        subgraph ENTRY["Entry Layer"]
            APIM_DP["Azure API Management\nJWT · tenant_id inject · rate limit"]
            ORCH_DP["api-orchestrator\nREST API + async dispatch"]
        end
        subgraph ASYNC_PROC["Async Processing"]
            SB_DP["Azure Service Bus\ndisruption-requests queue"]
            WORKER_DP["worker-pool\nService Bus consumers\nAI execution"]
        end
        subgraph DATA_ORC["Data Orchestration"]
            MCP_DP["mcp-gateway\nExternal API fan-out\nasync connector"]
        end
        subgraph CACHE_DP["Cache & State"]
            REDIS_DP["Azure Cache for Redis\nIdempotency · PNR Cache\nSeat Map Cache · Job State"]
        end
        subgraph AI_DP["AI Execution"]
            AIF_DP["Azure AI Foundry\nRecovery Agent (Pass 1 + 2)\nMessaging Agent"]
        end
        subgraph EXT_DP["Tenant External APIs"]
            EXTAPIS["Disruption API\nCDP API · Airline API"]
        end
    end

    MP --> PROV --> SUBLIFE --> BILLING_CP
    PROV --> COSMOS_CP
    ADMIN_API_CP --> COSMOS_CP
    TENANT_MGT & AGENT_MGT & PROMPT_MGT & TEMPLATE_MGT --> COSMOS_CP
    FEATURE_FLAGS & AUDIT --> COSMOS_CP
    COSMOS_CP -..->|"read at runtime"| WORKER_DP
    COSMOS_CP -..->|"read at runtime"| ORCH_DP
    KV_CP -..->|"Managed Identity"| WORKER_DP
    KV_CP -..->|"Managed Identity"| MCP_DP

    APIM_DP --> ORCH_DP
    ORCH_DP --> SB_DP
    WORKER_DP --> SB_DP
    WORKER_DP --> MCP_DP
    WORKER_DP --> AIF_DP
    WORKER_DP --> REDIS_DP
    MCP_DP --> EXTAPIS
    ORCH_DP --> REDIS_DP
```

---

## 3. Async Runtime Flow

> Core request lifecycle. The hot path returns immediately (202 Accepted). AI execution happens asynchronously in the worker pool.

```mermaid
sequenceDiagram
    autonumber
    actor C as Client
    participant APIM as Azure APIM
    participant ORCH as api-orchestrator
    participant REDIS as Redis
    participant SB as Service Bus
    participant WORKER as worker-pool
    participant MCP as mcp-gateway
    participant AIF as Azure AI Foundry
    participant COSMOS as Cosmos DB

    C->>APIM: POST /disruption {pnr, last_name, tenant_id}
    Note over APIM: Validate JWT<br/>Inject X-Tenant-ID header<br/>Inject X-Trace-ID header<br/>Check rate limit counter (Redis)

    APIM->>ORCH: Forwarded request + headers

    ORCH->>REDIS: GET idempotency:{tenant_id}:{pnr}
    alt Cached result exists (within TTL)
        REDIS-->>ORCH: Cached result
        ORCH-->>C: 200 OK (cached, no job created)
    else No cached result
        ORCH->>REDIS: SET job:{job_id} = {status:"queued", tenant_id, pnr}
        ORCH->>SB: Publish DisruptionJob {job_id, tenant_id, pnr, last_name, trace_id}
        ORCH-->>C: 202 Accepted {job_id, status_url: /jobs/{job_id}}
    end

    Note over SB: Message in disruption-requests queue<br/>Max delivery count: 5<br/>Lock duration: 5 minutes

    SB->>WORKER: Receive DisruptionJob (competing consumer)
    WORKER->>REDIS: SET job:{job_id} = {status:"processing"}

    WORKER->>MCP: orchestrate(tenant_id, pnr, last_name, trace_id)
    Note over MCP: Fetch disruption event<br/>Validate last_name<br/>Check eligibility (CDP)<br/>Fetch flights (parallel with CDP profile)
    MCP-->>WORKER: {flow, event, profile, flights}

    alt Recovery flow (flight_cancelled)
        Note over WORKER: TWO-PASS AI EXECUTION
        WORKER->>AIF: Pass 1 — select best flight<br/>(profile + event + flights, NO seat maps)
        AIF-->>WORKER: {selected_flight, flight_reasoning}
        WORKER->>MCP: fetch_seat_map(tenant_id, selected_flight.segKey)
        MCP-->>WORKER: seat_map for selected flight only
        WORKER->>AIF: Pass 2 — select best seat<br/>(selected_flight + seat_map + profile)
        AIF-->>WORKER: {selected_seat, seat_reasoning}
    else Messaging flow (flight_delayed)
        WORKER->>MCP: fetch_message_templates(tenant_id)
        MCP-->>WORKER: message template set
        WORKER->>AIF: Single pass — select group + personalize<br/>(profile + event + templates)
        AIF-->>WORKER: {selected_group_id, messages[SMS, WA, EMAIL]}
    end

    WORKER->>REDIS: SET result:{job_id} = {status:"completed", result, ttl:3600}
    WORKER->>REDIS: SET idempotency:{tenant_id}:{pnr} = {job_id, ttl:300}
    WORKER->>SB: Complete message (remove from queue)
    WORKER->>COSMOS: Append audit log entry

    Note over C: Client polls or receives webhook
    C->>APIM: GET /jobs/{job_id}
    APIM->>ORCH: Forward
    ORCH->>REDIS: GET result:{job_id}
    REDIS-->>ORCH: Full result
    ORCH-->>C: 200 OK {status:"completed", result}
```

---

## 4. Recovery Flow Redesign — Two-Pass AI

> Key architectural change: seat maps are fetched ONLY for the AI-selected flight. Eliminates redundant API calls for N-1 flights and reduces token usage by removing unused seat data from the agent context.

```mermaid
sequenceDiagram
    autonumber
    participant WORKER as worker-pool
    participant MCP as mcp-gateway
    participant REDIS as Redis Cache
    participant DIS as Disruption API
    participant CDP as CDP API
    participant ALN as Airline API
    participant AIF as Azure AI Foundry
    participant COSMOS as Cosmos DB

    Note over WORKER,COSMOS: Pre-condition: DisruptionJob dequeued from Service Bus<br/>event_type = flight_cancelled · passenger is eligible

    WORKER->>MCP: fetch_disruption_context(tenant_id, pnr, last_name)

    par Parallel fetch — Phase 1
        MCP->>DIS: GET /disruptions/{pnr}
        DIS-->>MCP: raw disruption event
    and
        MCP->>CDP: POST /cdp/user-lookup {last_name, email_or_phone}
        CDP-->>MCP: CDP profile (eligibility check)
    end

    MCP->>MCP: normalize_event(raw)<br/>verify last_name · check HIGHSPENDER/STUDENT

    par Parallel fetch — Phase 2 (only after eligibility confirmed)
        MCP->>CDP: POST /cdp/user-lookup (full profile)
        CDP-->>MCP: full CDP profile
    and
        MCP->>ALN: POST /flight-search {segKey}
        ALN-->>MCP: available_flights[] (N flights)
    end

    MCP-->>WORKER: {event, profile, available_flights[N]}

    Note over WORKER,AIF: PASS 1 — Flight Selection<br/>Context: event + CDP profile + available_flights<br/>NO seat map data (reduces token count significantly)

    WORKER->>REDIS: GET seatmap_cache:{tenant_id}:{segKey} (miss expected)
    WORKER->>AIF: Run Recovery Agent — Flight Selection Pass<br/>Input: profile + event + flights[N]<br/>temp=0.1 · structured output enforced via Pydantic

    AIF-->>WORKER: {selected_flight: {segKey, flight_number, ...}, flight_reasoning}

    Note over WORKER: Validate selected_flight against available_flights[]<br/>Reject hallucinated flights not in the list

    WORKER->>MCP: fetch_seat_map(tenant_id, selected_flight.segKey)

    WORKER->>REDIS: GET seatmap_cache:{tenant_id}:{selected_flight.segKey}
    alt Cache hit (seat map cached within 30min TTL)
        REDIS-->>WORKER: cached seat map
    else Cache miss
        MCP->>ALN: GET /seat-map/{selected_flight.segKey}
        ALN-->>MCP: raw seat map
        MCP->>MCP: parse + flatten seats (assignable only)
        MCP-->>WORKER: seat_map[]
        WORKER->>REDIS: SET seatmap_cache:{tenant_id}:{segKey} TTL=1800
    end

    Note over WORKER,AIF: PASS 2 — Seat Selection<br/>Context: selected_flight + seat_map + CDP profile<br/>Focused context — no flight list noise

    WORKER->>AIF: Run Recovery Agent — Seat Selection Pass<br/>Input: selected_flight + seat_map + profile<br/>temp=0.1 · structured output enforced

    AIF-->>WORKER: {selected_seat: {seat_number, travel_class, seat_type[]}, seat_reasoning}

    Note over WORKER: Validate selected_seat exists in seat_map[]<br/>Validate travel_class matches eligibility rules

    WORKER->>COSMOS: Append recovery audit log<br/>{tenant_id, pnr, selected_flight, selected_seat, ai_tokens_used}
    WORKER->>REDIS: SET result:{job_id} TTL=3600
```

**Key changes from AS-IS:**
- Seat maps fetched for **1 flight** (selected by AI) vs previously **N flights** (all available)
- Two separate AI passes with focused context windows
- Pydantic validation on AI output with hallucination guard (validates flight exists in list)
- Seat map cached in Redis (30-min TTL) — subsequent requests for same flight skip the Airline API
- AI token usage reduced proportionally to number of available flights (typically 5–10x reduction)

---

## 5. Messaging Flow Redesign

> Async delay notification flow. All external calls happen in the Worker. Single AI pass selects persona and personalizes all channel messages.

```mermaid
sequenceDiagram
    autonumber
    participant WORKER as worker-pool
    participant MCP as mcp-gateway
    participant REDIS as Redis Cache
    participant DIS as Disruption API
    participant CDP as CDP API
    participant COSMOS as Cosmos DB
    participant AIF as Azure AI Foundry

    Note over WORKER,AIF: Pre-condition: DisruptionJob dequeued<br/>event_type = flight_delayed

    par Parallel — Phase 1
        WORKER->>MCP: fetch_disruption_event(tenant_id, pnr)
        MCP->>DIS: GET /disruptions/{pnr}
        DIS-->>MCP: raw event
        MCP-->>WORKER: normalized event {delay_count, delay_duration_minutes, ...}
    and
        WORKER->>MCP: fetch_cdp_profile(tenant_id, last_name, contact)
        MCP->>CDP: POST /cdp/user-lookup
        CDP-->>MCP: CDP profile
        MCP-->>WORKER: profile {student, doctor, defense, highspender, ...}
    and
        WORKER->>COSMOS: fetch_active_template(tenant_id)
        COSMOS-->>WORKER: message template set {MESSAGES[group_id, delay_count, channels[]]}
    end

    WORKER->>REDIS: GET agent_config:{tenant_id}
    alt Config cached (TTL=60s)
        REDIS-->>WORKER: {message_agent_id, system_prompt, user_prompt}
    else Cache miss
        WORKER->>COSMOS: fetch_active_agents(tenant_id) + fetch_active_prompt(tenant_id)
        COSMOS-->>WORKER: agent config
        WORKER->>REDIS: SET agent_config:{tenant_id} TTL=60
    end

    Note over WORKER,AIF: SINGLE PASS — Persona detection + Message selection + Personalization<br/>Priority: DEFENCE > DOCTOR > STUDENT > GENERAL<br/>Replace placeholder vars: var1=origin, var2=destination

    WORKER->>AIF: Run Messaging Agent<br/>Input: event + profile + templates + delay_count<br/>temp=0.1 · structured output: {selected_group_id, reason, messages[]}

    AIF-->>WORKER: {selected_group_id, reason, messages: [{id, channel, message} x3]}

    WORKER->>WORKER: Validate output — verify group_id exists in template set<br/>Verify all 3 channels (SMS, WA, EMAIL) present

    WORKER->>COSMOS: Append messaging audit log<br/>{tenant_id, pnr, persona, selected_group_id, ai_tokens_used}
    WORKER->>REDIS: SET result:{job_id} TTL=3600
    WORKER->>REDIS: SET idempotency:{tenant_id}:{pnr} TTL=300
```

---

## 6. Redis + Service Bus Architecture

> Detailed view of the cache key schema, Service Bus topology, and job state machine.

```mermaid
flowchart TB
    subgraph SB_TOPOLOGY["AZURE SERVICE BUS TOPOLOGY"]
        direction LR
        subgraph QUEUES["Queues"]
            Q_MAIN["disruption-requests\n(main queue)\nMax delivery: 5\nLock: 5min\nTTL: 7 days"]
            Q_RETRY["disruption-retry\n(delayed retry queue)\nBackoff: 30s · 2min · 10min\nMax retries: 3"]
            Q_DLQ["disruption-requests/$DeadLetterQueue\n(DLQ)\nManual investigation\nAlert on depth > 10"]
        end
        subgraph TOPICS["Topics (future)"]
            T_COMPLETE["disruption-completed\n(completion events)\nSubscriptions: webhook-relay · analytics"]
        end
        Q_MAIN -->|"delivery count > 5"| Q_DLQ
        Q_MAIN -->|"transient failure"| Q_RETRY
        Q_RETRY -->|"after backoff delay"| Q_MAIN
    end

    subgraph REDIS_SCHEMA["REDIS KEY SCHEMA"]
        direction TB
        subgraph IDEM["Idempotency (TTL: 300s)"]
            K_IDEM["idem:{tenant_id}:{pnr}\n→ {job_id, cached_at}"]
        end
        subgraph JOBS["Job State (TTL: 3600s)"]
            K_JOB["job:{job_id}\n→ {status, tenant_id, pnr, created_at}\nstatus: queued → processing → completed | failed"]
        end
        subgraph RESULTS["Results (TTL: 3600s)"]
            K_RESULT["result:{job_id}\n→ full response JSON"]
        end
        subgraph CACHES["API Response Caches"]
            K_PNR["pnr:{tenant_id}:{pnr}\n→ normalized disruption event (TTL: 300s)"]
            K_SEATMAP["seatmap:{tenant_id}:{segKey}\n→ parsed seat list (TTL: 1800s)"]
            K_AGCFG["agent_config:{tenant_id}\n→ {agent_ids, prompts} (TTL: 60s)"]
            K_TENANT["tenant_cfg:{tenant_id}\n→ {flight_api, cdp_api, disrupt_api} (TTL: 300s)"]
        end
        subgraph RATE["Rate Limiting (sliding window)"]
            K_RATE["rl:{tenant_id}:{window_ts}\n→ request count\nEvaluated by Lua script in APIM"]
        end
    end

    subgraph JOB_STATE["JOB STATE MACHINE"]
        S_QUEUED["QUEUED\n(published to SB)"]
        S_PROC["PROCESSING\n(worker picked up)"]
        S_COMP["COMPLETED\n(result in Redis)"]
        S_FAIL["FAILED\n(moved to DLQ)"]
        S_CACHED["SERVED FROM CACHE\n(idempotency hit)"]

        S_QUEUED --> S_PROC
        S_PROC --> S_COMP
        S_PROC -->|"transient error\n< 5 attempts"| S_QUEUED
        S_PROC -->|"permanent error\nor > 5 attempts"| S_FAIL
        S_CACHED -..->|"bypasses all"| S_COMP
    end
```

---

## 7. AI Orchestration Architecture

> Per-tenant agent isolation, structured output validation, two-pass execution, fallback, and AI audit logging.

```mermaid
flowchart TB
    subgraph RUNTIME["WORKER-POOL RUNTIME"]
        DISPATCHER["Job Dispatcher\n(Service Bus consumer)"]
        FLOW_ROUTER["Flow Router\nrecovery | messaging | diverted"]
        PROMPT_BUILDER["Prompt Builder\nbase + admin_system + admin_user injection"]
        OUTPUT_VAL["Structured Output Validator\nPydantic model enforcement\nhallucination guard"]
        AI_CLIENT["Azure AI Projects Client\n(per-request thread, no reuse)"]
    end

    subgraph AI_FOUNDRY["AZURE AI FOUNDRY (per-tenant agents)"]
        subgraph TENANT_A["Tenant: indigo_mock"]
            RA_A["Recovery Agent\nasst_recovery_indigo\nGPT-5.1 · temp=0.1"]
            MA_A["Messaging Agent\nasst_messaging_indigo\nGPT-5.1 · temp=0.1"]
        end
        subgraph TENANT_B["Tenant: airline_mock"]
            RA_B["Recovery Agent\nasst_recovery_airline\nGPT-5.1 · temp=0.1"]
            MA_B["Messaging Agent\nasst_messaging_airline\nGPT-5.1 · temp=0.1"]
        end
        subgraph SHARED["Shared Infrastructure"]
            MODEL["GPT-5.1 Deployment\n(shared model, isolated threads)"]
            THREADS["Thread isolation\n(new thread per request)"]
        end
    end

    subgraph COSMOS_AI["COSMOS DB — AGENT REGISTRY"]
        AGENT_DOC["agents collection\n{tenant_id, agent_id,\nis_active_recovery,\nis_active_messaging}\nHot-swap: flip flag, no restart"]
        PROMPT_DOC["prompts collection\n{tenant_id, type,\nsystem_prompt, user_prompt,\nis_active}\nAdmin-managed at runtime"]
    end

    subgraph VALIDATION["OUTPUT VALIDATION PIPELINE"]
        FENCE_STRIP["safe_json_from_agent()\nStrip markdown code fences"]
        PYDANTIC["Pydantic Model Validation\nRecoveryOutput | MessagingOutput"]
        HALL_GUARD["Hallucination Guard\nValidate selected_flight in available_flights[]\nValidate selected_seat in seat_map[]\nValidate group_id in templates"]
        FALLBACK["Fallback Handler\nRetry with simplified prompt\nMax 2 retries before DLQ"]
    end

    subgraph AUDIT_AI["AI AUDIT LOG (Cosmos DB)"]
        AI_LOG["ai_audit_logs collection\n{tenant_id, job_id, flow,\nagent_id, input_tokens,\noutput_tokens, latency_ms,\nmodel_version, result_valid}"]
    end

    DISPATCHER --> FLOW_ROUTER
    FLOW_ROUTER --> PROMPT_BUILDER
    PROMPT_BUILDER --> AI_CLIENT

    AGENT_DOC -..->|"resolve agent_id\n(per request)"| FLOW_ROUTER
    PROMPT_DOC -..->|"inject admin prompts"| PROMPT_BUILDER

    AI_CLIENT --> RA_A & MA_A & RA_B & MA_B
    RA_A & MA_A & RA_B & MA_B --> MODEL
    MODEL --> THREADS

    AI_CLIENT --> FENCE_STRIP --> PYDANTIC --> HALL_GUARD
    HALL_GUARD -->|"invalid"| FALLBACK
    FALLBACK -->|"retry"| AI_CLIENT
    HALL_GUARD -->|"valid"| AI_LOG
```

---

## 8. Security Architecture

> Trust boundaries, network isolation, identity, secrets management, and audit.

```mermaid
flowchart TB
    subgraph TRUST_0["TRUST BOUNDARY: PUBLIC INTERNET"]
        INTERNET_USERS["External Users\n& Webhook Sources"]
    end

    subgraph TRUST_1["TRUST BOUNDARY: AZURE EDGE (TLS termination + WAF)"]
        AFD_SEC["Azure Front Door\nWAF: OWASP rules\nDDoS Protection Standard\nGeo-filtering\nBot management"]
        APIM_SEC["Azure API Management\nJWT validation (Azure AD / tenant IdP)\nSubscription key validation\nOAuth2 scope enforcement\nRate limit: 1000 req/min per tenant\nTLS 1.3 only\nIP filtering (admin endpoints)"]
    end

    subgraph TRUST_2["TRUST BOUNDARY: CONTAINER APPS ENVIRONMENT (private VNET)"]
        direction TB
        subgraph INGRESS_CA["Public Ingress Container Apps"]
            ORCH_SEC["api-orchestrator\nManaged Identity: reads KV, Cosmos, Redis, SB\nNo outbound to internet\nAll secrets via KV references"]
            ADMIN_SEC["api-admin\nManaged Identity: reads/writes Cosmos\nAudit log on every write\nIP restricted: admin CIDR only"]
            EVTP_SEC["event-processor\nManaged Identity: publishes to SB\nHMAC signature validation on webhooks"]
        end
        subgraph INTERNAL_CA["Internal-Only Container Apps (no public ingress)"]
            MCP_SEC["mcp-gateway\nInternal traffic only\nManaged Identity: reads KV for tenant API keys\nMutual TLS optional\nCIDR restricted: ACA env subnet only"]
            WORKER_SEC["worker-pool\nInternal + SB trigger\nManaged Identity: SB, KV, Cosmos, Redis, AI Foundry\nNo direct internet egress\nAll external calls via mcp-gateway"]
        end
    end

    subgraph TRUST_3["TRUST BOUNDARY: AZURE DATA SERVICES (private endpoints)"]
        direction LR
        COSMOS_SEC["Azure Cosmos DB\nPrivate endpoint\nRBAC: Managed Identity only\nNo connection strings\nData encrypted at rest (AES-256)\nDocument-level tenant_id filter\nAudit logging to Log Analytics"]
        SB_SEC["Azure Service Bus\nPrivate endpoint\nSAS policy: send-only (orchestrator)\nSAS policy: receive-only (workers)\nMSI auth preferred over SAS"]
        REDIS_SEC["Azure Cache for Redis\nPrivate endpoint\nTLS in transit\nMSI auth (Azure AD authentication)\nKey namespacing: {tenant_id}:*"]
        KV_SEC["Azure Key Vault\nPrivate endpoint\nRBAC: Key Vault Secrets User (Container Apps)\nKey Vault Secrets Officer (CI/CD pipeline only)\nSoft delete + purge protection\nAudit log: every access"]
        AIF_SEC["Azure AI Foundry\nPrivate endpoint (optional)\nMSI auth from worker-pool\nPer-tenant agent isolation\nAI audit log: all runs"]
    end

    subgraph IDENTITY["AZURE MANAGED IDENTITY & RBAC"]
        MI_ORCH["api-orchestrator MI\nRoles: Cosmos DB Reader\nService Bus Sender\nRedis Cache Contributor\nKey Vault Secrets User"]
        MI_WORKER["worker-pool MI\nRoles: Cosmos DB Reader/Writer\nService Bus Receiver\nRedis Cache Contributor\nKey Vault Secrets User\nAI Developer (AI Foundry)"]
        MI_MCP["mcp-gateway MI\nRoles: Cosmos DB Reader\nKey Vault Secrets User"]
        MI_ADMIN["api-admin MI\nRoles: Cosmos DB Reader/Writer\nKey Vault Secrets User\nAudit log writer"]
    end

    INTERNET_USERS --> AFD_SEC --> APIM_SEC
    APIM_SEC --> ORCH_SEC & ADMIN_SEC & EVTP_SEC
    ORCH_SEC & EVTP_SEC --> SB_SEC
    WORKER_SEC --> SB_SEC & MCP_SEC & AIF_SEC & REDIS_SEC & COSMOS_SEC
    ORCH_SEC --> REDIS_SEC & COSMOS_SEC
    MCP_SEC --> KV_SEC & COSMOS_SEC
    ADMIN_SEC --> COSMOS_SEC

    MI_ORCH -..-> KV_SEC
    MI_WORKER -..-> KV_SEC
    MI_MCP -..-> KV_SEC
    MI_ADMIN -..-> KV_SEC
```

---

## 9. Observability Architecture

> End-to-end distributed tracing, AI-specific telemetry, metrics, alerting, and operational dashboards.

```mermaid
flowchart TB
    subgraph TRACE_PROPAGATION["DISTRIBUTED TRACE PROPAGATION"]
        direction LR
        T1["APIM\nGenerate X-Trace-ID\nX-Tenant-ID header\nAdd to all responses"]
        T2["api-orchestrator\nAttach trace_id to SB message\nLog {tenant_id, pnr, job_id, trace_id}"]
        T3["Service Bus message\ntrace_id in ApplicationProperties"]
        T4["worker-pool\nContinue trace span\nLog {job_id, flow, worker_id}"]
        T5["mcp-gateway\nChild span per external API call\nLog {api_name, latency_ms, status}"]
        T6["AI Foundry\nAI span: {agent_id, model, tokens_in, tokens_out, latency_ms}"]
        T1 --> T2 --> T3 --> T4 --> T5
        T4 --> T6
    end

    subgraph METRICS["AZURE MONITOR METRICS (Custom + Platform)"]
        direction TB
        subgraph LATENCY["Latency Metrics"]
            M1["disruption_job_e2e_ms\np50 · p95 · p99 per tenant"]
            M2["ai_agent_latency_ms\nper agent_id · per flow"]
            M3["mcp_external_api_latency_ms\nper api_name · per tenant"]
        end
        subgraph THROUGHPUT["Throughput Metrics"]
            M4["disruption_jobs_created_total\ntenant · flow · hour"]
            M5["service_bus_active_messages\nqueue depth per queue"]
            M6["redis_cache_hit_rate\nper cache key type"]
        end
        subgraph AI_METRICS["AI Telemetry"]
            M7["ai_tokens_consumed_total\ntenant · agent · model"]
            M8["ai_structured_output_valid_rate\nper flow (hallucination guard pass rate)"]
            M9["ai_retry_rate\n(fallback handler trigger rate)"]
        end
        subgraph ERROR_METRICS["Error & Health Metrics"]
            M10["dlq_message_depth\n(alert: depth > 10)"]
            M11["worker_error_rate\nper error type"]
            M12["external_api_error_rate\nper tenant · per api"]
        end
    end

    subgraph ALERTS["AZURE MONITOR ALERT RULES"]
        A1["CRITICAL: DLQ depth > 10\nPage on-call immediately"]
        A2["HIGH: e2e latency p95 > 30s\nPage on-call"]
        A3["HIGH: AI retry rate > 5%\nSlack notification"]
        A4["MEDIUM: Redis hit rate < 40%\nInvestigation ticket"]
        A5["LOW: External API error rate > 1%\nSlack notification"]
    end

    subgraph DASHBOARDS["AZURE DASHBOARDS"]
        D1["Ops Dashboard\n- Live job throughput\n- Queue depths\n- Error rates\n- p95 latency heatmap"]
        D2["AI Telemetry Dashboard\n- Token usage per tenant/day\n- Agent latency distribution\n- Output validity rate\n- Cost attribution"]
        D3["Tenant Health Dashboard\n- Per-tenant job success rate\n- Per-tenant SLA compliance\n- External API health per tenant"]
    end

    subgraph LOG_ANALYTICS["LOG ANALYTICS WORKSPACE"]
        LAW_Q1["Query: Failed jobs in last 1h\nby tenant + error_code"]
        LAW_Q2["Query: AI token usage by tenant/week\nfor cost allocation"]
        LAW_Q3["Query: Slow external API calls\n> 5s response time"]
    end

    TRACE_PROPAGATION --> METRICS
    METRICS --> ALERTS
    METRICS --> DASHBOARDS
    METRICS --> LOG_ANALYTICS
```

---

## 10. Container Apps Deployment Topology

> Explicit mapping of every Container App, its resources, scaling rules, and network configuration.

```mermaid
flowchart TB
    subgraph ACR_REGISTRY["AZURE CONTAINER REGISTRY\nPrivate · Geo-replicated"]
        IMG1["prod-disruption/api-orchestrator:v2.x"]
        IMG2["prod-disruption/mcp-gateway:v2.x"]
        IMG3["prod-disruption/worker-pool:v2.x"]
        IMG4["prod-disruption/api-admin:v2.x"]
        IMG5["prod-disruption/event-processor:v2.x"]
    end

    subgraph ACA_ENV["AZURE CONTAINER APPS ENVIRONMENT\nName: prod-disruption-env\nRegion: East US 2\nInfrastructure: Dedicated · Workload Profile: D4\nVNET: prod-disruption-vnet · Subnet: aca-subnet\nInternal load balancer only (except APIM targets)"]
        direction TB

        subgraph CA1["Container App: api-orchestrator"]
            CA1_IMG["Image: api-orchestrator:v2.x"]
            CA1_ING["Ingress: External (HTTPS:443)\nExposed via APIM only"]
            CA1_SCALE["Scale: min=2 max=20\nRule: HTTP concurrent requests > 50"]
            CA1_ENV["Env vars: Key Vault refs\nCOSMOS_URL · REDIS_URL · SB_CONNECTION\nMIN_REPLICAS · MAX_REPLICAS"]
            CA1_PROBE["Liveness: GET /health (5s)\nReadiness: GET /ready (3s)"]
        end

        subgraph CA2["Container App: mcp-gateway"]
            CA2_IMG["Image: mcp-gateway:v2.x"]
            CA2_ING["Ingress: Internal only\nNot reachable from internet"]
            CA2_SCALE["Scale: min=2 max=10\nRule: CPU > 60% OR HTTP concurrent > 30"]
            CA2_ENV["Env vars: Key Vault refs\nCOSMOS_URL · TENANT_API_KEYS_KV_PREFIX\nHTTP_TIMEOUT=30s · MAX_CONNECTIONS=100"]
            CA2_PROBE["Liveness: GET /health (5s)\nReadiness: GET /ready (3s)"]
        end

        subgraph CA3["Container App: worker-pool"]
            CA3_IMG["Image: worker-pool:v2.x"]
            CA3_ING["Ingress: None (Service Bus trigger only)"]
            CA3_SCALE["Scale: min=1 max=50\nRule: Service Bus queue depth\nAdd 1 replica per 5 messages (KEDA)"]
            CA3_ENV["Env vars: Key Vault refs\nSB_CONNECTION · AIF_ENDPOINT\nCOSMOS_URL · REDIS_URL\nWORKER_CONCURRENCY=10"]
            CA3_PROBE["Startup: 60s grace (agent init)\nLiveness: internal heartbeat"]
        end

        subgraph CA4["Container App: api-admin"]
            CA4_IMG["Image: api-admin:v2.x"]
            CA4_ING["Ingress: External\nIP restricted to admin CIDR in APIM policy"]
            CA4_SCALE["Scale: min=1 max=5\nRule: HTTP concurrent > 20"]
            CA4_ENV["Env vars: Key Vault refs\nCOSMOS_URL · AUDIT_LOG_ENABLED=true"]
            CA4_PROBE["Liveness: GET /health (5s)"]
        end

        subgraph CA5["Container App: event-processor"]
            CA5_IMG["Image: event-processor:v2.x"]
            CA5_ING["Ingress: External (HTTPS:443)\nWebhook endpoint — HMAC validated"]
            CA5_SCALE["Scale: min=1 max=10\nRule: HTTP concurrent > 20"]
            CA5_ENV["Env vars: Key Vault refs\nSB_CONNECTION · WEBHOOK_SECRET_KV_REF"]
            CA5_PROBE["Liveness: GET /health (5s)"]
        end
    end

    subgraph SWA["AZURE STATIC WEB APPS\nPassenger SPA + Admin SPA\nGlobal CDN · Custom domain · SSL\nAPI routes proxied to APIM"]
    end

    subgraph SHARED_SERVICES["SHARED AZURE SERVICES (all connected via Private Endpoints)"]
        direction LR
        SVC_COSMOS["Azure Cosmos DB\nAccount: prod-disruption-cosmos\nConsistency: Session\nPartition: tenant_id"]
        SVC_REDIS["Azure Cache for Redis\nSKU: Premium P1\nGeo-replication: enabled\nCluster: 2 shards"]
        SVC_SB["Azure Service Bus\nNamespace: prod-disruption-sb\nSKU: Premium (VNET support)\nZone redundant: true"]
        SVC_KV["Azure Key Vault\nSKU: Premium (HSM)\nPurge protection: enabled\nSoft delete: 90 days"]
        SVC_AIF["Azure AI Foundry\nProject: proj-disruption-prod\nModel: GPT-5.1\nPer-tenant agent IDs"]
    end

    ACR_REGISTRY --> ACA_ENV
    CA1 & CA2 & CA3 & CA4 & CA5 --> SVC_COSMOS
    CA1 & CA3 --> SVC_REDIS
    CA1 & CA5 --> SVC_SB
    CA3 --> SVC_SB
    CA1 & CA2 & CA3 & CA4 --> SVC_KV
    CA3 --> SVC_AIF
```

---

## 11. Multi-Region Architecture

> Active-active deployment across two Azure regions with Azure Front Door for global traffic routing, failover, and latency-based routing.

```mermaid
flowchart TB
    subgraph GLOBAL_EDGE["AZURE FRONT DOOR (Global)"]
        direction LR
        AFD_GLOBAL["Azure Front Door Premium\nGlobal load balancing\nHealth probes: /health every 10s\nLatency-based routing\nFailover: < 60s RTO\nWAF: global policy"]
    end

    subgraph REGION_PRIMARY["PRIMARY REGION: East US 2"]
        direction TB
        APIM_P["Azure API Management\n(Premium tier · Zone redundant)"]
        ACA_P["Container Apps Environment\napi-orchestrator · mcp-gateway\nworker-pool · api-admin\nevent-processor\n(Dedicated · VNET integrated)"]
        subgraph DATA_P["Data Layer — Primary"]
            COSMOS_P["Cosmos DB\n(write region: East US 2)\nStrong consistency"]
            REDIS_P["Redis Cache\nPrimary shard"]
            SB_P["Service Bus\nPrimary namespace\nZone redundant"]
        end
        AIF_P["AI Foundry\nEast US 2 endpoint"]
    end

    subgraph REGION_SECONDARY["SECONDARY REGION: West Europe"]
        direction TB
        APIM_S["Azure API Management\n(Secondary unit)"]
        ACA_S["Container Apps Environment\napi-orchestrator · mcp-gateway\nworker-pool · api-admin\nevent-processor\n(Dedicated · VNET integrated)"]
        subgraph DATA_S["Data Layer — Secondary"]
            COSMOS_S["Cosmos DB\n(read/write replica: West Europe)\nEventual consistency for reads"]
            REDIS_S["Redis Cache\nGeo-replicated from primary"]
            SB_S["Service Bus\nGeo-DR paired namespace"]
        end
        AIF_S["AI Foundry\nWest Europe endpoint"]
    end

    subgraph SWA_GLOBAL["Azure Static Web Apps (Global CDN)"]
        SWA_GLOB["Frontend\nGlobal CDN distribution\nAutomatic failover"]
    end

    USERS["Global Users"] --> AFD_GLOBAL
    AFD_GLOBAL --> APIM_P
    AFD_GLOBAL --> APIM_S
    AFD_GLOBAL --> SWA_GLOBAL

    APIM_P --> ACA_P
    ACA_P --> DATA_P
    ACA_P --> AIF_P

    APIM_S --> ACA_S
    ACA_S --> DATA_S
    ACA_S --> AIF_S

    COSMOS_P <-->|"Multi-region replication\n(async)"| COSMOS_S
    REDIS_P <-->|"Geo-replication"| REDIS_S
    SB_P <-->|"Geo-DR pairing"| SB_S
```

**RTO/RPO targets:**
- RTO: < 60 seconds (Front Door health probe failover)
- RPO: < 5 seconds (Cosmos DB async replication lag)
- Reads: served from nearest region (eventual consistency acceptable for config reads)
- Writes: Primary region only for job creation; secondary can serve reads and polling

---

## 12. Multi-Tenant Isolation Architecture

> How tenant isolation is enforced at every layer of the platform.

```mermaid
flowchart LR
    subgraph TENANT_A["TENANT: indigo_mock"]
        direction TB
        TA_JWT["JWT: tenant_id=indigo_mock\n(from Azure AD B2C / tenant IdP)"]
        TA_APIM["APIM Policy\nInject X-Tenant-ID: indigo_mock\nRate limit: 500 req/min\nQuota: 10,000 req/day"]
        TA_REDIS["Redis namespace\nidem:indigo_mock:*\njob:indigo_mock:*\nseatmap:indigo_mock:*"]
        TA_SB["Service Bus message\nApplicationProperties:\ntenant_id=indigo_mock"]
        TA_COSMOS["Cosmos DB partition\npartitionKey=indigo_mock\nAll queries scoped to partition"]
        TA_AGENTS["AI Foundry\nasst_recovery_indigo\nasst_messaging_indigo"]
        TA_EXT["External APIs\nflight_api: indigo endpoint\ncdp_api: indigo endpoint\ndisruption_api: indigo endpoint"]
    end

    subgraph TENANT_B["TENANT: airline_mock"]
        direction TB
        TB_JWT["JWT: tenant_id=airline_mock"]
        TB_APIM["APIM Policy\nInject X-Tenant-ID: airline_mock\nRate limit: 200 req/min\nQuota: 5,000 req/day"]
        TB_REDIS["Redis namespace\nidem:airline_mock:*\njob:airline_mock:*\nseatmap:airline_mock:*"]
        TB_SB["Service Bus message\nApplicationProperties:\ntenant_id=airline_mock"]
        TB_COSMOS["Cosmos DB partition\npartitionKey=airline_mock"]
        TB_AGENTS["AI Foundry\nasst_recovery_airline\nasst_messaging_airline"]
        TB_EXT["External APIs\nflight_api: airline endpoint\ncdp_api: airline endpoint\ndisruption_api: airline endpoint"]
    end

    subgraph SHARED_INFRA["SHARED INFRASTRUCTURE (logically isolated)"]
        APIM_SHARED["Azure API Management\n(shared, isolated by policy)"]
        ACA_SHARED["Container Apps\n(shared env, isolated by code)"]
        COSMOS_SHARED["Cosmos DB\n(shared account, isolated by partition)"]
        REDIS_SHARED["Redis\n(shared cluster, isolated by key prefix)"]
        SB_SHARED["Service Bus\n(shared namespace, isolated by message properties)"]
        AIF_SHARED["AI Foundry\n(shared project, isolated by agent_id)"]
    end

    TA_JWT --> TA_APIM --> APIM_SHARED
    TA_REDIS & TB_REDIS --> REDIS_SHARED
    TA_SB & TB_SB --> SB_SHARED
    TA_COSMOS & TB_COSMOS --> COSMOS_SHARED
    TA_AGENTS & TB_AGENTS --> AIF_SHARED
    TA_APIM & TB_APIM --> ACA_SHARED

    TB_JWT --> TB_APIM --> APIM_SHARED
```

**Isolation matrix:**

| Layer | Mechanism | Shared? |
|---|---|---|
| API Management | JWT claim `tenant_id`, rate limit per tenant | Shared gateway, isolated policies |
| Container Apps | `X-Tenant-ID` header, no shared state | Shared process, isolated execution context |
| Redis | Key prefix `{tenant_id}:*` | Shared cluster |
| Service Bus | `tenant_id` in ApplicationProperties | Shared namespace |
| Cosmos DB | Partition key = `tenant_id` | Shared account, isolated partition |
| AI Foundry | Per-tenant `agent_id` resolved at runtime | Shared model deployment, isolated threads |
| External APIs | Per-tenant base URLs from Cosmos DB | Fully isolated |

---

## 13. DevOps / CI-CD Architecture

> GitHub Actions pipeline, container image lifecycle, blue-green deployments, and secret rotation.

```mermaid
flowchart LR
    subgraph SOURCE["SOURCE CONTROL"]
        GITHUB["GitHub Repository\nprod-disruption/platform\nBranch strategy:\nmain · develop · feature/*\nrelease/*"]
        GH_PR["Pull Request\nRequired reviewers: 2\nStatus checks: lint · test · security scan"]
    end

    subgraph CI["CONTINUOUS INTEGRATION (GitHub Actions)"]
        direction TB
        CI_LINT["Lint & Format\nruff · black · mypy"]
        CI_TEST["Unit & Integration Tests\npytest · coverage > 80%\nMock Service Bus + Redis"]
        CI_SECURITY["Security Scan\nbandit · pip-audit\nGitHub Advanced Security\nSecret detection"]
        CI_BUILD["Docker Build\nMulti-stage build\nNon-root user\nMinimal base image"]
        CI_PUSH["Push to ACR\nprod-disruption.azurecr.io\nTag: {service}:{sha} + {service}:latest"]
        CI_SCAN["Container Scan\nMicrosoft Defender for Containers\nBlock on HIGH/CRITICAL CVE"]
    end

    subgraph CD_STAGING["CD — STAGING"]
        STG_DEPLOY["Deploy to Staging\nContainer App revision\naz containerapp update --image"]
        STG_SMOKE["Smoke Tests\nPOST /disruption (mock tenant)\nGET /health (all 5 apps)\nRedis connectivity\nService Bus connectivity"]
        STG_APPROVE["Manual Approval Gate\nRequired: 1 approver\n(GitHub Environment Protection)"]
    end

    subgraph CD_PROD["CD — PRODUCTION (Blue-Green)"]
        PROD_REVISION["New Container App Revision\n(inactive, 0% traffic)"]
        PROD_VERIFY["Revision Smoke Test\nInternal health probe\nGET /health → 200"]
        PROD_SHIFT["Traffic Split\n10% → 50% → 100%\nMonitor error rate at each step\nAuto-rollback if error rate > 1%"]
        PROD_CLEANUP["Deactivate Old Revision\nAfter 30 min 100% traffic\nKeep 1 previous for rollback"]
    end

    subgraph SECRET_ROT["SECRET ROTATION"]
        KV_ROT["Key Vault Rotation Policy\nCosmos DB key: 90 days\nRedis key: 90 days\nSB SAS: 30 days\n(MSI preferred, SAS as fallback)"]
        CA_RELOAD["Container App auto-reload\nKV secret version pinned\nApp restart on new version"]
    end

    subgraph OBS_CD["DEPLOYMENT OBSERVABILITY"]
        DEPLOY_MARKER["Deployment Annotation\nApplication Insights\nMarks release in metrics graphs"]
        DEPLOY_ALERT["Post-deploy Alert Silence\n15 min grace period\nThen re-enable all alerts"]
    end

    GITHUB --> GH_PR --> CI_LINT
    CI_LINT --> CI_TEST --> CI_SECURITY --> CI_BUILD --> CI_PUSH --> CI_SCAN
    CI_SCAN --> STG_DEPLOY --> STG_SMOKE --> STG_APPROVE
    STG_APPROVE --> PROD_REVISION --> PROD_VERIFY --> PROD_SHIFT --> PROD_CLEANUP
    PROD_SHIFT --> DEPLOY_MARKER --> DEPLOY_ALERT
    KV_ROT --> CA_RELOAD
```

---

## Azure SaaS Accelerator Integration

> Tenant lifecycle managed through Azure Marketplace and the SaaS Accelerator. Each airline onboards as a SaaS subscriber.

```mermaid
flowchart TD
    subgraph MARKETPLACE["AZURE MARKETPLACE"]
        OFFER["prodDisruption SaaS Offer\nPlans: Starter · Professional · Enterprise\n(priced per disruption event volume)"]
        SUBSCRIBE["Airline subscribes\n(Azure portal or direct)"]
    end

    subgraph SAAS_ACC["AZURE SAAS ACCELERATOR"]
        LANDING["Landing Page\n(Azure Static Web Apps)\nCollect airline admin details\nRedirect from Marketplace"]
        WEBHOOK_ACC["SaaS Accelerator Webhook\nSubscription events:\nSubscribe · Suspend · Unsubscribe\nPlanChange"]
        FULFILL["Fulfillment API\nActivate subscription\nProvision tenant record in Cosmos DB\nGenerate tenant API credentials"]
        METERING["Azure Metering API\nReport usage:\ndisruption_events_processed per tenant\nBilled hourly"]
    end

    subgraph TENANT_ONBOARD["TENANT PROVISIONING WORKFLOW (api-admin)"]
        PROV_TENANT["Create tenant record\nCosmos DB: tenants collection\n{tenant_id, plan, flight_api, cdp_api, disruption_api}"]
        PROV_AGENTS["Register Azure AI Agents\nCreate Recovery + Messaging agents\nStore agent_ids in agents collection"]
        PROV_PROMPTS["Initialize default prompts\nClone base prompts to tenant\nStore in prompts collection"]
        PROV_TEMPLATES["Initialize message templates\nClone default templates to tenant\nStore in templates collection"]
        PROV_KEYS["Store tenant API keys\nKey Vault: secrets/tenant/{tenant_id}/*\nManaged Identity access grants"]
        PROV_NOTIFY["Notify tenant admin\nSend onboarding email\nProvide API credentials"]
    end

    subgraph ONGOING["ONGOING LIFECYCLE"]
        PLAN_CHANGE["Plan Upgrade/Downgrade\nUpdate rate limits in APIM\nUpdate quota in tenant config"]
        SUSPEND["Suspension\nDisable tenant in APIM\nQueue drain: complete in-flight jobs\nPreserve data 30 days"]
        OFFBOARD["Offboarding\nDelete tenant record\nRevoke Key Vault grants\nDelete AI agents\nExport audit logs to tenant"]
    end

    OFFER --> SUBSCRIBE --> LANDING
    LANDING --> WEBHOOK_ACC --> FULFILL
    FULFILL --> PROV_TENANT --> PROV_AGENTS --> PROV_PROMPTS --> PROV_TEMPLATES --> PROV_KEYS --> PROV_NOTIFY
    FULFILL --> METERING

    WEBHOOK_ACC --> PLAN_CHANGE
    WEBHOOK_ACC --> SUSPEND
    WEBHOOK_ACC --> OFFBOARD
```

---

## Migration Path: AS-IS → TO-BE

| Phase | Scope | Key Change |
|---|---|---|
| **Phase 1** (2 weeks) | Fix critical bugs | BUG-001 `tenant_id` in `validate_request()`, BUG-002 `httpx` in requirements |
| **Phase 2** (4 weeks) | Containerize + ACR | Dockerfiles for all 5 services, push to ACR, deploy to ACA dev env |
| **Phase 3** (6 weeks) | Async refactor | Add Service Bus, decouple AI execution from hot path, implement job polling |
| **Phase 4** (4 weeks) | Redis layer | Idempotency, seat map cache, agent config cache, rate limiting |
| **Phase 5** (4 weeks) | Security hardening | Managed Identity, Key Vault, private endpoints, remove all hardcoded secrets |
| **Phase 6** (4 weeks) | Observability | App Insights correlation, custom metrics, dashboards, alerts |
| **Phase 7** (6 weeks) | Two-pass AI + validation | Recovery flow redesign, Pydantic validation, hallucination guard |
| **Phase 8** (4 weeks) | Multi-tenancy | Per-tenant agents, Redis namespacing, Cosmos DB partition validation |
| **Phase 9** (6 weeks) | SaaS Accelerator | Marketplace offer, tenant onboarding automation, metering |
| **Phase 10** (4 weeks) | Multi-region | Secondary region deployment, Front Door routing, geo-replication |
