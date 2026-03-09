def normalize_event(raw: dict) -> dict:
    user = raw.get("user_info", {})
    event_type = raw.get("event_type")

    base = {
        "event_type": event_type,
        "pnr": raw.get("pnr"),
        "last_name": user.get("USR_LASTNAME"),

        "flight": {
            "flight_number": raw.get("flight_number"),
            "origin": raw.get("origin"),
            "destination": raw.get("destination"),
            "utc_departure": raw.get("utc_scheduled_departure"),
            "utc_arrival": raw.get("utc_scheduled_arrival"),
            "cabin_class": raw.get("cabin_class"),
            "fare_class": raw.get("fare_class")
        },

        "passenger": {
            "email": user.get("USR_EMAIL"),
            "mobile": user.get("USR_MOBILE")
        }
    }

    # 🔴 Cancellation-specific fields
    if event_type == "flight_cancelled":
        base["cancellation"] = {
            "timestamp": raw.get("cancellation_time") or raw.get("event_timestamp"),
            "reason": raw.get("cancellation_reason"),
            "source": raw.get("cancellation_source"),
            "cancelled_after_checkin": raw.get("cancelled_after_checkin")
        }

    elif event_type == "flight_delayed":
        base["delay"] = {
            "delay_count": raw.get("delay_count"),
            "delay_duration_minutes": raw.get("delay_duration_minutes"),
            "delay_reason": raw.get("delay_reason")
        }

    return base
