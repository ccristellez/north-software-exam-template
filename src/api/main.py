"""
Congestion Monitor API
FastAPI application for tracking vehicle congestion using H3 hexagonal grid system.
"""
from fastapi import FastAPI, Response
from redis.exceptions import RedisError
from datetime import datetime, timezone
from prometheus_client import generate_latest, CONTENT_TYPE_LATEST
import time

from src.api.redis_client import get_redis_client
from src.api.models import Ping
from src.api.time_utils import current_bucket, WINDOW_SECONDS
from src.api.grid import latlon_to_cell, get_neighbor_cells
from src.api import metrics
from src.api import events

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
    1. Convert lat/lon to H3 hexagon cell ID
    2. Determine current time bucket (5-minute window)
    3. Increment counter in Redis for this cell + bucket
    4. Set TTL to auto-expire old data

    Args:
        ping: Ping object containing device_id, lat, lon, and optional timestamp

    Returns:
        dict: Confirmation with cell_id, bucket, and current count
    """
    start_time = time.time()
    r = get_redis_client()

    # Use provided timestamp or current time
    ts = ping.timestamp or datetime.now(timezone.utc)

    # Calculate which 5-minute bucket this ping belongs to
    bucket = current_bucket(ts)

    # Convert GPS coordinates to H3 hexagon ID (resolution 8 = ~460m)
    cell_id = latlon_to_cell(ping.lat, ping.lon)

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


@app.get("/v1/pings/count")
def ping_count():
    """
    Get total ping count (legacy endpoint).
    
    Returns:
        dict: Total number of pings received
    """
    r = get_redis_client()
    val = r.get("pings:total")
    return {"total_pings": int(val or 0)}


@app.get("/v1/congestion")
def congestion(lat: float, lon: float):
    """
    Get congestion level for a single hexagon cell.

    Process:
    1. Convert lat/lon to H3 cell ID
    2. Look up vehicle count in current time bucket
    3. Classify congestion level based on thresholds

    Args:
        lat: Latitude
        lon: Longitude

    Returns:
        dict: Cell ID, vehicle count, and congestion level (LOW/MODERATE/HIGH)
    """
    start_time = time.time()
    r = get_redis_client()

    # Convert coordinates to H3 hexagon
    cell_id = latlon_to_cell(lat, lon)

    # Get current time bucket
    now = datetime.now(timezone.utc)
    current = int(now.timestamp()) // WINDOW_SECONDS

    # Query Redis for unique device count in this cell during current bucket
    key = f"cell:{cell_id}:bucket:{current}"
    count = int(r.scard(key) or 0)
    metrics.redis_operations_total.labels(operation="scard", status="success").inc()

    # Classify congestion based on vehicle count thresholds
    if count >= 30:
        level = "HIGH"
    elif count >= 10:
        level = "MODERATE"
    else:
        level = "LOW"

    # Record metrics
    metrics.congestion_requests_total.labels(endpoint="congestion", status="success").inc()
    metrics.congestion_level_count.labels(level=level).inc()
    metrics.request_duration_seconds.labels(endpoint="congestion").observe(time.time() - start_time)

    return {
        "cell_id": cell_id,
        "vehicle_count": count,
        "congestion_level": level,
        "window_seconds": WINDOW_SECONDS,
    }


@app.get("/v1/congestion/area")
def congestion_area(lat: float, lon: float, radius: int = 1):
    """
    Get congestion for a hexagonal area around the given location.

    Uses H3's grid_disk (k-ring) algorithm to find all hexagons within
    a specified number of hops from the center point.

    Process:
    1. Convert lat/lon to center H3 cell
    2. Get all neighboring cells within 'radius' hops
    3. Query congestion for each cell in parallel
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
    area_cells = get_neighbor_cells(center_cell_id, k=radius)

    # Get current time bucket
    now = datetime.now(timezone.utc)
    current = int(now.timestamp()) // WINDOW_SECONDS

    # Query congestion for all cells in the area
    cell_data = []
    total_count = 0

    for cell_id in area_cells:
        # Build Redis key for this cell + bucket
        key = f"cell:{cell_id}:bucket:{current}"
        count = int(r.scard(key) or 0)
        metrics.redis_operations_total.labels(operation="scard", status="success").inc()
        total_count += count

        # Classify this cell's congestion level
        if count >= 30:
            level = "HIGH"
        elif count >= 10:
            level = "MODERATE"
        else:
            level = "LOW"

        metrics.congestion_level_count.labels(level=level).inc()

        # Store cell data
        cell_data.append({
            "cell_id": cell_id,
            "count": count,
            "level": level,
            "is_center": cell_id == center_cell_id
        })

    # Sort cells by vehicle count (highest congestion first)
    cell_data.sort(key=lambda x: x["count"], reverse=True)

    # Calculate area-level metrics
    avg_count = total_count / len(area_cells) if area_cells else 0
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
        "high_congestion_cells": high_congestion_cells,
        "cells": cell_data,
        "window_seconds": WINDOW_SECONDS
    }