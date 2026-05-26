
from config.cosmos_data_fetcher import get_collection


TENANTS = [
    {
        "tenant_id": "indigo_mock",
        "flight_api": "https://mock-flightdetails-api.niceflower-39d2e00e.southindia.azurecontainerapps.io",
        "cdp_api": "https://cdp-mock-api.niceflower-39d2e00e.southindia.azurecontainerapps.io/",
        "disruption_api": "https://disruption-mock-api1.niceflower-39d2e00e.southindia.azurecontainerapps.io/"
    },
    {
        "tenant_id": "airline_mock",
        "flight_api": "https://mock-flightdetails-api.niceflower-39d2e00e.southindia.azurecontainerapps.io",
        "cdp_api": "https://cdp-mock-api.niceflower-39d2e00e.southindia.azurecontainerapps.io/",
        "disruption_api": "https://disruption-mock-api1.niceflower-39d2e00e.southindia.azurecontainerapps.io/"
    }
]


def seed():
    client, collection = get_collection("tenants")

    try:
        for tenant in TENANTS:
            result = collection.update_one(
                {"tenant_id": tenant["tenant_id"]},
                {"$set": tenant},
                upsert=True
            )

            action = "updated" if result.matched_count else "inserted"
            print(f"[{action}] {tenant['tenant_id']}")

        # Verify
        print("\n--- Tenants in DB ---")
        for doc in collection.find({}, {"_id": 0}):
            print(doc)

    finally:
        client.close()


if __name__ == "__main__":
    seed()
