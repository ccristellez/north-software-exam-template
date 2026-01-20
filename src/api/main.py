"""
Congestion Monitor API
FastAPI application for tracking vehicle congestion using H3 hexagonal grid system.
"""
from fastapi import FastAPI, Response, HTTPException
from redis.exceptions import RedisError
from datetime import datetime, timezone
from prometheus_client import generate_latest, CONTENT_TYPE_LATEST
import time
from typing import List

from src.api.redis_client import get_redis_client
from src.api.models import Ping, BatchPingRequest
from src.api.time_utils import current_bucket, WINDOW_SECONDS
from src.api.grid import latlon_to_cell, get_neighbor_cells
from src.api import metrics
from src.api import events
from src.api import congestion as cong


# Rate limiting configuration
RATE_LIMIT_WINDOW_SECONDS = 60  # 1-minute window
RATE_LIMIT_MAX_REQUESTS = 100   # Max pings per device per minute


def check_rate_limit(r, device_id: str) -> bool:
    """
    Check if device has exceeded rate limit using sliding window counter.

    Uses Redis INCR with TTL for a simple but effective rate limiter.

    Args:
        r: Redis client
        device_id: Device identifier

    Returns:
        True if within rate limit, False if exceeded
    """
    key = f"ratelimit:{device_id}"

    # Increment counter, set TTL on first request
    count = r.incr(key)
    if count == 1:
        r.expire(key, RATE_LIMIT_WINDOW_SECONDS)

    return count <= RATE_LIMIT_MAX_REQUESTS


def flush_completed_bucket_to_history(r, cell_id: str, current_bucket: int) -> bool:
    """
    Save completed bucket data to the history table.

    When a new ping arrives, this function checks if the previous bucket's data
    should be saved to bucket_history in Supabase. This raw data is later used
    for percentile-based congestion detection.

    Args:
        r: Redis client
        cell_id: H3 cell ID
        current_bucket: The current time bucket number

    Returns:
        True if history was saved, False otherwise
    """
    prev_bucket = current_bucket - 1

    # Check if we already saved this bucket (use a flag key)
    saved_flag_key = f"cell:{cell_id}:bucket:{prev_bucket}:history_saved"
    if r.exists(saved_flag_key):
        return False  # Already saved

    # Check if previous bucket has any data
    prev_key = f"cell:{cell_id}:bucket:{prev_bucket}"
    prev_count = int(r.scard(prev_key) or 0)

    if prev_count == 0:
        return False  # No data to save

    # Get speed data from previous bucket
    prev_speeds = cong.get_bucket_speeds(r, cell_id, prev_bucket)
    prev_avg_speed = sum(prev_speeds) / len(prev_speeds) if prev_speeds else None

    # Calculate bucket start time from bucket number
    bucket_time = datetime.fromtimestamp(prev_bucket * WINDOW_SECONDS, tz=timezone.utc)

    # Save to history table
    cong.save_bucket_to_history(cell_id, bucket_time, prev_count, prev_avg_speed)

    # Mark as saved (TTL slightly longer than bucket TTL to ensure flag persists)
    r.setex(saved_flag_key, 600, "1")  # 10 minute TTL

    return True

# Initialize FastAPI application
app = FastAPI(
    title="Congestion Monitor",
    description="Real-time traffic congestion monitoring using H3 hexagonal spatial indexing",
    version="1.0.0"
)


@app.get("/metrics")
def get_metrics():
    """
    Prometheus metrics endpoint.

    Returns:
        Response: Prometheus-formatted metrics
    """
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/health")
def health():
    """
    Health check endpoint.

    Returns:
        dict: API status and Redis connection status
    """
    try:
        redis_client = get_redis_client()
        redis_client.ping()
        redis_status = "connected"
        metrics.redis_operations_total.labels(operation="ping", status="success").inc()
    except RedisError:
        redis_status = "disconnected"
        metrics.redis_operations_total.labels(operation="ping", status="error").inc()

    return {"status": "healthy", "redis": redis_status}


@app.post("/v1/pings")
def create_ping(ping: Ping):
    """
    Record a location ping from a device.

    Process:
    1. Check rate limit for this device
    2. Convert lat/lon to H3 hexagon cell ID
    3. Determine current time bucket (5-minute window)
    4. Save previous bucket to baseline if not already saved (update-on-write)
    5. Increment counter in Redis for this cell + bucket
    6. Set TTL to auto-expire old data

    Args:
        ping: Ping object containing device_id, lat, lon, and optional timestamp

    Returns:
        dict: Confirmation with cell_id, bucket, and current count

    Raises:
        HTTPException 429: If device exceeds rate limit (100 pings/minute)
    """
    start_time = time.time()
    r = get_redis_client()

    # Check rate limit before processing
    if not check_rate_limit(r, ping.device_id):
        metrics.ping_requests_total.labels(status="rate_limited").inc()
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit exceeded. Max {RATE_LIMIT_MAX_REQUESTS} pings per minute per device."
        )

    # Use provided timestamp or current time
    ts = ping.timestamp or datetime.now(timezone.utc)

    # Calculate which 5-minute bucket this ping belongs to
    bucket = current_bucket(ts)

    # Convert GPS coordinates to H3 hexagon ID (resolution 8 = ~460m)
    cell_id = latlon_to_cell(ping.lat, ping.lon)

    # Save previous bucket to history if not already saved
    flush_completed_bucket_to_history(r, cell_id, bucket)

    # Build Redis key: cell:<hex_id>:bucket:<time_bucket>
    key = f"cell:{cell_id}:bucket:{bucket}"

    # Add device to the set of unique devices for this cell+bucket
    # Using a set ensures each device is only counted once per time window
    r.sadd(key, ping.device_id)
    metrics.redis_operations_total.labels(operation="sadd", status="success").inc()

    # Get the count of unique devices in this cell+bucket
    count = r.scard(key)
    metrics.redis_operations_total.labels(operation="scard", status="success").inc()

    # Set expiration to 300 seconds (5 minutes) to auto-clean old data
    r.expire(key, 300)

    # Store speed data if provided (for historical baseline calibration)
    if ping.speed_kmh is not None:
        cong.record_speed(r, cell_id, bucket, ping.speed_kmh)
        metrics.redis_operations_total.labels(operation="rpush", status="success").inc()

    # Record metrics
    metrics.ping_requests_total.labels(status="success").inc()
    metrics.unique_devices_per_bucket.labels(cell_id=cell_id).set(count)
    metrics.request_duration_seconds.labels(endpoint="create_ping").observe(time.time() - start_time)

    # Publish event to Redis Stream
    # This allows other services to react to pings (alerts, analytics, etc.)
    events.publish_ping_event(
        redis_client=r,
        device_id=ping.device_id,
        cell_id=cell_id,
        lat=ping.lat,
        lon=ping.lon,
        bucket=bucket,
        vehicle_count=int(count)
    )

    # If this ping pushed the cell into HIGH congestion, publish an alert
    if count >= 30:
        events.publish_high_congestion_alert(
            redis_client=r,
            cell_id=cell_id,
            vehicle_count=int(count),
            lat=ping.lat,
            lon=ping.lon
        )

    return {
        "message": "Ping received",
        "device_id": ping.device_id,
        "cell_id": cell_id,
        "bucket": bucket,
        "bucket_count": int(count),
    }


@app.post("/v1/pings/batch")
def create_pings_batch(batch: BatchPingRequest):
    """
    Record multiple location pings in a single request.

    Optimized for high-volume ingestion scenarios where individual ping
    requests would be inefficient. Uses Redis pipelines to batch all
    operations into minimal round-trips.

    Features:
    - Single rate limit check per batch (not per ping)
    - Pipeline batching: All Redis ops in 1-2 round-trips
    - Partial success: Reports which pings succeeded/failed

    Args:
        batch: BatchPingRequest containing list of Ping objects (max 1000)

    Returns:
        dict: Summary with processed count and any errors

    Raises:
        HTTPException 429: If any device in batch exceeds rate limit
    """
    start_time = time.time()
    r = get_redis_client()

    # Pre-check rate limits for all unique devices in batch
    unique_devices = set(p.device_id for p in batch.pings)
    rate_limited_devices = []

    for device_id in unique_devices:
        if not check_rate_limit(r, device_id):
            rate_limited_devices.append(device_id)

    if rate_limited_devices:
        metrics.ping_requests_total.labels(status="rate_limited").inc()
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit exceeded for devices: {rate_limited_devices[:5]}{'...' if len(rate_limited_devices) > 5 else ''}"
        )

    # Process all pings using pipeline for efficiency
    pipe = r.pipeline()

    # Pre-calculate all cell IDs and buckets
    ping_data = []
    for ping in batch.pings:
        ts = ping.timestamp or datetime.now(timezone.utc)
        bucket = current_bucket(ts)
        cell_id = latlon_to_cell(ping.lat, ping.lon)
        key = f"cell:{cell_id}:bucket:{bucket}"

        ping_data.append({
            "ping": ping,
            "cell_id": cell_id,
            "bucket": bucket,
            "key": key
        })

        # Queue Redis commands
        pipe.sadd(key, ping.device_id)
        pipe.expire(key, 300)

        if ping.speed_kmh is not None:
            speed_key = cong.get_speed_key(cell_id, bucket)
            pipe.rpush(speed_key, ping.speed_kmh)
            pipe.expire(speed_key, 300)

    # Execute all commands in single round-trip
    pipe.execute()
    metrics.redis_operations_total.labels(operation="pipeline_batch", status="success").inc()

    # Get final counts for each unique cell (for response and events)
    unique_cells = {}
    for pd in ping_data:
        cell_id = pd["cell_id"]
        bucket = pd["bucket"]
        if (cell_id, bucket) not in unique_cells:
            unique_cells[(cell_id, bucket)] = pd["key"]

    # Pipeline to get all counts
    count_pipe = r.pipeline()
    cell_keys = list(unique_cells.items())
    for (cell_id, bucket), key in cell_keys:
        count_pipe.scard(key)

    counts = count_pipe.execute()

    # Build cell count map
    cell_counts = {}
    for i, ((cell_id, bucket), key) in enumerate(cell_keys):
        cell_counts[(cell_id, bucket)] = int(counts[i] or 0)

    # Publish events and check for high congestion
    cells_with_high_congestion = []
    for (cell_id, bucket), count in cell_counts.items():
        if count >= 30:
            cells_with_high_congestion.append(cell_id)

    # Record metrics
    metrics.ping_requests_total.labels(status="success").inc()
    metrics.request_duration_seconds.labels(endpoint="create_pings_batch").observe(time.time() - start_time)

    return {
        "message": "Batch processed",
        "total_pings": len(batch.pings),
        "unique_devices": len(unique_devices),
        "unique_cells": len(unique_cells),
        "high_congestion_cells": cells_with_high_congestion,
        "processing_time_ms": round((time.time() - start_time) * 1000, 2)
    }


@app.get("/v1/congestion")
def congestion(lat: float, lon: float, debug: bool = False):
    """
    Get congestion level for a single hexagon cell.

    Uses historical percentile comparison when available. Each cell's current
    speed is compared to its historical 25th/50th percentiles to determine
    if traffic is worse than typical.

    Process:
    1. Convert lat/lon to H3 cell ID
    2. Look up vehicle count and speeds in current time bucket
    3. Query historical percentiles for this cell
    4. Calculate congestion by comparing to percentiles (or fallback to absolute thresholds)

    Args:
        lat: Latitude
        lon: Longitude
        debug: If True, include calculation details in response

    Returns:
        dict: Cell ID, vehicle count, avg speed, and congestion level (LOW/MODERATE/HIGH)
    """
    start_time = time.time()
    r = get_redis_client()

    # Convert coordinates to H3 hexagon
    cell_id = latlon_to_cell(lat, lon)

    # Get current time bucket
    now = datetime.now(timezone.utc)
    bucket = int(now.timestamp()) // WINDOW_SECONDS

    # Query Redis for unique device count in this cell during current bucket
    key = f"cell:{cell_id}:bucket:{bucket}"
    count = int(r.scard(key) or 0)
    metrics.redis_operations_total.labels(operation="scard", status="success").inc()

    # Get speeds for this bucket
    speeds = cong.get_bucket_speeds(r, cell_id, bucket)
    avg_speed = sum(speeds) / len(speeds) if speeds else None

    # Get historical percentiles for this cell (from Supabase)
    percentiles = cong.get_cell_percentiles(cell_id)

    # Calculate congestion level using percentile comparison
    level, debug_info = cong.calculate_congestion_level(count, avg_speed, percentiles)

    # Record metrics
    metrics.congestion_requests_total.labels(endpoint="congestion", status="success").inc()
    metrics.congestion_level_count.labels(level=level).inc()
    metrics.request_duration_seconds.labels(endpoint="congestion").observe(time.time() - start_time)

    response = {
        "cell_id": cell_id,
        "vehicle_count": count,
        "avg_speed_kmh": round(avg_speed, 1) if avg_speed else None,
        "congestion_level": level,
        "calibrated": percentiles.is_calibrated,
        "window_seconds": WINDOW_SECONDS,
    }

    if debug:
        response["debug"] = debug_info

    return response


@app.get("/v1/congestion/area")
def congestion_area(lat: float, lon: float, radius: int = 1):
    """
    Get congestion for a hexagonal area around the given location.

    Uses H3's grid_disk (k-ring) algorithm to find all hexagons within
    a specified number of hops from the center point. Each cell uses
    historical baseline calibration when available.

    Process:
    1. Convert lat/lon to center H3 cell
    2. Get all neighboring cells within 'radius' hops
    3. Batch query all cells using Redis pipeline (single round-trip)
    4. Aggregate results and calculate area-level metrics

    Args:
        lat: Latitude of center point
        lon: Longitude of center point
        radius: Number of hexagon hops (1 = 7 cells, 2 = 19 cells, 3 = 37 cells)

    Returns:
        dict: Area-level congestion with per-cell breakdown
            - center_cell: H3 ID of the center hexagon
            - radius: Number of hops queried
            - total_cells: Number of hexagons in the area
            - area_congestion_level: Overall congestion (LOW/MODERATE/HIGH)
            - total_vehicles: Sum of all vehicles in the area
            - avg_vehicles_per_cell: Average count across all cells
            - avg_speed_kmh: Average speed across all cells with speed data
            - high_congestion_cells: Count of cells with HIGH congestion
            - cells: List of individual cell data, sorted by count

    Examples:
        radius=0: Query only the center cell (1 hexagon)
        radius=1: Query center + immediate neighbors (7 hexagons)
        radius=2: Query 2-hop neighborhood (19 hexagons, ~1km area)
        radius=3: Query 3-hop neighborhood (37 hexagons, ~1.5km area)
    """
    start_time = time.time()
    r = get_redis_client()

    # Convert coordinates to center H3 hexagon
    center_cell_id = latlon_to_cell(lat, lon)

    # Get all hexagons within 'radius' hops (includes center)
    # This uses H3's grid_disk algorithm for efficient neighbor finding
    area_cells = list(get_neighbor_cells(center_cell_id, k=radius))

    # Get current time bucket
    now = datetime.now(timezone.utc)
    bucket = int(now.timestamp()) // WINDOW_SECONDS

    # ==========================================================================
    # OPTIMIZATION: Use Redis pipeline to batch all queries into single round-trip
    # Before: N cells × 2 commands = 2N network round-trips
    # After:  1 pipeline with 2N commands = 1 network round-trip
    # For radius=2 (19 cells): 38 round-trips → 1 round-trip
    # ==========================================================================
    pipe = r.pipeline()

    # Queue all SCARD commands (vehicle counts)
    for cell_id in area_cells:
        key = f"cell:{cell_id}:bucket:{bucket}"
        pipe.scard(key)

    # Queue all LRANGE commands (speed readings)
    for cell_id in area_cells:
        speed_key = cong.get_speed_key(cell_id, bucket)
        pipe.lrange(speed_key, 0, -1)

    # Execute all commands in single round-trip
    results = pipe.execute()
    metrics.redis_operations_total.labels(operation="pipeline", status="success").inc()

    # Split results: first half is counts, second half is speeds
    num_cells = len(area_cells)
    counts = results[:num_cells]
    speed_lists = results[num_cells:]

    # Process results
    cell_data = []
    total_count = 0
    all_speeds = []

    for i, cell_id in enumerate(area_cells):
        count = int(counts[i] or 0)
        total_count += count

        # Parse speed data
        raw_speeds = speed_lists[i]
        speeds = [float(s) for s in raw_speeds] if raw_speeds else []
        avg_speed = sum(speeds) / len(speeds) if speeds else None
        if speeds:
            all_speeds.extend(speeds)

        # Get percentiles and calculate congestion level (from Supabase)
        percentiles = cong.get_cell_percentiles(cell_id)
        level, _ = cong.calculate_congestion_level(count, avg_speed, percentiles)

        metrics.congestion_level_count.labels(level=level).inc()

        # Store cell data
        cell_data.append({
            "cell_id": cell_id,
            "count": count,
            "avg_speed_kmh": round(avg_speed, 1) if avg_speed else None,
            "level": level,
            "calibrated": percentiles.is_calibrated,
            "is_center": cell_id == center_cell_id
        })

    # Sort cells by vehicle count (highest congestion first)
    cell_data.sort(key=lambda x: x["count"], reverse=True)

    # Calculate area-level metrics
    avg_count = total_count / len(area_cells) if area_cells else 0
    area_avg_speed = sum(all_speeds) / len(all_speeds) if all_speeds else None
    high_congestion_cells = sum(1 for c in cell_data if c["level"] == "HIGH")

    # Determine overall area congestion level
    # HIGH if: average is high OR multiple cells are congested
    # MODERATE if: average is moderate OR at least one cell is HIGH
    # LOW otherwise
    if avg_count >= 30 or high_congestion_cells >= 3:
        area_level = "HIGH"
    elif avg_count >= 10 or high_congestion_cells >= 1:
        area_level = "MODERATE"
    else:
        area_level = "LOW"

    # Record metrics
    metrics.congestion_requests_total.labels(endpoint="congestion_area", status="success").inc()
    metrics.request_duration_seconds.labels(endpoint="congestion_area").observe(time.time() - start_time)

    return {
        "center_cell": center_cell_id,
        "radius": radius,
        "total_cells": len(area_cells),
        "area_congestion_level": area_level,
        "total_vehicles": total_count,
        "avg_vehicles_per_cell": round(avg_count, 1),
        "avg_speed_kmh": round(area_avg_speed, 1) if area_avg_speed else None,
        "high_congestion_cells": high_congestion_cells,
        "cells": cell_data,
        "window_seconds": WINDOW_SECONDS
    }


@app.get("/v1/history")
def get_cell_history(lat: float, lon: float):
    """
    Get historical percentile data for a cell.

    Returns the historical traffic percentiles for the hexagon at the given
    location. These percentiles are used for congestion detection by comparing
    current conditions to historical patterns.

    Args:
        lat: Latitude
        lon: Longitude

    Returns:
        dict: Historical percentiles for this cell
    """
    cell_id = latlon_to_cell(lat, lon)
    percentiles = cong.get_cell_percentiles(cell_id)

    return {
        "cell_id": cell_id,
        "speed_p25_kmh": round(percentiles.speed_p25, 1) if percentiles.speed_p25 else None,
        "speed_p50_kmh": round(percentiles.speed_p50, 1) if percentiles.speed_p50 else None,
        "count_p75": round(percentiles.count_p75, 1) if percentiles.count_p75 else None,
        "sample_count": percentiles.sample_count,
        "is_calibrated": percentiles.is_calibrated,
        "min_samples_required": cong.MIN_SAMPLES_FOR_PERCENTILES
    }


@app.post("/v1/history/save")
def save_bucket_to_history(lat: float = None, lon: float = None, cell_id: str = None):
    """
    Manually save current bucket data to the history table.

    This endpoint is useful for demos and testing. In production, history
    is saved automatically when new pings arrive (update-on-write pattern).

    Args:
        lat: Latitude (optional if cell_id provided)
        lon: Longitude (optional if cell_id provided)
        cell_id: H3 cell ID (optional if lat/lon provided)

    Returns:
        dict: Saved bucket data and updated percentiles
    """
    r = get_redis_client()

    # Get cell_id from lat/lon if not provided directly
    if cell_id is None:
        if lat is None or lon is None:
            return {"error": "Must provide either cell_id or lat/lon"}
        cell_id = latlon_to_cell(lat, lon)

    # Get current bucket data from Redis
    now = datetime.now(timezone.utc)
    bucket = int(now.timestamp()) // WINDOW_SECONDS

    key = f"cell:{cell_id}:bucket:{bucket}"
    count = int(r.scard(key) or 0)

    speeds = cong.get_bucket_speeds(r, cell_id, bucket)
    avg_speed = sum(speeds) / len(speeds) if speeds else None

    # Calculate bucket start time
    bucket_time = datetime.fromtimestamp(bucket * WINDOW_SECONDS, tz=timezone.utc)

    # Save to history table
    saved = cong.save_bucket_to_history(cell_id, bucket_time, count, avg_speed)

    # Get updated percentiles
    percentiles = cong.get_cell_percentiles(cell_id)

    return {
        "message": "Bucket saved to history" if saved else "Failed to save (may already exist)",
        "cell_id": cell_id,
        "bucket_time": bucket_time.isoformat(),
        "bucket_count": count,
        "bucket_avg_speed": round(avg_speed, 1) if avg_speed else None,
        "current_percentiles": {
            "speed_p25_kmh": round(percentiles.speed_p25, 1) if percentiles.speed_p25 else None,
            "speed_p50_kmh": round(percentiles.speed_p50, 1) if percentiles.speed_p50 else None,
            "count_p75": round(percentiles.count_p75, 1) if percentiles.count_p75 else None,
            "sample_count": percentiles.sample_count,
            "is_calibrated": percentiles.is_calibrated
        }
    }