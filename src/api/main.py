from fastapi import FastAPI
from redis.exceptions import RedisError

from src.api.redis_client import get_redis_client
from src.api.models import Ping
from src.api.time_utils import current_bucket
from src.api.grid import latlon_to_cell
from datetime import datetime, timezone

from src.api.grid import latlon_to_cell
from src.api.time_utils import WINDOW_SECONDS
from datetime import datetime, timezone

app = FastAPI(title="Congestion Monitor")

@app.get("/health")
def health():
    try:
        redis_client = get_redis_client()
        redis_client.ping()
        redis_status = "connected"
    except RedisError:
        redis_status = "disconnected"

    return {"status": "healthy", "redis": redis_status}

@app.post("/v1/pings")
def create_ping(ping: Ping):
    r = get_redis_client()
    ts = ping.timestamp or datetime.now(timezone.utc)
    bucket = current_bucket(ts)
    cell_id = latlon_to_cell(ping.lat, ping.lon)

    key = f"cell:{cell_id}:bucket:{bucket}"

    count = r.incr(key)
    r.expire(key, 300)

    return {
        "message": "Ping received",
        "device_id": ping.device_id,
        "cell_id": cell_id,
        "bucket": bucket,
        "bucket_count": int(count),
    }


@app.get("/v1/pings/count")
def ping_count():
    r = get_redis_client()
    val = r.get("pings:total")
    return {"total_pings": int(val or 0)}


@app.get("/v1/congestion")
def congestion(lat: float, lon: float):
    r = get_redis_client()

    cell_id = latlon_to_cell(lat, lon)

    # current bucket based on "now" (server time)
    now = datetime.now(timezone.utc)
    current = int(now.timestamp()) // WINDOW_SECONDS

    # sum last 5 buckets (25 minutes would be 5*300, but we want last 5 minutes, so 1 bucket)
    # We'll sum current bucket only for now (simple and correct for 5-minute buckets).
    key = f"cell:{cell_id}:bucket:{current}"
    count = int(r.get(key) or 0)

    if count >= 30:
        level = "HIGH"
    elif count >= 10:
        level = "MODERATE"
    else:
        level = "LOW"


    return {
        "cell_id": cell_id,
        "vehicle_count": count,
        "congestion_level": level,
        "window_seconds": WINDOW_SECONDS,
    }

