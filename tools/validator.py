import json
from services.cdp_client import find_users


def normalize_bool(v):
    if isinstance(v, bool):
        return v
    if isinstance(v, int):
        return v == 1
    if isinstance(v, str):
        return v.strip().lower() in ["true", "1", "yes"]
    return False


def normalize_student(v):
    if isinstance(v, bool):
        return v
    if isinstance(v, int):
        return v > 0
    if isinstance(v, str):
        return v.strip().isdigit() and int(v) > 0
    return False


def check_user_autorecovery_eligibility(last_name, email_or_phone):

    try:
        users = find_users(last_name, email_or_phone)
    except Exception as e:
        return {"status": "error", "reason": str(e)}

    if not users:
        return {"eligible": False, "reason": "invalid_user_info"}

    user = users[0]
    user_info = user.get("user_info", {})
    bookings = user.get("booking_details", [])

    is_highspender = False
    is_student = False

    for b in bookings:
        h_high = normalize_bool(b.get("HIGHSPENDERHIGHFREQ", False))
        h_low = normalize_bool(b.get("HIGHSPENDERLOWFREQ", False))
        student = normalize_student(b.get("STUDENT", 0))

        if h_high or h_low:
            is_highspender = True

        if student:
            is_student = True

    eligible = is_highspender or is_student

    if eligible:
        return {
            "eligible": True,
            "user_info": {
                "USR_FIRSTNAME": user_info.get("USR_FIRSTNAME", ""),
                "USR_LASTNAME": user_info.get("USR_LASTNAME", ""),
                "USR_EMAIL": user_info.get("USR_EMAIL", ""),
                "USR_MOBILE": user_info.get("USR_MOBILE", ""),
                "USR_GUID": user_info.get("USR_GUID", "")
            }
        }

    return {"eligible": False}


# MCP wrapper
def validate_request(last_name: str, email_or_phone: str):
    return check_user_autorecovery_eligibility(last_name, email_or_phone)


def main():
    print("\n=== Autorecovery Eligibility Checker ===\n")

    last_name = input("Enter Last Name: ").strip()
    email_or_phone = input("Enter Email or Phone: ").strip()

    if not last_name or not email_or_phone:
        print("Both fields are required")
        return

    try:
        result = validate_request(last_name, email_or_phone)
        print("\nResult:\n")
        print(json.dumps(result, indent=2))
    except Exception as e:
        print("\nError occurred:")
        print(str(e))


if __name__ == "__main__":
    main()