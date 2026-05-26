# Deployment — prodDisruption

## Local Development Setup

### Prerequisites

- Python 3.11+
- Azure CLI (`az`) installed and authenticated (`az login`)
- Access to the Azure subscription hosting the AI Foundry project

### 1. Clone and create virtualenv

```bash
cd prodDisruption
python3 -m venv venv
source venv/bin/activate       # macOS/Linux
# venv\Scripts\activate        # Windows
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

Key packages:
- `azure-ai-projects==1.1.0b4` — AI Foundry project client
- `azure-ai-agents==1.2.0b3` — Agent SDK
- `azure-identity==1.25.1` — Azure authentication
- `fastapi` — REST API framework
- `fastmcp` — MCP server framework
- `uvicorn[standard]` — ASGI server
- `pymongo` — Cosmos DB (MongoDB API) client
- `httpx` — Async HTTP client (referenced in code but not in requirements.txt — add it)
- `python-dotenv` — `.env` file loader
- `requests` — Sync HTTP (used by FastAPI layer for MCP calls)

**Note:** `requests` appears twice in `requirements.txt` (duplicate). `httpx` is used in service clients but not listed in requirements.txt — this is a missing dependency.

### 3. Configure environment

Create `.env` in the project root (already gitignored):

```env
COSMOS_DB_USERNAME=pluggableagent
COSMOS_DB_PASSWORD=AIONOS@1234
COSMOS_DB_HOST=aionospluggable.global.mongocluster.cosmos.azure.com
COSMOS_DB_NAME=flight_operations

# Optional — defaults to mock URLs if not set
AIRLINE_API_BASE_URL=https://mock-flightdetails-api.niceflower-39d2e00e.southindia.azurecontainerapps.io
CDP_API_BASE_URL=https://cdp-mock-api.niceflower-39d2e00e.southindia.azurecontainerapps.io/
DISRUPTION_API_BASE_URL=https://disruption-mock-api1.niceflower-39d2e00e.southindia.azurecontainerapps.io/
AIRLINE_API_KEY=test-key
CDP_API_KEY=cdp-test-key
DISRUPTION_API_KEY=disruption-test-key
TIMEOUT=30
```

### 4. Authenticate with Azure

```bash
az login
```

Required for `AzureCliCredential` fallback. In local dev, `DefaultAzureCredential` will fail (no managed identity) and the code falls back to `AzureCliCredential` automatically.

### 5. Start both processes (two terminals)

**Terminal 1 — MCP Server:**
```bash
source venv/bin/activate
python server.py
```
Starts on `http://0.0.0.0:8004/mcp`

**Terminal 2 — FastAPI Server:**
```bash
source venv/bin/activate
uvicorn api_prod:app --host 0.0.0.0 --port 8000 --reload
```
Starts on `http://0.0.0.0:8000`

### 6. Test the setup

```bash
curl -X POST http://localhost:8000/disruption \
  -H "Content-Type: application/json" \
  -d '{"pnr": "ABC123", "last_name": "SHARMA", "tenant_id": "indigo_mock"}'
```

---

## Environment Variables Reference

| Variable | Required | Default | Description |
|---|---|---|---|
| `COSMOS_DB_USERNAME` | yes | — | Cosmos DB MongoDB username |
| `COSMOS_DB_PASSWORD` | yes | — | Cosmos DB MongoDB password |
| `COSMOS_DB_HOST` | yes | — | Cosmos DB MongoDB host (global cluster) |
| `COSMOS_DB_NAME` | no | `flight_operations` | Database name |
| `AIRLINE_API_BASE_URL` | no | mock Azure URL | Flight search + seat map base URL |
| `CDP_API_BASE_URL` | no | mock Azure URL | CDP user lookup base URL |
| `DISRUPTION_API_BASE_URL` | no | mock Azure URL | Disruption events base URL |
| `AIRLINE_API_KEY` | no | `test-key` | Airline API bearer token |
| `CDP_API_KEY` | no | `cdp-test-key` | CDP API bearer token |
| `DISRUPTION_API_KEY` | no | `disruption-test-key` | Disruption API bearer token |
| `TIMEOUT` | no | `30` | HTTP request timeout in seconds |

---

## Azure Resources

| Resource | Details |
|---|---|
| AI Foundry Project | `https://marketplace-aifoundry.services.ai.azure.com/api/projects/proj-default` |
| Cosmos DB Account | `aionospluggable.global.mongocluster.cosmos.azure.com` |
| Mock Airline API | Azure Container Apps, South India |
| Mock CDP API | Azure Container Apps, South India |
| Mock Disruption API | Azure Container Apps, South India |

---

## Mock Servers (Reference)

The mock APIs are pre-deployed to Azure Container Apps. For local mock development (commented in `config/settings.py`):

```bash
# Mock CDP server
python -m uvicorn apis.mock_cdp_server:app --host 0.0.0.0 --port 9100 --reload

# Mock disruption server
python -m uvicorn apis.mock_disruption_server:app --host 0.0.0.0 --port 9200 --reload

# Mock flight server
python -m uvicorn apis.mock_flight_server:app --host 0.0.0.0 --port 9001 --reload

# Docker alternative
docker run --env-file .env -p 9200:9200 airline-disruption-api
```

Note: The `apis/` directory is not present in this repository. These are from a separate mock server project.

---

## Production Deployment (Azure)

The expected production topology is:

```
Load Balancer / API Gateway
        │
        ▼
FastAPI Server (api_prod.py)
        │
        ▼ localhost
MCP Server (server.py)
        │
        ├──► Azure AI Foundry (agent execution)
        ├──► Cosmos DB (config, templates)
        └──► External APIs (airline, CDP, disruption) per tenant
```

### Recommended deployment approach:
1. Deploy both processes on the same Azure Container App or VM (MCP server is localhost-only)
2. Use Azure Managed Identity instead of `AzureCliCredential` — `DefaultAzureCredential` will pick it up automatically
3. Store secrets in Azure Key Vault; reference them as env vars in the container

### CORS
Before production: restrict `allow_origins` in `api_prod.py` to the actual frontend domain.

---

## Seeding the Database

After first deployment, seed tenant records:

```bash
python seed_tenants.py
```

This upserts `indigo_mock` and `airline_mock` tenants. For new tenants, add them to `seed_tenants.py → TENANTS` and re-run.

---

## Checking System Health

```bash
# MCP server alive
curl http://localhost:8004/mcp -X POST \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":"ping","method":"tools/list","params":{}}'

# FastAPI admin config read (validates Cosmos DB + runtime config)
curl http://localhost:8000/admin/config/indigo_mock
```

---

## Known Missing Items

- No `Dockerfile` or `docker-compose.yml` (excluded by `.gitignore`)
- No CI/CD pipeline in repository
- No health check endpoint (`/health` or `/ping`)
- `httpx` missing from `requirements.txt`
- `requirements.txt` has duplicate `requests` entry
