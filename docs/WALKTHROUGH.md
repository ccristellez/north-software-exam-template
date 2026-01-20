# System Walkthrough: From Ping to Congestion

This document walks through exactly what happens when a vehicle sends its location to our traffic monitoring system. We'll follow a single ping through the entire pipeline, from the moment it hits our API to when it affects congestion calculations.

Think of this as a "day in the life" of a GPS ping.

---

## The Setup

Before we dive in, here's what we're working with:

- **FastAPI** handles HTTP requests (`src/api/main.py`)
- **Redis** stores real-time data with automatic expiration
- **PostgreSQL (Supabase)** stores historical bucket data for percentile calculations
- **H3** converts GPS coordinates into hexagonal grid cells

The core insight is that we bucket time into 5-minute windows and space into ~460-meter hexagons. This gives us a nice grid where we can count vehicles and detect unusual patterns.

---

## Part 1: A Ping Arrives

Let's say a delivery truck is driving through San Francisco at 8:37 AM. Its onboard GPS sends this request:

```json
POST /v1/pings
{
  "device_id": "truck-42",
  "lat": 37.7749,
  "lon": -122.4194,
  "speed_kmh": 28.5,
  "timestamp": "2024-01-15T08:37:23Z"
}
```

### Step 1: Rate Limiting

The first thing we do is check if this device is spamming us.

**File:** `src/api/main.py`, lines 26-46

```python
def check_rate_limit(r, device_id: str) -> bool:
    key = f"ratelimit:{device_id}"
    count = r.incr(key)
    if count == 1:
        r.expire(key, RATE_LIMIT_WINDOW_SECONDS)
    return count <= RATE_LIMIT_MAX_REQUESTS
```

We allow 100 pings per minute per device. Redis handles the counting with atomic `INCR` and auto-expiring keys. Simple and effective.

### Step 2: Time Bucketing

Next, we figure out which 5-minute bucket this ping belongs to.

**File:** `src/api/time_utils.py`, lines 18-31

```python
def current_bucket(ts: datetime = None) -> int:
    if ts is None:
        ts = datetime.now(timezone.utc)
    return int(ts.timestamp()) // WINDOW_SECONDS
```

For our 8:37 AM ping, the bucket number is calculated as:
- Unix timestamp: `1705311443`
- Divided by 300 seconds: `5684371`

This means our ping joins bucket `5684371`, which covers 8:35-8:40 AM.

### Step 3: Spatial Indexing with H3

Now we convert GPS coordinates to a hexagon cell.

**File:** `src/api/grid.py`, lines 26-39

```python
def latlon_to_cell(lat: float, lon: float, resolution: int = H3_RESOLUTION) -> str:
    return h3.latlng_to_cell(lat, lon, resolution)
```

At resolution 8, each hexagon is about 460 meters across. Our truck's location `(37.7749, -122.4194)` becomes cell ID `882830829bfffff`.

### Step 4: Flushing Previous Bucket to History

Here's where it gets interesting. Before we record this ping, we check if the *previous* bucket should be saved to our history table.

**File:** `src/api/main.py`, lines 49-92

```python
def flush_completed_bucket_to_history(r, cell_id: str, current_bucket: int) -> bool:
    prev_bucket = current_bucket - 1

    # Check if we already saved this bucket
    saved_flag_key = f"cell:{cell_id}:bucket:{prev_bucket}:history_saved"
    if r.exists(saved_flag_key):
        return False

    # Get previous bucket data
    prev_key = f"cell:{cell_id}:bucket:{prev_bucket}"
    prev_count = int(r.scard(prev_key) or 0)

    if prev_count == 0:
        return False

    # Get speeds and calculate average
    prev_speeds = cong.get_bucket_speeds(r, cell_id, prev_bucket)
    prev_avg_speed = sum(prev_speeds) / len(prev_speeds) if prev_speeds else None

    # Save to PostgreSQL
    cong.save_bucket_to_history(cell_id, bucket_time, prev_count, prev_avg_speed)
```

This is the **update-on-write pattern**. Instead of running a cron job, we piggyback on incoming pings to save historical data. The first ping in a new bucket triggers the save for the previous bucket. Efficient and automatic.

### Step 5: Recording the Ping

Now we actually record our truck's presence.

**File:** `src/api/main.py`, lines 178-196

```python
# Build Redis key
key = f"cell:{cell_id}:bucket:{bucket}"  # "cell:882830829bfffff:bucket:5684371"

# Add device to the set (sets ensure uniqueness)
r.sadd(key, ping.device_id)

# Get count of unique devices
count = r.scard(key)

# Auto-expire after 5 minutes
r.expire(key, 300)

# Store speed for averaging
if ping.speed_kmh is not None:
    cong.record_speed(r, cell_id, bucket, ping.speed_kmh)
```

Key insight: we use Redis **SETs** so each device only counts once per bucket. If truck-42 sends 10 pings in 5 minutes, it still only contributes 1 to the vehicle count.

Speed data goes into a separate Redis **LIST** so we can calculate averages later:

**File:** `src/api/congestion.py`, lines 159-171

```python
def record_speed(r: Redis, cell_id: str, bucket: int, speed_kmh: float) -> None:
    key = get_speed_key(cell_id, bucket)  # "cell:882830829bfffff:bucket:5684371:speeds"
    r.rpush(key, speed_kmh)
    r.expire(key, 300)
```

### Step 6: Events and Alerts

Finally, we publish events to a Redis Stream for downstream consumers.

**File:** `src/api/main.py`, lines 204-223

```python
# Publish event
events.publish_ping_event(r, device_id, cell_id, lat, lon, bucket, count)

# Alert if congestion is high
if count >= 30:
    events.publish_high_congestion_alert(r, cell_id, count, lat, lon)
```

Any service can subscribe to these streams for real-time notifications.

---

## Part 2: Querying Congestion

An hour later, someone queries for congestion at our truck's location:

```
GET /v1/congestion?lat=37.7749&lon=-122.4194
```

### Step 1: Get Current Data from Redis

**File:** `src/api/main.py`, lines 347-387

```python
cell_id = latlon_to_cell(lat, lon)  # "882830829bfffff"
bucket = int(now.timestamp()) // WINDOW_SECONDS  # current bucket

# Get vehicle count
key = f"cell:{cell_id}:bucket:{bucket}"
count = int(r.scard(key) or 0)  # e.g., 25 vehicles

# Get average speed
speeds = cong.get_bucket_speeds(r, cell_id, bucket)
avg_speed = sum(speeds) / len(speeds) if speeds else None  # e.g., 22.3 km/h
```

### Step 2: Get Historical Percentiles from PostgreSQL

This is where our history table pays off.

**File:** `src/api/congestion.py`, lines 59-108

```python
def get_cell_percentiles(cell_id: str, hours_back: int = 168) -> CellPercentiles:
    result = session.execute(text("""
        SELECT
            PERCENTILE_CONT(0.25) WITHIN GROUP (ORDER BY avg_speed) as speed_p25,
            PERCENTILE_CONT(0.50) WITHIN GROUP (ORDER BY avg_speed) as speed_p50,
            PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY vehicle_count) as count_p75,
            COUNT(*) as sample_count
        FROM bucket_history
        WHERE cell_id = :cell_id
          AND bucket_time > NOW() - INTERVAL '168 hours'
    """), {"cell_id": cell_id}).fetchone()
```

We're using PostgreSQL's `PERCENTILE_CONT` function to calculate:
- **speed_p25**: 25th percentile speed (slow day)
- **speed_p50**: Median speed (typical day)
- **count_p75**: 75th percentile vehicle count (busy day)

For a cell with 50 historical samples, we might get:
- speed_p25 = 28.0 km/h
- speed_p50 = 42.0 km/h
- count_p75 = 20 vehicles

### Step 3: Calculate Congestion Level

Now we compare current conditions to history.

**File:** `src/api/congestion.py`, lines 191-262

```python
def calculate_congestion_level(current_count, current_avg_speed, percentiles):
    # Not enough history? Use absolute thresholds
    if not percentiles.is_calibrated:
        return _calculate_congestion_fallback(current_count, current_avg_speed)

    # Speed is primary signal
    if current_avg_speed is not None and percentiles.has_speed_data:
        if current_avg_speed < percentiles.speed_p25:
            return "HIGH"    # Slower than 75% of historical data
        elif current_avg_speed < percentiles.speed_p50:
            return "MODERATE"  # Slower than typical
        else:
            # Good speed, but check if count is unusually high
            if current_count > percentiles.count_p75:
                return "MODERATE"
            return "LOW"

    # No speed data - use count only
    if current_count > percentiles.count_p75 * 1.5:
        return "HIGH"
    elif current_count > percentiles.count_p75:
        return "MODERATE"
    return "LOW"
```

With our example data:
- Current speed: 22.3 km/h
- Historical p25: 28.0 km/h
- 22.3 < 28.0, so **congestion = HIGH**

The response:

```json
{
  "cell_id": "882830829bfffff",
  "vehicle_count": 25,
  "avg_speed_kmh": 22.3,
  "congestion_level": "HIGH",
  "calibrated": true,
  "window_seconds": 300
}
```

---

## Part 3: The Fallback System

What if a cell has no history? Maybe it's a new area or rural location.

**File:** `src/api/congestion.py`, lines 265-294

```python
def _calculate_congestion_fallback(count: int, avg_speed: Optional[float]) -> str:
    # Speed thresholds (from urban traffic studies)
    FALLBACK_SPEED_HIGH = 15      # Below 15 km/h = crawling
    FALLBACK_SPEED_MODERATE = 40  # Below 40 km/h = slow

    # Count thresholds
    FALLBACK_COUNT_HIGH = 30      # 30+ vehicles = busy
    FALLBACK_COUNT_MODERATE = 10  # 10+ vehicles = moderate

    if avg_speed is not None:
        if avg_speed < FALLBACK_SPEED_HIGH:
            return "HIGH"
        elif avg_speed < FALLBACK_SPEED_MODERATE:
            return "MODERATE"
        elif count >= FALLBACK_COUNT_HIGH:
            return "MODERATE"  # Good speed but lots of cars
        return "LOW"

    # No speed data at all - just use counts
    if count >= FALLBACK_COUNT_HIGH:
        return "HIGH"
    elif count >= FALLBACK_COUNT_MODERATE:
        return "MODERATE"
    return "LOW"
```

The system works from day one with sensible defaults, then gets smarter as it collects data.

---

## Part 4: History Accumulation

Every 5 minutes, each active cell saves its bucket data to PostgreSQL.

**File:** `src/api/congestion.py`, lines 111-156

```python
def save_bucket_to_history(cell_id, bucket_time, vehicle_count, avg_speed):
    hour_of_day = bucket_time.hour      # 0-23
    day_of_week = bucket_time.weekday() # 0=Monday, 6=Sunday

    record = BucketHistory(
        cell_id=cell_id,
        bucket_time=bucket_time,
        vehicle_count=vehicle_count,
        avg_speed=avg_speed,
        hour_of_day=hour_of_day,
        day_of_week=day_of_week
    )
    session.add(record)
    session.commit()
```

We store `hour_of_day` and `day_of_week` for future time-aware queries. Want to compare current Tuesday 8 AM traffic to historical Tuesday 8 AM traffic? That's possible.

After 20 samples (defined by `MIN_SAMPLES_FOR_PERCENTILES` in `congestion.py:26`), a cell becomes "calibrated" and starts using percentile-based detection instead of fallback thresholds.

---

## Data Flow Summary

```
                                   ┌─────────────────────┐
     GPS Ping                      │                     │
        │                          │    PostgreSQL       │
        ▼                          │    (Supabase)       │
  ┌──────────┐                     │                     │
  │  Rate    │                     │  bucket_history     │
  │  Limit   │                     │  ┌───────────────┐  │
  └────┬─────┘                     │  │ cell_id       │  │
       │                           │  │ bucket_time   │  │
       ▼                           │  │ vehicle_count │  │
  ┌──────────┐      Save when      │  │ avg_speed     │  │
  │  H3      │      bucket ends    │  │ hour_of_day   │  │
  │  Cell ID │─────────────────────►  │ day_of_week   │  │
  └────┬─────┘                     │  └───────────────┘  │
       │                           │                     │
       ▼                           │  PERCENTILE_CONT()  │
  ┌──────────────┐                 │         │          │
  │    Redis     │                 └─────────┼──────────┘
  │              │                           │
  │  cell:X:     │                           ▼
  │  bucket:Y    │◄─────────────────  Query percentiles
  │  (SET)       │                    for congestion
  │              │                    calculation
  │  cell:X:     │
  │  bucket:Y:   │
  │  speeds      │
  │  (LIST)      │
  └──────────────┘
```

---

## Key Files Reference

| File | Purpose | Key Functions |
|------|---------|---------------|
| `src/api/main.py` | FastAPI endpoints | `create_ping()`, `congestion()`, `flush_completed_bucket_to_history()` |
| `src/api/congestion.py` | Congestion logic | `calculate_congestion_level()`, `get_cell_percentiles()`, `save_bucket_to_history()` |
| `src/api/grid.py` | H3 spatial indexing | `latlon_to_cell()`, `get_neighbor_cells()` |
| `src/api/time_utils.py` | Time bucketing | `current_bucket()` |
| `src/api/database.py` | SQLAlchemy models | `BucketHistory` model |
| `src/api/redis_client.py` | Redis connection | `get_redis_client()` |
| `src/api/events.py` | Event streaming | `publish_ping_event()`, `publish_high_congestion_alert()` |

---

## Why This Design?

**Percentiles over Z-scores:** Easier to explain. "Traffic is slower than 75% of historical data" beats "traffic is 1.5 standard deviations below the mean."

**SQL PERCENTILE_CONT:** Let PostgreSQL do the heavy math. No need for Welford's algorithm or incremental statistics.

**Update-on-write:** No cron jobs. History saves automatically when pings arrive.

**Fallback thresholds:** Works immediately. No cold-start problem.

**Redis TTL:** Old data disappears automatically. No cleanup scripts needed.

The system is simple enough to explain in an interview, but robust enough for production traffic.
