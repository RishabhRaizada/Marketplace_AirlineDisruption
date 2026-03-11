# RUNTIME_CONFIG = {
#     "default": {
#         "recovery_agent_id": "asst_0ywRqan9UlWxRSM3bcgpJmDh",
#         "message_agent_id": "asst_BiR67fIqqynuIHTI3VBTM7rr",
#         "prompt_append": ""
#     }
# }


# def get_runtime_config(tenant_id: str):
#     return RUNTIME_CONFIG.get(tenant_id, RUNTIME_CONFIG["default"])


# def update_runtime_config(tenant_id: str, data: dict):
#     if tenant_id not in RUNTIME_CONFIG:
#         RUNTIME_CONFIG[tenant_id] = {}

#     RUNTIME_CONFIG[tenant_id].update(data)
from config.cosmos_data_fetcher import (
    fetch_active_agents,
    fetch_active_prompt_payload
)

DEFAULT_RUNTIME = {
    "recovery_agent_id": None,
    "message_agent_id": None,
    "prompt_append": ""
}


def get_runtime_config(tenant_id: str):

    runtime = DEFAULT_RUNTIME.copy()

    agents = fetch_active_agents()

    for agent in agents:

        if agent.get("is_active_recovery"):
            runtime["recovery_agent_id"] = agent.get("agent_id")

        if agent.get("is_active_messaging"):
            runtime["message_agent_id"] = agent.get("agent_id")

    prompt = fetch_active_prompt_payload()

    if prompt:
        runtime["addon_system_prompt"] = prompt.get("system_prompt", "")
        runtime["addon_user_prompt"] = prompt.get("user_prompt", "")

    return runtime


def update_runtime_config(tenant_id: str, data: dict):
    raise Exception(
        "Runtime config must be updated through the admin panel / Cosmos DB"
    )