__all__ = [
    "COSMOS_DB_URI",
    "COSMOS_DB_NAME",
    "fetch_api_settings",
    "fetch_active_prompt",
    "fetch_active_prompt_payload",
    "fetch_active_agents",
    "fetch_active_template",
    # Shared flight/seat utilities used by flight_api.py and flight_data_access.py
    "normalize_key",
    "get_collection",
    "extract_flights",
    "collect_seatmaps",
    "load_flights",
    "load_seatmaps",
]

import os
from copy import deepcopy
from urllib.parse import quote_plus
from pymongo import MongoClient
from dotenv import load_dotenv

load_dotenv()
import warnings

warnings.filterwarnings(
    "ignore",
    message="You appear to be connected to a CosmosDB cluster"
)
# ---------------------------------------------------------------------------
# Environment Variables
# ---------------------------------------------------------------------------

_USERNAME = os.getenv("COSMOS_DB_USERNAME")
_PASSWORD = os.getenv("COSMOS_DB_PASSWORD")
_HOST = os.getenv("COSMOS_DB_HOST")
COSMOS_DB_NAME = os.getenv("COSMOS_DB_NAME", "flight_operations")

COSMOS_DB_URI = (
    f"mongodb+srv://{_USERNAME}:{quote_plus(_PASSWORD or '')}"
    f"@{_HOST}/?retryWrites=true&w=majority"
)

# ---------------------------------------------------------------------------
# Collection Names
# ---------------------------------------------------------------------------

FLIGHT_COLLECTION = "flight_data"
SEATS_COLLECTION = "available_seats"

# ---------------------------------------------------------------------------
# Internal DB Helpers (shared across all modules)
# ---------------------------------------------------------------------------

def _get_db():
    """Return (MongoClient, DB) instance."""
    client = MongoClient(COSMOS_DB_URI, serverSelectionTimeoutMS=5000)
    return client, client[COSMOS_DB_NAME]


def _find_one(collection: str, query: dict, projection: dict | None = None):
    """Reusable single-document fetch helper."""
    client, db = _get_db()
    try:
        return db[collection].find_one(query, projection)
    finally:
        client.close()


def get_collection(collection_name: str):
    """Return (MongoClient, Collection) — caller is responsible for closing the client."""
    client, db = _get_db()
    return client, db[collection_name]


# ---------------------------------------------------------------------------
# Shared Key/Data Utilities
# ---------------------------------------------------------------------------

def normalize_key(value: str | None) -> str:
    """Strip and uppercase a string key for consistent comparison."""
    return (value or "").strip().upper()


def extract_flights(payload: dict | list) -> list[dict]:
    """Flatten all journeys from a raw Cosmos flight payload."""
    trips = []
    if isinstance(payload, dict):
        trips.extend(payload.get("data", {}).get("trips", []))
    elif isinstance(payload, list):
        for item in payload:
            if not isinstance(item, dict):
                continue
            trips.extend(item.get("data", {}).get("trips", []))

    flights = []
    for trip in trips:
        for journey in trip.get("journeysAvailable", []):
            segments = journey.get("segments", [])
            first_segment = segments[0] if segments else {}

            item = journey.copy()
            item["_trip_origin"] = trip.get("origin")
            item["_trip_destination"] = trip.get("destination")
            item["flight_uid"] = (
                journey.get("journeyKey")
                or journey.get("segKey")
                or first_segment.get("segmentKey")
            )
            flights.append(item)

    return flights


def collect_seatmaps(payload: dict | list) -> list[dict]:
    """Collect all seatMap entries from a raw Cosmos seat payload."""
    seat_maps = []
    if isinstance(payload, dict):
        seat_maps.extend(payload.get("data", {}).get("seatMaps", []))
        seat_maps.extend(payload.get("seatMaps", []))
    elif isinstance(payload, list):
        for item in payload:
            if not isinstance(item, dict):
                continue
            seat_maps.extend(item.get("data", {}).get("seatMaps", []))
            seat_maps.extend(item.get("seatMaps", []))
    return seat_maps


def recompute_seatmap_availability(seat_map: dict) -> dict:
    """Return a copy of seat_map with availableUnits synced to actual assignable seat units."""
    updated = deepcopy(seat_map)
    total = 0
    for deck in updated.get("decks", {}).values():
        for compartment in deck.get("compartments", {}).values():
            units = compartment.get("units", [])
            valid = [
                unit for unit in units
                if isinstance(unit, dict)
                and str(unit.get("designator") or "").strip()
                and bool(unit.get("assignable"))
            ]
            compartment["availableUnits"] = len(valid)
            total += len(valid)
    updated["availableUnits"] = total
    return updated


# ---------------------------------------------------------------------------
# Shared Data Loaders
# ---------------------------------------------------------------------------

def load_flights() -> list[dict]:
    """Load and flatten all journeys from Cosmos DB flight_data collection."""
    client, collection = get_collection(FLIGHT_COLLECTION)
    try:
        doc = collection.find_one({}, {"_id": 0, "content.extracted_data": 1, "data": 1})
        if not doc:
            raise Exception("No flight data found in Cosmos DB")
        extracted = doc.get("content", {}).get("extracted_data")
        payload = extracted if extracted is not None else doc
        return extract_flights(payload)
    except Exception as exc:
        raise Exception(f"Cosmos DB flight fetch failed: {exc}")
    finally:
        client.close()


def load_seatmaps() -> tuple[dict[str, dict], dict[str, dict]]:
    """
    Load seat-map data from Cosmos DB available_seats collection.
    Returns (by_seg, by_route) lookup dicts.
    """
    client, collection = get_collection(SEATS_COLLECTION)
    try:
        doc = collection.find_one(
            {},
            {"_id": 0, "content.extracted_data": 1, "data.seatMaps": 1, "seatMaps": 1},
        )
        if not doc:
            raise Exception("No seat data found in Cosmos DB")

        extracted = doc.get("content", {}).get("extracted_data")
        payload = extracted if extracted is not None else doc
        seat_map_entries = collect_seatmaps(payload)

        by_seg: dict[str, dict] = {}
        by_route: dict[str, dict] = {}

        for entry in seat_map_entries:
            sm = entry.get("seatMap", {})
            seg_key = entry.get("segKey")

            if seg_key:
                by_seg[normalize_key(seg_key)] = sm

            route_key = f"{sm.get('departureStation', '')}->{sm.get('arrivalStation', '')}".upper()
            by_route.setdefault(route_key, sm)

        return by_seg, by_route
    except Exception as exc:
        raise Exception(f"Cosmos DB seat fetch failed: {exc}")
    finally:
        client.close()


# ---------------------------------------------------------------------------
# API Settings
# ---------------------------------------------------------------------------

def fetch_api_settings() -> dict:
    try:
        return _find_one("settings", {"id": "api_settings"}, {"_id": 0}) or {}
    except Exception as e:
        print(f"[cosmos_data_fetcher] fetch_api_settings failed: {e}")
        return {}


# ---------------------------------------------------------------------------
# Active Prompt
# ---------------------------------------------------------------------------

def fetch_active_prompt_payload() -> dict:
    try:
        doc = _find_one("prompts", {"is_active": True}, {"_id": 0}) or {}
        system_prompt = doc.get("system_prompt") or doc.get("systemPrompt") or ""
        user_prompt = doc.get("user_prompt") or doc.get("userPrompt") or doc.get("text") or ""
        return {
            "name": doc.get("name"),
            "is_active": bool(doc.get("is_active")),
            "system_prompt": system_prompt,
            "user_prompt": user_prompt,
        }
    except Exception as e:
        print(f"[cosmos_data_fetcher] fetch_active_prompt_payload failed: {e}")
        return {"name": None, "is_active": False, "system_prompt": "", "user_prompt": ""}


def fetch_active_prompt() -> str | None:
    try:
        return fetch_active_prompt_payload().get("user_prompt") or None
    except Exception as e:
        print(f"[cosmos_data_fetcher] fetch_active_prompt failed: {e}")
        return None


# ---------------------------------------------------------------------------
# Active Agents
# ---------------------------------------------------------------------------

def fetch_active_agents() -> list[dict]:
    try:
        client, db = _get_db()
        try:
            return list(
                db["agents"].find(
                    {
                        "$or": [
                            {"is_active_recovery": True},
                            {"is_active_messaging": True}
                        ]
                    },
                    {
                        "_id": 0,
                        "agent_id": 1,
                        "name": 1,
                        "description": 1,
                        "is_active_recovery": 1,
                        "is_active_messaging": 1
                    },
                )
            )
        finally:
            client.close()
    except Exception as e:
        print(f"[cosmos_data_fetcher] fetch_active_agents failed: {e}")
        return []


# ---------------------------------------------------------------------------
# Active Template
# ---------------------------------------------------------------------------

def fetch_active_template() -> dict | None:
    try:
        doc = _find_one("templates", {"is_active": True}, {"_id": 0})
        if not doc:
            doc = _find_one("templates", {}, {"_id": 0})
        return doc or None
    except Exception as e:
        print(f"[cosmos_data_fetcher] fetch_active_template failed: {e}")
        return None
    
def fetch_recovery_prompt():

    doc = _find_one(
        "prompts",
        {"type": "recovery", "is_active": True},
        {"_id": 0}
    )

    if not doc:
        return ""

    return doc.get("system_prompt", "")

def fetch_messaging_prompt():

    doc = _find_one(
        "prompts",
        {"type": "messaging", "is_active": True},
        {"_id": 0}
    )

    if not doc:
        return ""

    return doc.get("user_prompt", "")