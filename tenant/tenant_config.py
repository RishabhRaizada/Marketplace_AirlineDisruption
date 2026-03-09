TENANTS = {
    "indigo_mock": {
        "flight_api": "https://mock-flightdetails-api.niceflower-39d2e00e.southindia.azurecontainerapps.io",
        "cdp_api": "https://cdp-mock-api.niceflower-39d2e00e.southindia.azurecontainerapps.io/",
        "disruption_api": "https://disruption-mock-api1.niceflower-39d2e00e.southindia.azurecontainerapps.io/"
    }
}


def get_tenant_config(tenant_id: str):
    if tenant_id not in TENANTS:
        raise Exception(f"Unknown tenant: {tenant_id}")

    return TENANTS[tenant_id]

