import httpx
from config.settings import TIMEOUT

client = httpx.AsyncClient(
    timeout=httpx.Timeout(TIMEOUT),
    limits=httpx.Limits(
        max_connections=100,
        max_keepalive_connections=20
    )
)