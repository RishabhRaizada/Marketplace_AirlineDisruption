import requests
from tenant.tenant_config import get_tenant_config
from config.settings import TIMEOUT


def _headers():
    return {
        "Content-Type": "application/json"
    }


def search_flights(tenant_id: str, seg_key: str):

    config = get_tenant_config(tenant_id)

    base_url = config["flight_api"]

    payload = {
        "segKey": seg_key
    }

    response = requests.post(
        f"{base_url}/flight-search",
        headers=_headers(),
        json=payload,
        timeout=TIMEOUT
    )

    response.raise_for_status()

    data = response.json()

    return data.get("flights", [])


def get_seat_map(tenant_id: str, seg_key: str):

    config = get_tenant_config(tenant_id)

    base_url = config["flight_api"]

    response = requests.get(
        f"{base_url}/seat-map/{seg_key}",
        headers=_headers(),
        timeout=TIMEOUT
    )

    response.raise_for_status()

    data = response.json()

    seatmap = data["data"]["seatMaps"][0]["seatMap"]

    seats = []

    for deck in seatmap.get("decks", {}).values():
        for cabin in deck.get("compartments", {}).values():
            for seat in cabin.get("units", []):

                if not seat.get("assignable"):
                    continue

                seats.append({
                    "seat_number": seat.get("designator"),
                    "travel_class": seat.get("travelClassCode"),
                    "availability": seat.get("availability"),
                    "seat_type": [
                        p["code"] for p in seat.get("properties", [])
                    ]
                })

    return seats