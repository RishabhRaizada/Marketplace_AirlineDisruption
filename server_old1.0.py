import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

from fastmcp import FastMCP

from services.disruption_client import fetch_event_by_pnr
from services.cdp_client import find_users
from services.airline_client import (
    search_flights,
    get_seat_map
)

from tools.validator import validate_request
from tools.message_fetcher import fetch_all_messages

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("disruption-mcp")

mcp = FastMCP("disruption_mcp")

executor = ThreadPoolExecutor(max_workers=20)


def normalize_event(raw: dict) -> dict:
    user = raw.get("user_info", {})
    event_type = raw.get("event_type")

    base = {
        "event_type": event_type,
        "pnr": raw.get("pnr"),
        "last_name": user.get("USR_LASTNAME"),
        "segKey": raw.get("segKey"),
        "original_flight": raw,
        "passenger": {
            "email": user.get("USR_EMAIL"),
            "mobile": user.get("USR_MOBILE")
        }
    }

    if event_type == "flight_cancelled":
        base["cancellation"] = {
            "timestamp": raw.get("cancellation_time") or raw.get("event_timestamp"),
            "reason": raw.get("cancellation_reason"),
            "source": raw.get("cancellation_source")
        }

    elif event_type == "flight_delayed":
        base["delay"] = {
            "delay_count": raw.get("delay_count"),
            "delay_duration_minutes": raw.get("delay_duration_minutes"),
            "delay_reason": raw.get("delay_reason")
        }

    return base


@mcp.tool()
def handle_disruption(tenant_id: str, pnr: str, last_name: str):

    logger.info(
        "TENANT=%s PNR=%s LAST_NAME=%s",
        tenant_id,
        pnr,
        last_name
    )

    if not tenant_id or not pnr or not last_name:
        return {
            "content": [{
                "type": "json",
                "json": {
                    "final": True,
                    "status": "error",
                    "reason": "TENANT_PNR_LASTNAME_REQUIRED"
                }
            }]
        }

    # -------------------------------------------------
    # DISRUPTION API
    # -------------------------------------------------
    raw = fetch_event_by_pnr(tenant_id, pnr)

    if not raw:
        return {
            "content": [{
                "type": "json",
                "json": {
                    "final": True,
                    "status": "error",
                    "reason": "EVENT_NOT_FOUND"
                }
            }]
        }

    event = normalize_event(raw)

    if not event.get("last_name") or \
       event["last_name"].lower() != last_name.lower():
        return {
            "content": [{
                "type": "json",
                "json": {
                    "final": True,
                    "status": "error",
                    "reason": "LAST_NAME_MISMATCH"
                }
            }]
        }

    # =====================================================
    # CANCELLATION FLOW
    # =====================================================
    if event["event_type"] == "flight_cancelled":

        eligibility = validate_request(
            last_name,
            event["passenger"]["email"] or event["passenger"]["mobile"]
        )

        if not eligibility or eligibility.get("eligible") is False:
            return {
                "content": [{
                    "type": "json",
                    "json": {
                        "final": True,
                        "status": "ineligible",
                        "reason": "NOT_ELIGIBLE_FOR_AUTORECOVERY"
                    }
                }]
            }

        seg_key = event.get("segKey")

        if not seg_key:
            return {
                "content": [{
                    "type": "json",
                    "json": {
                        "final": True,
                        "status": "error",
                        "reason": "SEGKEY_NOT_FOUND"
                    }
                }]
            }

        try:

            # -------------------------------------------------
            # PARALLEL: CDP + FLIGHT SEARCH
            # -------------------------------------------------
            future_profile = executor.submit(
                find_users,
                tenant_id,
                last_name,
                event["passenger"]["email"] or event["passenger"]["mobile"]
            )

            future_flights = executor.submit(
                search_flights,
                tenant_id,
                seg_key
            )

            profile = future_profile.result()
            flights = future_flights.result()

            logger.info(
                "FLIGHT_SEARCH_RESULT_COUNT=%d",
                len(flights)
            )

            if not flights:
                return {
                    "content": [{
                        "type": "json",
                        "json": {
                            "final": True,
                            "status": "success",
                            "flow": "recovery",
                            "event": event,
                            "profile": profile,
                            "recovery": {
                                "available_flights": [],
                                "available_seats": []
                            }
                        }
                    }]
                }

            # -------------------------------------------------
            # PARALLEL SEATMAP FETCH
            # -------------------------------------------------
            seat_futures = {
                executor.submit(
                    get_seat_map,
                    tenant_id,
                    f.get("segKey")
                ): f.get("segKey")
                for f in flights
            }

            all_seats = []

            for future in as_completed(seat_futures):

                flight_segkey = seat_futures[future]

                try:
                    seats = future.result()

                    logger.info(
                        "SEATS_FOUND_FOR_FLIGHT=%s COUNT=%d",
                        flight_segkey,
                        len(seats)
                    )

                    all_seats.extend(seats)

                except Exception as e:

                    logger.error(
                        "SEATMAP_FETCH_FAILED=%s ERROR=%s",
                        flight_segkey,
                        str(e)
                    )

            logger.info(
                "TOTAL_SEATS_FOUND=%d",
                len(all_seats)
            )

        except Exception as e:

            logger.error(
                "AIRLINE_API_ERROR=%s",
                str(e)
            )

            return {
                "content": [{
                    "type": "json",
                    "json": {
                        "final": True,
                        "status": "error",
                        "reason": "AIRLINE_API_FAILURE",
                        "details": str(e)
                    }
                }]
            }

        return {
            "content": [{
                "type": "json",
                "json": {
                    "final": True,
                    "status": "success",
                    "flow": "recovery",
                    "event": event,
                    "profile": profile,
                    "recovery": {
                        "available_flights": flights,
                        "available_seats": all_seats
                    }
                }
            }]
        }

    # =====================================================
    # DELAY FLOW
    # =====================================================
    if event["event_type"] == "flight_delayed":

        profile = find_users(
            tenant_id,
            last_name,
            event["passenger"]["email"] or event["passenger"]["mobile"]
        )

        return {
            "content": [{
                "type": "json",
                "json": {
                    "final": True,
                    "status": "success",
                    "flow": "messaging",
                    "event": event,
                    "profile": profile,
                    "messages": fetch_all_messages()
                }
            }]
        }

    return {
        "content": [{
            "type": "json",
            "json": {
                "final": True,
                "status": "ignored",
                "reason": "UNSUPPORTED_EVENT_TYPE"
            }
        }]
    }


if __name__ == "__main__":

    mcp.run(
        transport="streamable-http",
        host="0.0.0.0",
        port=8004,
        path="/mcp",
        stateless_http=True
    )