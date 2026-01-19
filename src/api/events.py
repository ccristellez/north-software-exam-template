"""
Event publishing using Redis Streams.

Redis Streams work like a log - events are appended and consumers can read them.
This is useful for:
- Decoupling the API from downstream processing
- Building alerting systems
- Analytics pipelines
- Audit logging

Stream name: "congestion:events"
Event types: "ping_received", "high_congestion"
"""
import redis
from datetime import datetime, timezone


# Stream configuration
STREAM_NAME = "congestion:events"
MAX_STREAM_LENGTH = 10000  # Keep last 10k events (prevents unbounded growth)


def publish_ping_event(
    redis_client: redis.Redis,
    device_id: str,
    cell_id: str,
    lat: float,
    lon: float,
    bucket: int,
    vehicle_count: int
) -> str:
    """
    Publish a ping event to the Redis stream.

    Args:
        redis_client: Redis connection
        device_id: Device that sent the ping
        cell_id: H3 cell where the ping landed
        lat: Latitude
        lon: Longitude
        bucket: Time bucket number
        vehicle_count: Current count of vehicles in this cell

    Returns:
        Event ID assigned by Redis (e.g., "1234567890123-0")
    """
    event_data = {
        "event_type": "ping_received",
        "device_id": device_id,
        "cell_id": cell_id,
        "lat": str(lat),
        "lon": str(lon),
        "bucket": str(bucket),
        "vehicle_count": str(vehicle_count),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    # XADD appends to the stream
    # MAXLEN ~ 10000 keeps approximately 10k events (the ~ means "approximately" for performance)
    event_id = redis_client.xadd(
        STREAM_NAME,
        event_data,
        maxlen=MAX_STREAM_LENGTH,
        approximate=True
    )

    return event_id


def publish_high_congestion_alert(
    redis_client: redis.Redis,
    cell_id: str,
    vehicle_count: int,
    lat: float,
    lon: float
) -> str:
    """
    Publish a high congestion alert to the stream.

    This is a separate event type that consumers can filter for
    to trigger notifications, dashboards, etc.

    Args:
        redis_client: Redis connection
        cell_id: H3 cell with high congestion
        vehicle_count: Number of vehicles
        lat: Center latitude of the cell
        lon: Center longitude of the cell

    Returns:
        Event ID assigned by Redis
    """
    event_data = {
        "event_type": "high_congestion",
        "cell_id": cell_id,
        "vehicle_count": str(vehicle_count),
        "lat": str(lat),
        "lon": str(lon),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    event_id = redis_client.xadd(
        STREAM_NAME,
        event_data,
        maxlen=MAX_STREAM_LENGTH,
        approximate=True
    )

    return event_id


def read_events(
    redis_client: redis.Redis,
    last_id: str = "0",
    count: int = 100,
    block_ms: int = None
) -> list:
    """
    Read events from the stream.

    Args:
        redis_client: Redis connection
        last_id: Read events after this ID ("0" for all, "$" for only new)
        count: Maximum number of events to return
        block_ms: If set, block for this many milliseconds waiting for new events

    Returns:
        List of (event_id, event_data) tuples
    """
    if block_ms is not None:
        # Blocking read - waits for new events
        result = redis_client.xread(
            {STREAM_NAME: last_id},
            count=count,
            block=block_ms
        )
    else:
        # Non-blocking read
        result = redis_client.xread(
            {STREAM_NAME: last_id},
            count=count
        )

    # xread returns: [(stream_name, [(id, data), (id, data), ...])]
    # We want just the events list
    if not result:
        return []

    # result[0] is (stream_name, events_list)
    events = result[0][1]
    return events


def get_stream_length(redis_client: redis.Redis) -> int:
    """Get the current number of events in the stream."""
    return redis_client.xlen(STREAM_NAME)
