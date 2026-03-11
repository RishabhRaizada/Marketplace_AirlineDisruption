import json
import time
import requests

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from fastapi.middleware.cors import CORSMiddleware

from config.runtime_config import get_runtime_config, update_runtime_config

from azure.ai.projects import AIProjectClient
from azure.identity import DefaultAzureCredential, AzureCliCredential
from azure.ai.agents.models import ListSortOrder, RunStatus


PROJECT_ENDPOINT = "https://marketplace-aifoundry.services.ai.azure.com/api/projects/proj-default"

MCP_URL = "http://localhost:8004/mcp"

app = FastAPI(title="Flight Disruption API")


# =================================================
# REQUEST MODELS
# =================================================

class DisruptionRequest(BaseModel):
    pnr: str
    last_name: str
    tenant_id: str


class AdminConfig(BaseModel):
    tenant_id: str
    recovery_agent_id: str | None = None
    message_agent_id: str | None = None
    system_prompt: str | None = None
    text: str | None = None


# =================================================
# SAFE JSON EXTRACTION FROM AGENT
# =================================================

def safe_json_from_agent(text: str) -> dict:

    if not text or not text.strip():
        raise ValueError("Agent returned empty response")

    text = text.strip()

    if text.startswith("```"):
        parts = text.split("```")
        if len(parts) >= 2:
            text = parts[1].strip()
            if text.startswith("json"):
                text = text[4:].strip()

    start = text.find("{")
    end = text.rfind("}")

    if start == -1 or end == -1:
        raise ValueError(f"No JSON object found in agent output:\n{text}")

    return json.loads(text[start:end + 1])


# =================================================
# MCP TOOL EXECUTION
# =================================================

def execute_mcp_tool(tool_name: str, arguments: dict):

    payload = {
        "jsonrpc": "2.0",
        "id": "ui-call",
        "method": "tools/call",
        "params": {
            "name": tool_name,
            "arguments": arguments
        }
    }

    headers = {
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json"
    }

    response = requests.post(
        MCP_URL,
        json=payload,
        headers=headers,
        timeout=30
    )

    if response.status_code != 200:
        raise RuntimeError("MCP call failed")

    for line in response.text.splitlines():

        if not line.startswith("data:"):
            continue

        raw = line.replace("data:", "", 1).strip()

        try:
            payload = json.loads(raw)
        except Exception:
            continue

        result = payload.get("result", {})

        structured = result.get("structuredContent")

        if structured:
            for item in structured.get("content", []):
                if item.get("type") == "json":
                    return item["json"]

        for item in result.get("content", []):

            if item.get("type") == "text":

                try:
                    embedded = json.loads(item["text"])

                    for c in embedded.get("content", []):

                        if c.get("type") == "json":
                            return c["json"]

                except Exception:
                    pass

    raise RuntimeError("No valid MCP JSON found")


# =================================================
# CORS
# =================================================

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# =================================================
# AZURE AUTH
# =================================================

try:
    credential = DefaultAzureCredential()
    credential.get_token("https://management.azure.com/.default")
except Exception:
    credential = AzureCliCredential()


# =================================================
# AGENT EXECUTION
# =================================================

def run_agent(flow: str, tenant_id: str, mcp_data: dict):

    runtime = get_runtime_config(tenant_id)

    agent_id = (
        runtime["recovery_agent_id"]
        if flow == "recovery"
        else runtime["message_agent_id"]
    )

    client = AIProjectClient(
        endpoint=PROJECT_ENDPOINT,
        credential=credential
    )

    with client:

        thread = client.agents.threads.create()

        # =================================================
        # PROMPT PLACEHOLDER
        # =================================================

        if flow == "recovery":
            recovery = mcp_data["recovery"]
            # -------------------------------------------------
            # TODO: Add recovery prompt here
            # -------------------------------------------------
            prompt = f"""
You are a STRICT Flight & Seat Recovery Decision Engine.
Passenger Profile:
--------------------------------
INPUT DATA (ACTUAL MCP RESPONSE - THIS IS YOUR UNIVERSE)
--------------------------------

Passenger Profile:
{json.dumps(mcp_data.get("profile", []), indent=2)}

Original Flight:
{json.dumps(mcp_data.get('original_flight', {}), indent=2)}

Available Flights (YOU MUST SELECT FROM THIS LIST):
{json.dumps(recovery.get('available_flights', []), indent=2)}

Available Seats (YOU MUST SELECT FROM THIS LIST):
{json.dumps(recovery.get('available_seats', []), indent=2)}
You are a STRICT Flight & Seat Optimization Engine.

ABSOLUTE RULES (FAIL IF VIOLATED):
1. You MUST ONLY use flights and seats provided in the input JSON.
2. You MUST NOT invent flight_uid, flight_number, seat_number, or prices.
3. If any identifier is not found in the input → FAIL.
--------------------------------
ORIGINAL BOOKING CONSTRAINTS (MANDATORY)
--------------------------------

The original_flight represents the passenger's contractual booking intent.

MANDATORY RULES:

1. Route Preservation:
- selected_flight.origin MUST equal original_flight.origin
- selected_flight.destination MUST equal original_flight.destination
- If no such flight exists → FAIL

2. Time Proximity:
- Prefer flights whose utcDeparture is closest to original_flight.utc_scheduled_departure
- Prefer earlier arrival over later arrival when possible

3. Cabin Preservation:
- If original_flight.cabin_class == "Business":
  - Must preserve Business cabin in recovery
  - Only downgrade if no business seats exist
- If original_flight.cabin_class == "Economy":
  - Economy acceptable, upgrade optional for highspender

These rules apply BEFORE CDP logic.
CDP rules apply only after these booking constraints are satisfied.

--------------------------------
PRIORITY 0 (ORIGINAL BOOKING OVERRIDE)
--------------------------------

If original_flight.cabin_class == "Business":
- ALWAYS select a flight that has min_business_fare available
- ALWAYS select a seat with travel_class == "C"
- This rule OVERRIDES STUDENT logic
- Only downgrade to Economy if NO business seats exist


If student > 0 AND original_flight.cabin_class == "Economy":
- MUST NOT select travel_class == "C"
- MUST select economy class only
- FAIL if only business seats are selected

If original_flight.cabin_class == "Economy":
- Proceed with CDP rules as defined



--------------------------------
CDP PRIORITY ORDER (MANDATORY)
--------------------------------

Evaluate booking_details[0] first.

PRIORITY 1 (OVERRIDES EVERYTHING):
- If STUDENT > 0:
  - ALWAYS choose the CHEAPEST min_economy_fare flight
  - NEVER choose business class
  - Seat priority: cheapest economy seat, ignore comfort
  - Comfort signals (LEGROOM, XL, AISLE) are SECONDARY

- If HIGHSPENDERHIGHFREQ == true OR HIGHSPENDERLOWFREQ == true:
  - Price is IRRELEVANT
  - Prefer comfort, stretch, business class
  - Prefer earlier arrival and non-stop

--------------------------------
PRIORITY 2 (ONLY IF NOT STUDENT / HIGHSPENDER)
--------------------------------

Journey Intent:
- BUSINESS > LEISURE → time + comfort
- LEISURE >= BUSINESS → cost + flexibility

--------------------------------
FLIGHT SCORING (STRICT)
--------------------------------

For EACH available flight:

Start score = 0

Base:
+40 if NonStop
+25 if utcArrival earlier than original flight
+20 if utcDeparture closest to original flight
-15 if fillingFast == true

STUDENT OVERRIDE:
- score = -min_economy_fare
- IGNORE all comfort bonuses

HIGHSPENDER OVERRIDE:
+40 if isStretch == true
+30 if min_business_fare exists

--------------------------------
SEAT SCORING (STRICT)
--------------------------------

For EACH seat on SELECTED flight:

Start score = 0

STUDENT OVERRIDE:
- Prefer travel_class == "Y"
- Ignore LEGROOM, XL, WINDOW, AISLE
- Pick seat with highest availability or lowest cost proxy

HIGHSPENDER OVERRIDE:
+40 if travel_class == "C"
+25 if LEGROOM
+20 if XL
+15 if AISLE or WINDOW

--------------------------------
OUTPUT (STRICT JSON ONLY)
--------------------------------

{{
  "cdp_summary": {{
    "student": number,
    "highspender": boolean,
    "business": number,
    "leisure": number
  }},
  "selected_flight": {{ ... }},
  "selected_seat": {{ ... }},
  "reasoning": {{
    "flight_reason": "......",
    "seat_reason": "....."
  }}
}}

FAIL IF:
- Cheapest flight is NOT selected for STUDENT
- Business class is selected for STUDENT
- Any invented ID appears


"""


        else:

            # -------------------------------------------------
            # TODO: Add messaging prompt here
            # -------------------------------------------------
            prompt = f"""
CRITICAL RULES (MUST FOLLOW):
- You MUST NOT call any tools
- You MUST NOT request any actions
- You MUST NOT ask follow-up questions
- You MUST NOT invent messages
- You MUST use ONLY the data provided in MCP CONTEXT
- You MUST return ONLY valid JSON (no markdown, no explanation)
- ALL instances of `{{var1}}` must be replaced with the actual flight origin.
- ALL instances of `{{var2}}` must be replaced with the actual flight destination.
- You MAY personalize messages with appropriate greetings based on the passenger persona.

PERSONA SELECTION RULES (MANDATORY):
- If defense_flag = 1 → persona = DEFENCE
- Else if Doctor flag = 1 → persona = DOCTOR
- Else if student_flag >= 1 → persona = STUDENT
- Else → persona = GENERAL
You MUST choose messages ONLY from the group_id matching that persona.

ROLE:
You are a flight communication personalization agent.

INPUT:
You are given a resolved MCP context containing:
- Flight event details
   - `flight_info`: Object containing:
     - `origin`: Departure city/airport
     - `destination`: Arrival city/airport
- Passenger CDP persona
- Eligible message templates grouped by group_id

TASK:
0. The flight origin and destination from `event.flight_info.origin` and `event.flight_info.destination`.
1. Infer the passenger persona using CDP profile
2. Select the SINGLE best group_id
3. Return ALL messages belonging to that group_id
4. **Replace** all placeholders in the messages:
   - Replace `{{var1}}` with the flight origin
   - Replace `{{var2}}` with the flight destination
5. **Personalize** the messages with appropriate greetings (e.g., "Dear Doctor", "Dear Officer") based on the identified persona.

OUTPUT FORMAT (JSON ONLY):
{{
  "selected_group_id": "MSG-XXXX",
  "reason": "Why this group was chosen based on CDP + event",
  "messages": [
    {{
      "id": "MSG-XXXX-SMS",
      "channel": "sms",
      "message": "..."
    }},
    {{
      "id": "MSG-XXXX-WA",
      "channel": "whatsapp",
      "message": "..."
    }},
    {{
      "id": "MSG-XXXX-EMAIL",
      "channel": "email",
      "message": "..."
    }}
  ]
}}

MCP CONTEXT:
{json.dumps(mcp_data, indent=2)}
"""
    

        # -------------------------------------------------
        # ADMIN PROMPT APPEND
        # -------------------------------------------------

        admin_system = runtime.get("system_prompt", "")
        admin_user = runtime.get("text", "")
        print("RUNTIME CONFIG:", runtime)
        if admin_system:
            prompt = f"{admin_system}\n\n{prompt}"

        if admin_user:
            prompt = f"{prompt}\n\nADMIN INSTRUCTIONS:\n{admin_user}"
        client.agents.messages.create(
            thread_id=thread.id,
            role="user",
            content=prompt
        )

        run = client.agents.runs.create(
            thread_id=thread.id,
            agent_id=agent_id,
            temperature=0.1,
            top_p=0.1
        )

        start = time.time()

        while True:

            run = client.agents.runs.get(thread.id, run.id)

            if run.status == RunStatus.COMPLETED:
                break

            if time.time() - start > 120:
                raise TimeoutError("Agent timeout")

            time.sleep(1)

        messages = client.agents.messages.list(
            thread_id=thread.id,
            order=ListSortOrder.ASCENDING
        )

        for msg in reversed(list(messages)):

            if msg.role == "assistant":

                if not msg.text_messages:
                    raise RuntimeError("Agent returned no text messages")

                raw = msg.text_messages[0].text.value

                return safe_json_from_agent(raw)

    raise RuntimeError("Agent failed")


# =================================================
# EVENT FORMATTER
# =================================================

def build_event_payload(mcp_data: dict):

    flight = mcp_data.get("event", {}).get("original_flight", {})

    return {
        "event_type": mcp_data.get("event", {}).get("event_type"),
        "flight_number": flight.get("flight_number"),
        "origin": flight.get("origin"),
        "destination": flight.get("destination"),
        "scheduled_departure_time": flight.get("scheduled_departure_time"),
        "scheduled_arrival_time": flight.get("scheduled_arrival_time"),
        "utc_scheduled_departure": flight.get("utc_scheduled_departure"),
        "utc_scheduled_arrival": flight.get("utc_scheduled_arrival")
    }


# =================================================
# MAIN API
# =================================================

@app.post("/disruption")
def handle_disruption_api(req: DisruptionRequest):

    try:

        mcp_data = execute_mcp_tool(
            "handle_disruption",
            {
                "pnr": req.pnr,
                "last_name": req.last_name,
                "tenant_id": req.tenant_id,
            }
        )

        if mcp_data.get("status") != "success":
            return mcp_data

        flow = mcp_data.get("flow")

        agent_output = run_agent(
            flow,
            req.tenant_id,
            mcp_data
        )

        event_payload = build_event_payload(mcp_data)

        return {
            "status": "success",
            "flow": flow,
            "event": event_payload,
            "result": agent_output
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# =================================================
# ADMIN PANEL APIs
# =================================================

@app.post("/admin/update-config")
def update_config(cfg: AdminConfig):

    update_runtime_config(
        cfg.tenant_id,
        {
            k: v
            for k, v in cfg.model_dump().items()
            if k != "tenant_id" and v is not None
        }
    )

    return {"status": "updated"}


@app.get("/admin/config/{tenant_id}")
def get_config(tenant_id: str):

    return get_runtime_config(tenant_id)