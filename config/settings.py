import os

AIRLINE_API_BASE_URL = os.getenv("AIRLINE_API_BASE_URL", "https://mock-flightdetails-api.niceflower-39d2e00e.southindia.azurecontainerapps.io")
CDP_API_BASE_URL = os.getenv("CDP_API_BASE_URL", "https://cdp-mock-api.niceflower-39d2e00e.southindia.azurecontainerapps.io/")

AIRLINE_API_KEY = os.getenv("AIRLINE_API_KEY", "test-key")
CDP_API_KEY = os.getenv("CDP_API_KEY", "cdp-test-key")

TIMEOUT = int(os.getenv("TIMEOUT", 30))

DISRUPTION_API_BASE_URL = os.getenv(
    "DISRUPTION_API_BASE_URL",
    "https://disruption-mock-api1.niceflower-39d2e00e.southindia.azurecontainerapps.io/"
)


DISRUPTION_API_KEY = os.getenv(
    "DISRUPTION_API_KEY",
    "disruption-test-key"
)


# python -m uvicorn apis.mock_cdp_server:app --host 0.0.0.0 --port 9100 --reload
# python -m uvicorn apis.mock_disruption_server:app --host 0.0.0.0 --port 9200 --reload
# python -m uvicorn apis.mock_flight_server:app --host 0.0.0.0 --port 9001 --reload

# docker run --env-file .env -p 9200:9200 airline-disruption-api