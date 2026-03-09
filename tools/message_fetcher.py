import json
from config.cosmos_data_fetcher import fetch_active_template


def fetch_all_messages():

    template = fetch_active_template()

    if not template:
        return {
            "total_messages": 0,
            "messages": []
        }

    raw_content = template.get("content", {}).get("raw_content")

    if not raw_content:
        return {
            "total_messages": 0,
            "messages": []
        }

    data = json.loads(raw_content)

    message_groups = data.get("MESSAGES", [])
    results = []

    for group in message_groups:
        for ch in group.get("channels", []):
            results.append({
                "group_id": group.get("group_id"),
                "delay_count": group.get("delay_count"),
                "id": ch.get("id"),
                "channel": ch.get("channel"),
                "message": ch.get("message")
            })

    return {
        "total_messages": len(results),
        "messages": results
    }

if __name__ == "__main__":
    result = fetch_all_messages()
    print(json.dumps(result, indent=2))