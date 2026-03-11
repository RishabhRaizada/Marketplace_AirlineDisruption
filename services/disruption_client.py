from config.settings import (
    DISRUPTION_API_BASE_URL,
    DISRUPTION_API_KEY
)

from tenant.tenant_config import get_tenant_config
from services.http_client import client


def _headers():
    return {
        "Authorization": f"Bearer {DISRUPTION_API_KEY}",
        "Content-Type": "application/json"
    }


async def fetch_event_by_pnr(tenant_id: str, pnr: str):

    # Resolve tenant configuration
    tenant_config = get_tenant_config(tenant_id)

    # Use tenant-specific API if available
    base_url = tenant_config.get("disruption_api", DISRUPTION_API_BASE_URL)

    response = await client.get(
        f"{base_url}/disruptions/{pnr}",
        headers=_headers()
    )

    if response.status_code == 404:
        return None

    response.raise_for_status()

    data = response.json()

    if "error" in data:
        raise Exception(data["error"]["message"])

    return data.get("event")
# import requests
# from config.settings import (
#     DISRUPTION_API_BASE_URL,
#     DISRUPTION_API_KEY,
#     TIMEOUT
# )

# from tenant.tenant_config import get_tenant_config


# def _headers():
#     return {
#         "Authorization": f"Bearer {DISRUPTION_API_KEY}",
#         "Content-Type": "application/json"
#     }


# def fetch_event_by_pnr(tenant_id: str, pnr: str):

#     # Resolve tenant configuration
#     tenant_config = get_tenant_config(tenant_id)

#     # Use tenant-specific API if available
#     base_url = tenant_config.get("disruption_api", DISRUPTION_API_BASE_URL)

#     response = requests.get(
#         f"{base_url}/disruptions/{pnr}",
#         headers=_headers(),
#         timeout=TIMEOUT
#     )

#     if response.status_code == 404:
#         return None

#     response.raise_for_status()

#     data = response.json()

#     if "error" in data:
#         raise Exception(data["error"]["message"])

#     return data.get("event")