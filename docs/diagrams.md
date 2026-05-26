# Architecture & Flow Diagrams — prodDisruption

**Excalidraw (interactive, shareable):** https://excalidraw.com/#json=5XivTgCo7aPz01NUBRe9U,Wo7f9Dag1owT5gMlGE0sIw

Six diagrams, two levels of detail:
- **[1–2]** System architecture — stakeholder overview + full technical map
- **[3–4]** Request flows — Recovery (cancelled) + Messaging (delayed)
- **[5]** Error & edge-case paths — all non-success branches
- **[6]** Admin config flow — how the admin panel hot-swaps agents

---

## 1. High-Level System Architecture (Stakeholder View)

> What the system does and which external systems it touches.

```mermaid
graph TB
    FE["🖥️ Frontend / Client App"]
    API["⚡ FastAPI Server<br/><b>api_prod.py</b><br/>:8000 — REST API"]
    MCP["🔧 MCP Server<br/><b>server.py</b><br/>:8004 — Data Orchestration"]
    AIF["🤖 Azure AI Foundry<br/>Recovery Agent<br/>Messaging Agent"]
    CDB[("🗄️ Azure Cosmos DB<br/>flight_operations")]
    DA["✈️ Disruption API<br/>(per tenant)"]
    CDP["👤 CDP API<br/>(per tenant)"]
    ALN["🛫 Airline API<br/>(per tenant)"]
    ADM["🔐 Admin Panel<br/>(external)"]

    FE -->|"POST /disruption<br/>{pnr, last_name, tenant_id}"| API
    API -->|"JSON-RPC 2.0<br/>localhost:8004/mcp"| MCP
    API -->|"Azure AI SDK<br/>run agent + poll"| AIF
    API -->|"read agents,<br/>prompts"| CDB

    MCP -->|"GET /disruptions/{pnr}"| DA
    MCP -->|"POST /cdp/user-lookup"| CDP
    MCP -->|"POST /flight-search<br/>GET /seat-map/{segKey}"| ALN
    MCP -->|"read tenant config,<br/>message templates"| CDB

    ADM -->|"writes agents,<br/>prompts, templates,<br/>tenants"| CDB

    AIF -->|"selected flight + seat<br/>OR personalized messages"| API
    API -->|"structured JSON response"| FE
```

---

## 2. Detailed Technical Architecture (Developer View)

> Every Python module, its role, and its dependencies.

```mermaid
graph TB
    subgraph FASTAPI["FastAPI Process  ·  api_prod.py  ·  :8000"]
        direction TB
        EP["POST /disruption<br/>handler"]
        EMT["execute_mcp_tool()"]
        RA["run_agent()"]
        SJA["safe_json_from_agent()"]
        BEP["build_event_payload()"]
        ADMU["GET /admin/config/{tenant_id}"]

        EP --> EMT
        EMT -->|"MCP JSON-RPC response"| RA
        RA --> SJA
        RA --> BEP
    end

    subgraph MCPSRV["MCP Process  ·  server.py  ·  :8004"]
        direction TB
        HD["handle_disruption()<br/>@mcp.tool()"]
        NE["normalize_event()"]
        VR["validate_request()<br/>⚠ BUG-001: missing tenant_id"]

        HD --> NE
        HD --> VR
    end

    subgraph SERVICES["services/"]
        direction TB
        DC["disruption_client.py<br/>fetch_event_by_pnr()"]
        CC["cdp_client.py<br/>find_users()"]
        AC["airline_client.py<br/>search_flights()<br/>get_seat_map()"]
        HC["http_client.py<br/>httpx.AsyncClient singleton<br/>max_conn=100, timeout=30s"]

        DC --> HC
        CC --> HC
        AC --> HC
    end

    subgraph CONF["config/"]
        direction TB
        CDF["cosmos_data_fetcher.py<br/>ALL Cosmos DB access<br/>fetch_*() functions"]
        RC["runtime_config.py<br/>get_runtime_config()"]
        SET["settings.py<br/>env vars + defaults"]

        RC --> CDF
    end

    subgraph TEN["tenant/"]
        TC["tenant_config.py<br/>get_tenant_config()<br/>Cosmos DB → FALLBACK dict"]
        TC --> CDF
    end

    subgraph TOOLS["tools/"]
        VAL["validator.py<br/>check_user_autorecovery_eligibility()"]
        MF["message_fetcher.py<br/>fetch_all_messages()"]

        VAL --> CC
        MF --> CDF
    end

    subgraph EXTDB["Azure Cosmos DB — flight_operations"]
        direction LR
        T_AGT[("agents")]
        T_PRM[("prompts")]
        T_TEN[("tenants")]
        T_TPL[("templates")]
        T_SET[("settings")]
    end

    subgraph EXTAPI["External APIs (per tenant)"]
        direction LR
        EXT_DA["Disruption API"]
        EXT_CDP["CDP API"]
        EXT_ALN["Airline API"]
    end

    %% FastAPI → MCP
    EMT -->|"HTTP POST<br/>localhost:8004/mcp"| HD

    %% FastAPI → Config
    RA --> RC

    %% MCP → Services
    HD --> DC
    HD --> CC
    HD --> AC
    HD --> VR
    HD --> MF
    HD --> TC

    %% Services → External APIs
    DC --> EXT_DA
    CC --> EXT_CDP
    AC --> EXT_ALN

    %% Config → Cosmos DB
    CDF --> T_AGT
    CDF --> T_PRM
    CDF --> T_TEN
    CDF --> T_TPL
    CDF --> T_SET

    %% Settings → Services
    SET --> CC
    SET --> DC
    SET --> AC
```

---

## 3. Recovery Flow — Flight Cancelled

> Step-by-step sequence for `event_type == "flight_cancelled"`. Only eligible passengers (HIGHSPENDER or STUDENT) proceed.

```mermaid
sequenceDiagram
    autonumber
    actor C as Client
    participant API as api_prod.py<br/>(FastAPI)
    participant MCP as server.py<br/>(MCP)
    participant CDB as Cosmos DB
    participant DA as Disruption API
    participant CDP as CDP API
    participant ALN as Airline API
    participant AIF as Azure AI Foundry

    C->>API: POST /disruption<br/>{pnr, last_name, tenant_id}

    API->>MCP: JSON-RPC tools/call<br/>handle_disruption(pnr, last_name, tenant_id)

    MCP->>CDB: fetch_tenant_config(tenant_id)
    CDB-->>MCP: {flight_api, cdp_api, disruption_api}

    MCP->>DA: GET /disruptions/{pnr}<br/>Authorization: Bearer DISRUPTION_API_KEY
    DA-->>MCP: raw disruption event

    MCP->>MCP: normalize_event(raw)<br/>→ {event_type, pnr, segKey, passenger, cancellation, ...}
    MCP->>MCP: last_name case-insensitive match

    Note over MCP: event_type == "flight_cancelled"

    MCP->>CDP: POST /cdp/user-lookup<br/>{last_name, email_or_phone}<br/>(via validate_request — auto-recovery eligibility)
    CDP-->>MCP: user CDP profile

    MCP->>MCP: check eligibility:<br/>HIGHSPENDERHIGHFREQ or HIGHSPENDERLOWFREQ<br/>or STUDENT > 0?

    alt Passenger NOT eligible
        MCP-->>API: {status: "ineligible",<br/>reason: "NOT_ELIGIBLE_FOR_AUTORECOVERY"}
        API-->>C: {final: true, status: "ineligible"}
    else Passenger IS eligible
        par asyncio.gather — parallel I/O
            MCP->>CDP: POST /cdp/user-lookup<br/>(full profile fetch)
            CDP-->>MCP: CDP profile list
        and
            MCP->>ALN: POST /flight-search<br/>{segKey}
            ALN-->>MCP: available flights[]
        end

        Note over MCP: For each flight — parallel seat map fetch

        par asyncio.gather — parallel seat maps
            MCP->>ALN: GET /seat-map/{segKey_1}
            ALN-->>MCP: seat map 1
        and
            MCP->>ALN: GET /seat-map/{segKey_2}
            ALN-->>MCP: seat map 2
        and
            MCP->>ALN: GET /seat-map/{segKey_N}
            ALN-->>MCP: seat map N
        end

        MCP-->>API: {flow: "recovery",<br/>event, profile,<br/>recovery: {available_flights, available_seats}}

        API->>CDB: fetch_active_agents() +<br/>fetch_active_prompt_payload()
        CDB-->>API: {recovery_agent_id,<br/>system_prompt, text}

        Note over API: Build prompt with all MCP data embedded as JSON<br/>Prepend system_prompt, append text (admin overrides)

        API->>AIF: Create thread → post user message<br/>→ start run (recovery_agent_id)<br/>temperature=0.1, top_p=0.1

        loop Poll every 1s (max 120s)
            API->>AIF: Check run status
            AIF-->>API: RunStatus.IN_PROGRESS / COMPLETED
        end

        AIF-->>API: Agent output (JSON in markdown fence)

        API->>API: safe_json_from_agent(raw_text)<br/>→ strip fences → parse JSON

        Note over AIF,API: Agent scoring rules:<br/>+40 NonStop, +25 early arrival, +20 close departure<br/>STUDENT → cheapest economy<br/>HIGHSPENDER → comfort first (isStretch +40, Business +30)<br/>Business cabin original → must select business

        API-->>C: {status: "success", flow: "recovery",<br/>event: {...},<br/>result: {cdp_summary, selected_flight,<br/>selected_seat, reasoning}}
    end
```

---

## 4. Messaging Flow — Flight Delayed

> Step-by-step sequence for `event_type == "flight_delayed"`. All passengers receive personalized messages. No eligibility gate.

```mermaid
sequenceDiagram
    autonumber
    actor C as Client
    participant API as api_prod.py<br/>(FastAPI)
    participant MCP as server.py<br/>(MCP)
    participant CDB as Cosmos DB
    participant DA as Disruption API
    participant CDP as CDP API
    participant AIF as Azure AI Foundry

    C->>API: POST /disruption<br/>{pnr, last_name, tenant_id}

    API->>MCP: JSON-RPC tools/call<br/>handle_disruption(pnr, last_name, tenant_id)

    MCP->>CDB: fetch_tenant_config(tenant_id)
    CDB-->>MCP: {flight_api, cdp_api, disruption_api}

    MCP->>DA: GET /disruptions/{pnr}<br/>Authorization: Bearer DISRUPTION_API_KEY
    DA-->>MCP: raw disruption event

    MCP->>MCP: normalize_event(raw)<br/>→ {event_type, pnr, passenger, delay: {count, duration, reason}, ...}
    MCP->>MCP: last_name case-insensitive match

    Note over MCP: event_type == "flight_delayed"

    MCP->>CDP: POST /cdp/user-lookup<br/>{last_name, email_or_phone}
    CDP-->>MCP: CDP profile list

    MCP->>CDB: fetch_active_template()<br/>(templates collection, is_active: true)
    CDB-->>MCP: message template JSON<br/>{MESSAGES: [{group_id, delay_count, channels[]}]}

    MCP->>MCP: fetch_all_messages()<br/>flatten channels → {total_messages, messages[]}

    MCP-->>API: {flow: "messaging",<br/>event, profile,<br/>messages: {total_messages, messages[]}}

    API->>CDB: fetch_active_agents() +<br/>fetch_active_prompt_payload()
    CDB-->>API: {message_agent_id,<br/>system_prompt, text}

    Note over API: Build prompt with profile + event + all message templates embedded<br/>Prepend system_prompt, append text (admin overrides)

    API->>AIF: Create thread → post user message<br/>→ start run (message_agent_id)<br/>temperature=0.1, top_p=0.1

    loop Poll every 1s (max 120s)
        API->>AIF: Check run status
        AIF-->>API: RunStatus.IN_PROGRESS / COMPLETED
    end

    AIF-->>API: Agent output (JSON in markdown fence)

    API->>API: safe_json_from_agent(raw_text)<br/>→ strip fences → parse JSON

    Note over AIF,API: Persona priority:<br/>1. DEFENCE (defense_flag==1)<br/>2. DOCTOR (doctor_flag==1)<br/>3. STUDENT (student_flag≥1)<br/>4. GENERAL (default)<br/>Replace {var1}=origin, {var2}=destination

    API-->>C: {status: "success", flow: "messaging",<br/>event: {...},<br/>result: {selected_group_id, reason,<br/>messages: [{id, channel, message} × 3]}}
```

---

## 5. Error & Edge-Case Paths

> Every non-success branch from the entry point through both flows.

```mermaid
flowchart TD
    REQ["POST /disruption\n{pnr, last_name, tenant_id}"]

    REQ --> V1{"All 3 fields\npresent?"}
    V1 -->|"No"| E1["❌ error\nTENANT_PNR_LASTNAME_REQUIRED\n(MCP-level)"]
    V1 -->|"Yes"| TENANT["resolve tenant config\nfetch_tenant_config(tenant_id)"]

    TENANT --> V_TEN{"Tenant found\nin Cosmos DB\nor FALLBACK?"}
    V_TEN -->|"No"| E_TEN["❌ HTTP 500\nUnknown tenant: {tenant_id}"]
    V_TEN -->|"Yes"| FETCH["GET /disruptions/{pnr}"]

    FETCH --> V2{"Event\nfound?"}
    V2 -->|"404 Not Found"| E2["❌ error\nEVENT_NOT_FOUND"]
    V2 -->|"API error"| E2B["❌ HTTP 500\nDisruption API failure"]
    V2 -->|"200 OK"| V3{"last_name\ncase-insensitive\nmatch?"}

    V3 -->|"No"| E3["❌ error\nLAST_NAME_MISMATCH"]
    V3 -->|"Yes"| V4{"event_type?"}

    V4 -->|"other / unknown"| E4["⚠️ ignored\nUNSUPPORTED_EVENT_TYPE"]
    V4 -->|"flight_delayed"| DELAY_PATH["Messaging path"]
    V4 -->|"flight_cancelled"| V5{"segKey\npresent on\nevent?"}

    V5 -->|"No"| E5["❌ error\nSEGKEY_NOT_FOUND"]
    V5 -->|"Yes"| ELIG["CDP lookup\ncheck eligibility"]

    ELIG --> V5B{"CDP API\nreachable?"}
    V5B -->|"No"| E5B["❌ HTTP 500\nCDP API failure"]
    V5B -->|"Yes"| V6{"HIGHSPENDERHIGHFREQ\nor HIGHSPENDERLOWFREQ\nor STUDENT > 0?"}

    V6 -->|"No"| E6["⚠️ ineligible\nNOT_ELIGIBLE_FOR_AUTORECOVERY\n(returned as-is to frontend)"]
    V6 -->|"Yes"| FLIGHTS["POST /flight-search\n+ CDP profile\n(parallel)"]

    FLIGHTS --> V7{"Airline API\nreachable?"}
    V7 -->|"No"| E7["❌ error\nAIRLINE_API_FAILURE"]
    V7 -->|"Yes"| SEATS["GET /seat-map per flight\n(parallel, non-fatal failures)"]

    SEATS --> V7B{"Any seats\nreturned?"}
    V7B -->|"No (logged, non-fatal)"| AGENT_R["Recovery Agent run\n(empty available_seats)"]
    V7B -->|"Yes"| AGENT_R

    DELAY_PATH --> AGENT_M["Messaging Agent run"]

    AGENT_R --> V8{"Agent completes\nwithin 120s?"}
    AGENT_M --> V9{"Agent completes\nwithin 120s?"}

    V8 -->|"Timeout"| E8["❌ HTTP 500\nTimeoutError: Agent timeout"]
    V9 -->|"Timeout"| E9["❌ HTTP 500\nTimeoutError: Agent timeout"]

    V8 -->|"Completed"| V8B{"Agent output\ncontains valid JSON?"}
    V9 -->|"Completed"| V9B{"Agent output\ncontains valid JSON?"}

    V8B -->|"No JSON found"| E8B["❌ HTTP 500\nValueError: No JSON object found"]
    V9B -->|"No JSON found"| E9B["❌ HTTP 500\nValueError: No JSON object found"]

    V8B -->|"Valid JSON"| S1["✅ success\nflow: recovery\n{selected_flight, selected_seat, reasoning}"]
    V9B -->|"Valid JSON"| S2["✅ success\nflow: messaging\n{selected_group_id, messages × 3 channels}"]

    style E1 fill:#ff6b6b,color:#fff
    style E2 fill:#ff6b6b,color:#fff
    style E2B fill:#ff6b6b,color:#fff
    style E3 fill:#ff6b6b,color:#fff
    style E4 fill:#ffa94d,color:#fff
    style E5 fill:#ff6b6b,color:#fff
    style E5B fill:#ff6b6b,color:#fff
    style E6 fill:#ffa94d,color:#fff
    style E7 fill:#ff6b6b,color:#fff
    style E8 fill:#ff6b6b,color:#fff
    style E9 fill:#ff6b6b,color:#fff
    style E8B fill:#ff6b6b,color:#fff
    style E9B fill:#ff6b6b,color:#fff
    style E_TEN fill:#ff6b6b,color:#fff
    style S1 fill:#51cf66,color:#fff
    style S2 fill:#51cf66,color:#fff
```

**Legend:**
- 🔴 `error` — hard failure returned to the client
- 🟠 `ineligible` / `ignored` — soft non-error outcomes, handled by frontend
- 🟢 `success` — normal completion

---

## 6. Admin Config Flow — Agent & Prompt Hot-Swap

> How the admin panel writes to Cosmos DB, and how this service reads it on every request without a restart.

```mermaid
flowchart LR
    subgraph ADMIN["Admin Panel (External Service)"]
        AP["Admin UI"]
    end

    subgraph COSMOS["Azure Cosmos DB — flight_operations"]
        direction TB
        AGT[("agents\n───────────\nagent_id\nis_active_recovery\nis_active_messaging")]
        PRM[("prompts\n───────────\ntype: recovery|messaging\nis_active\nsystem_prompt\nuser_prompt")]
        TEN[("tenants\n───────────\ntenant_id\nflight_api\ncdp_api\ndisruption_api")]
        TPL[("templates\n───────────\nis_active\ncontent.raw_content\nMESSAGES[]")]
    end

    subgraph RUNTIME["config/runtime_config.py"]
        GRC["get_runtime_config(tenant_id)\n───────────\ncalled on every /disruption request\nno caching ⚠ DEBT-006"]
    end

    subgraph FASTAPI["api_prod.py — run_agent()"]
        direction TB
        PROMPT["1. Build base prompt\n(hardcoded in run_agent)"]
        INJECT["2. Inject admin overrides\nprepend system_prompt\nappend text"]
        AGENTRUN["3. Start Azure AI agent run\nusing resolved agent_id"]
    end

    subgraph MCPSRV["server.py — handle_disruption()"]
        direction TB
        TC_CALL["get_tenant_config(tenant_id)\ncalled per service client call"]
        TPL_CALL["fetch_all_messages()\ncalled in messaging flow"]
    end

    AP -->|"set is_active_recovery: true\n(flip flag to hot-swap)"| AGT
    AP -->|"set is_active: true\n(flip flag to change prompt)"| PRM
    AP -->|"upsert via seed_tenants.py\nor direct write"| TEN
    AP -->|"set is_active: true\n(flip flag to change templates)"| TPL

    AGT -->|"fetch_active_agents()\n→ recovery_agent_id\n+ message_agent_id"| GRC
    PRM -->|"fetch_active_prompt_payload()\n→ system_prompt, text"| GRC

    GRC -->|"{recovery_agent_id,\nmessage_agent_id,\nsystem_prompt, text}"| PROMPT
    PROMPT --> INJECT
    INJECT --> AGENTRUN

    TEN -->|"fetch_tenant_config(tenant_id)\n→ {flight_api, cdp_api, disruption_api}"| TC_CALL
    TPL -->|"fetch_active_template()\n→ message groups"| TPL_CALL

    NOTE["⚡ Hot-swap works because:\nAgent ID is resolved on every request.\nChange is_active flag in Cosmos DB\n→ next request uses the new agent.\nNo service restart required."]

    style NOTE fill:#e7f5ff,stroke:#339af0
    style AGT fill:#fff9db,stroke:#fcc419
    style PRM fill:#fff9db,stroke:#fcc419
    style TEN fill:#fff9db,stroke:#fcc419
    style TPL fill:#fff9db,stroke:#fcc419
```

---

## Configuration Priority Layers

> How different config sources stack up — highest priority wins.

```mermaid
graph TB
    subgraph L1["Priority 1 — Runtime (Cosmos DB, per-request)"]
        A1["Admin system_prompt → prepended to base prompt"]
        A2["Admin text → appended to base prompt"]
        A3["Active agent IDs → recovery_agent_id, message_agent_id"]
    end

    subgraph L2["Priority 2 — Base Prompts (Code, api_prod.py)"]
        B1["Recovery scoring rules hardcoded in run_agent()"]
        B2["Messaging persona rules hardcoded in run_agent()"]
    end

    subgraph L3["Priority 3 — Environment Variables (.env)"]
        C1["API base URLs (with mock defaults)"]
        C2["API keys (with test defaults)"]
        C3["COSMOS_DB credentials"]
        C4["TIMEOUT (default 30s)"]
    end

    L1 --> EFFECT["Effective agent behaviour"]
    L2 --> EFFECT
    L3 --> EFFECT

    style L1 fill:#d3f9d8,stroke:#2f9e44
    style L2 fill:#fff9db,stroke:#fcc419
    style L3 fill:#e7f5ff,stroke:#339af0
```

---

## Process Startup Sequence

```mermaid
sequenceDiagram
    participant DEV as Developer
    participant MCP as server.py (port 8004)
    participant API as api_prod.py (port 8000)
    participant AZ as Azure CLI

    DEV->>AZ: az login
    AZ-->>DEV: authenticated

    DEV->>MCP: python server.py
    MCP->>MCP: FastMCP init<br/>transport: streamable-http<br/>path: /mcp<br/>stateless: true
    MCP-->>DEV: Listening on 0.0.0.0:8004

    DEV->>API: uvicorn api_prod:app --port 8000
    API->>API: FastAPI init<br/>CORS open (allow_origins=*)
    API->>API: Azure credential init<br/>DefaultAzureCredential → AzureCliCredential fallback
    API-->>DEV: Listening on 0.0.0.0:8000

    Note over MCP,API: Both processes must be running<br/>MCP is localhost-only (not exposed externally)<br/>FastAPI is the only public-facing process
```
