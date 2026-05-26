from config.cosmos_data_fetcher import fetch_tenant_config

# ---------------------------------------------------------------------------
# Hardcoded fallback — used if tenant is not found in Cosmos DB
# ---------------------------------------------------------------------------

FALLBACK_TENANTS = {
    "indigo_mock": {
        "flight_api": "https://mock-flightdetails-api.niceflower-39d2e00e.southindia.azurecontainerapps.io",
        "cdp_api": "https://cdp-mock-api.niceflower-39d2e00e.southindia.azurecontainerapps.io/",
        "disruption_api": "https://disruption-mock-api1.niceflower-39d2e00e.southindia.azurecontainerapps.io/"
    },
    "airline_mock": {
        "flight_api": "https://mock-flightdetails-api.niceflower-39d2e00e.southindia.azurecontainerapps.io",
        "cdp_api": "https://cdp-mock-api.niceflower-39d2e00e.southindia.azurecontainerapps.io/",
        "disruption_api": "https://disruption-mock-api1.niceflower-39d2e00e.southindia.azurecontainerapps.io/"
    }
}


def get_tenant_config(tenant_id: str) -> dict:
    """
    Fetch tenant config from Cosmos DB.
    Falls back to hardcoded dict if DB lookup fails or tenant not found.
    """

    # Try Cosmos DB first
    config = fetch_tenant_config(tenant_id)

    if config:
        return config

    # Fallback to hardcoded config
    if tenant_id in FALLBACK_TENANTS:
        return FALLBACK_TENANTS[tenant_id]

    raise Exception(f"Unknown tenant: {tenant_id}")
