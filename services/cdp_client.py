from config.settings import (
    CDP_API_BASE_URL,
    CDP_API_KEY
)

from tenant.tenant_config import get_tenant_config
from services.http_client import client


def _headers():
    return {
        "Authorization": f"Bearer {CDP_API_KEY}",
        "Content-Type": "application/json"
    }


async def find_users(tenant_id: str, last_name: str, email_or_phone: str):

    payload = {
        "last_name": last_name,
        "email_or_phone": email_or_phone
    }

    # Resolve tenant configuration
    tenant_config = get_tenant_config(tenant_id)

    # Use tenant-specific API if present, otherwise fallback
    base_url = tenant_config.get("cdp_api", CDP_API_BASE_URL)

    response = await client.post(
        f"{base_url}/cdp/user-lookup",
        headers=_headers(),
        json=payload
    )

    response.raise_for_status()

    data = response.json()

    if "error" in data:
        raise Exception(data["error"]["message"])

    return data.get("users", [])