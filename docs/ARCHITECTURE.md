# Architecture

## System Overview

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                              CONGESTION MONITOR                                  │
└─────────────────────────────────────────────────────────────────────────────────┘

    ┌──────────┐  ┌──────────┐  ┌──────────┐
    │ Device 1 │  │ Device 2 │  │ Device N │        Mobile devices / vehicles
    └────┬─────┘  └────┬─────┘  └────┬─────┘        sending location pings
         │             │             │
         └─────────────┼─────────────┘
                       │
                       ▼ HTTP POST /v1/pings
         ┌─────────────────────────────┐
         │        API Gateway          │            (AWS API Gateway in prod)
         │    Rate limiting, Auth      │
         └─────────────┬───────────────┘
                       │
                       ▼
┌──────────────────────────────────────────────────────────────────────────────────┐
│                            FastAPI Application                                    │
│  ┌────────────────────────────────────────────────────────────────────────────┐  │
│  │                              Endpoints                                      │  │
│  │  POST /v1/pings           - Receive device location ping                   │  │
│  │  POST /v1/pings/batch     - Batch ping ingestion (up to 1000)              │  │
│  │  GET  /v1/congestion      - Query single cell congestion                   │  │
│  │  GET  /v1/congestion/area - Query area congestion (k-ring)                 │  │
│  │  GET  /v1/history         - Get historical percentiles                     │  │
│  │  POST /v1/history/save    - Manually save bucket to history                │  │
│  │  GET  /health, /metrics   - Health check, Prometheus metrics               │  │
│  └────────────────────────────────────────────────────────────────────────────┘  │
│                                                                                   │
│  ┌─────────────────────┐  ┌─────────────────────┐  ┌────────────────────────┐   │
│  │   H3 Grid Module    │  │   Time Bucketing    │  │   Congestion Module    │   │
│  │                     │  │                     │  │                        │   │
│  │ • lat/lon → cell_id │  │ • 5-min windows     │  │ • Percentile-based     │   │
│  │ • Resolution 8      │  │ • Auto-expiring     │  │ • Historical buckets   │   │
│  │ • ~460m hexagons    │  │   buckets           │  │ • Fallback thresholds  │   │
│  │ • k-ring neighbors  │  │                     │  │                        │   │
│  └─────────────────────┘  └─────────────────────┘  └────────────────────────┘   │
└──────────────────────────────────────────────────────────────────────────────────┘
                       │
          ┌───────────┴───────────┐
          │                       │
          ▼                       ▼
┌─────────────────────────┐  ┌──────────────────────────┐
│         Redis           │  │   Supabase PostgreSQL    │
│    (Real-time data)     │  │    (Historical data)     │
│                         │  │                          │
│ • Device counts (SET)   │  │ • bucket_history table   │
│ • Speed readings (LIST) │  │ • Raw bucket data        │
│ • TTL: 300 seconds      │  │ • Percentile queries     │
│ • ElastiCache in prod   │  │ • Time-of-day filtering  │
└─────────────────────────┘  └──────────────────────────┘


## Data Flow

### 1. Ping Ingestion Flow

┌────────┐    POST /v1/pings     ┌─────────┐    SADD      ┌───────┐
│ Device │ ──────────────────────▶│ FastAPI │─────────────▶│ Redis │
│        │ {device_id,lat,lon,   │         │   + RPUSH    │       │
│        │  speed_kmh}           │         │   (speeds)   │       │
└────────┘                       └─────────┘              └───────┘
                                      │
                                      ▼
                              ┌───────────────┐
                              │ H3 Conversion │
                              │ lat,lon → cell│
                              └───────────────┘
                                      │
                                      ▼
                              ┌───────────────┐
                              │ Time Bucket   │
                              │ ts → bucket_n │
                              └───────────────┘
                                      │
                                      ▼
                              ┌───────────────────────────────┐
                              │ Redis Key:                    │
                              │ cell:882a100d63fffff:bucket:N │
                              │ Redis Value: SET{device_ids}  │
                              └───────────────────────────────┘


### 2. Congestion Query Flow (Percentile-Based)

┌────────┐   GET /v1/congestion   ┌─────────┐
│ Client │ ──────────────────────▶│ FastAPI │
│        │    ?lat=X&lon=Y        │         │
└────────┘                        └────┬────┘
                                       │
                        ┌──────────────┴──────────────┐
                        ▼                             ▼
                 ┌─────────────┐              ┌──────────────┐
                 │    Redis    │              │   Supabase   │
                 │ count+speed │              │  percentiles │
                 └──────┬──────┘              └──────┬───────┘
                        │                            │
                        └──────────────┬─────────────┘
                                       ▼
                               ┌────────────────┐
                               │ Percentile:    │
                               │ < p25   → HIGH │
                               │ < p50   → MOD  │
                               │ >= p50  → LOW  │
                               └────────────────┘


### 3. Area Query Flow (k-ring)

┌────────┐  GET /v1/congestion/area  ┌─────────┐
│ Client │ ─────────────────────────▶│ FastAPI │
│        │   ?lat=X&lon=Y&radius=2   │         │
└────────┘                           └─────────┘
                                          │
                          ┌───────────────┼───────────────┐
                          ▼               ▼               ▼
                    ┌──────────┐   ┌──────────┐   ┌──────────┐
                    │ Cell 1   │   │ Cell 2   │   │ Cell N   │
                    │ SCARD    │   │ SCARD    │   │ SCARD    │
                    └──────────┘   └──────────┘   └──────────┘
                          │               │               │
                          └───────────────┼───────────────┘
                                          ▼
                                  ┌───────────────┐
                                  │ Aggregate:    │
                                  │ • total count │
                                  │ • avg/cell    │
                                  │ • area level  │
                                  └───────────────┘


### 4. History Save Flow

Bucket data is saved to the history table for percentile calculations.

```
┌─────────┐  POST /v1/history/save  ┌─────────┐
│ Client  │ ───────────────────────▶│ FastAPI │
└─────────┘                         └────┬────┘
                                         │
                         ┌───────────────┴───────────────┐
                         ▼                               ▼
                  ┌─────────────┐               ┌───────────────┐
                  │    Redis    │               │   Supabase    │
                  │ get bucket  │               │ INSERT INTO   │
                  │ count+speed │               │bucket_history │
                  └─────────────┘               └───────────────┘
```

### 5. Automatic History Updates (Update-on-Write)

History is saved automatically when new pings arrive, without needing a
separate background job. This is implemented as "update-on-write":

```
┌─────────┐   POST /v1/pings   ┌─────────┐
│ Device  │ ─────────────────▶ │ FastAPI │
└─────────┘   (bucket N)       └────┬────┘
                                    │
                    ┌───────────────┴───────────────┐
                    ▼                               ▼
            ┌─────────────┐               ┌───────────────┐
            │ Save ping   │               │ Check: bucket │
            │ to bucket N │               │ N-1 saved?    │
            └─────────────┘               └───────┬───────┘
                                                  │
                                          ┌───────▼───────┐
                                          │   If unsaved: │
                                          │ INSERT bucket │
                                          │ N-1 to history│
                                          └───────────────┘
```

**How it works:**
1. When a ping arrives for bucket N, check if bucket N-1 has unsaved data
2. If bucket N-1 has data and hasn't been saved, INSERT it to bucket_history
3. Mark bucket N-1 as saved (using a Redis flag with TTL)
4. Process the new ping normally

See the **Design Tradeoffs** section at the end of this document for rationale.


### 6. Event-Driven Flow (Redis Streams)

Every ping is also published to a Redis Stream for downstream processing.
When congestion hits HIGH, an alert event is also published.

```
┌─────────┐     POST /v1/pings     ┌─────────┐
│ Device  │ ──────────────────────▶│ FastAPI │
└─────────┘                        └────┬────┘
                                        │
                        ┌───────────────┴───────────────┐
                        ▼                               ▼
                 ┌─────────────┐               ┌───────────────┐
                 │ Redis SET   │               │ Redis Stream  │
                 │ (counting)  │               │ (events)      │
                 └─────────────┘               └───────┬───────┘
                                                       │
                                                       ▼
                                         ┌─────────────────────────┐
                                         │   Event Consumer(s)     │
                                         │                         │
                                         │ • Alert notifications   │
                                         │ • Analytics pipeline    │
                                         │ • Audit logging         │
                                         └─────────────────────────┘
```

Event types published to stream `congestion:events`:
- `ping_received` - Every location ping
- `high_congestion` - When vehicle count hits 30+ in a cell


## H3 Hexagonal Grid

```
Resolution 8 hexagons (~460m edge):

        ╱╲     ╱╲     ╱╲
       ╱  ╲   ╱  ╲   ╱  ╲
      ╱    ╲ ╱    ╲ ╱    ╲
     │ Cell │ Cell │ Cell │
     │  A   │  B   │  C   │
      ╲    ╱ ╲    ╱ ╲    ╱
       ╲  ╱   ╲  ╱   ╲  ╱
        ╲╱     ╲╱     ╲╱
       ╱╲     ╱╲     ╱╲
      ╱  ╲   ╱  ╲   ╱  ╲
     │ Cell │Center│ Cell │
     │  D   │ Cell │  E   │     k=1 ring = 7 cells
      ╲    ╱ ╲    ╱ ╲    ╱      k=2 ring = 19 cells
       ╲  ╱   ╲  ╱   ╲  ╱
        ╲╱     ╲╱     ╲╱
```

## Redis Data Model (Real-time)

### Device Counts

| Property | Value |
|----------|-------|
| **Key Pattern** | `cell:{h3_cell_id}:bucket:{time_bucket}` |
| **Example** | `cell:882a100d63fffff:bucket:6043212` |
| **Value Type** | SET of device_id strings |
| **TTL** | 300 seconds |

### Speed Readings

| Property | Value |
|----------|-------|
| **Key Pattern** | `cell:{h3_cell_id}:bucket:{time_bucket}:speeds` |
| **Example** | `cell:882a100d63fffff:bucket:6043212:speeds` |
| **Value Type** | LIST of speed_kmh floats |
| **TTL** | 300 seconds |

**Benefits:**
- SET ensures unique device counting (no duplicates)
- LIST stores all speed readings for averaging
- SADD/RPUSH are O(1) - fast writes
- SCARD is O(1) - fast reads
- TTL auto-cleans old data - no manual cleanup needed


## Supabase Data Model (Historical)

### Table: `bucket_history`

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `id` | SERIAL | PRIMARY KEY | Auto-increment ID |
| `cell_id` | VARCHAR(20) | NOT NULL | H3 cell identifier |
| `bucket_time` | TIMESTAMPTZ | NOT NULL | When bucket started |
| `vehicle_count` | INTEGER | NOT NULL | Devices in bucket |
| `avg_speed` | FLOAT | NULLABLE | Avg speed km/h |
| `hour_of_day` | INTEGER | NOT NULL | 0-23 for filtering |
| `day_of_week` | INTEGER | NOT NULL | 0=Mon, 6=Sun |
| `created_at` | TIMESTAMPTZ | DEFAULT NOW() | Record created |

**Constraint:** `UNIQUE(cell_id, bucket_time)`

**Indexes:**
- `idx_bucket_history_cell_time`: (cell_id, bucket_time DESC)
- `idx_bucket_history_cell_hour`: (cell_id, hour_of_day)

**Benefits:**
- Durable storage survives restarts (unlike Redis)
- Raw data enables flexible percentile queries
- Time-of-day filtering: "is this slow for 8 AM?"
- Easy to debug and explain (no complex math)
- Cell is "calibrated" after 20+ samples

See `docs/schema.sql` for full DDL and example queries.


## Production Architecture (AWS)

Terraform modules in `terraform/` define a serverless AWS deployment:

```
Internet → API Gateway → Lambda (FastAPI+Mangum) → ElastiCache Redis
                                                 → Supabase PostgreSQL
```

**Key components:**
- **API Gateway**: Rate limiting, authentication
- **Lambda**: Auto-scales 0→1000 instances, pay-per-use
- **ElastiCache Redis**: Managed Redis with Multi-AZ failover
- **Supabase**: External managed PostgreSQL (not in Terraform)

See `terraform/README.md` for deployment instructions and cost estimates.


## Redis Streams (Event-Driven)

**Stream:** `congestion:events` (max ~10,000 events, auto-trimmed)

### Event: `ping_received`

| Field | Example Value |
|-------|---------------|
| `event_type` | `"ping_received"` |
| `device_id` | `"car_001"` |
| `cell_id` | `"882a100d63fffff"` |
| `lat`, `lon` | coordinates |
| `bucket` | time bucket number |
| `vehicle_count` | current count in cell |
| `timestamp` | ISO 8601 |

### Event: `high_congestion`

| Field | Example Value |
|-------|---------------|
| `event_type` | `"high_congestion"` |
| `cell_id` | `"882a100d63fffff"` |
| `vehicle_count` | 30+ |
| `lat`, `lon` | cell center |
| `timestamp` | ISO 8601 |

**Benefits of Redis Streams:**
- Decouples API from downstream processing
- Consumers can read at their own pace
- Supports multiple consumers (fan-out)
- Built-in message persistence
- MAXLEN prevents unbounded growth


## Component Responsibilities

| Component | Responsibility | Technology |
|-----------|---------------|------------|
| API Layer | Request handling, validation, routing | FastAPI |
| Grid Module | Spatial indexing, coordinate conversion | H3 library |
| Time Module | Temporal bucketing, window management | Python datetime |
| Real-time Data | Device counting, TTL-based expiration | Redis Sets |
| Historical Data | Bucket storage for percentile calculations | Supabase PostgreSQL |
| Congestion | Percentile-based level detection | Python (congestion.py) |
| Events | Event publishing for downstream consumers | Redis Streams |
| Metrics | Observability, performance monitoring | Prometheus |
| Infrastructure | Deployment, scaling, networking | Terraform + AWS |


---

## Summary

This architecture prioritizes:

1. **Simplicity** - Minimal components, clear data flow
2. **Correctness** - Unique device counting via Redis Sets, proper spatial indexing via H3
3. **Scalability** - Serverless-ready, horizontally scalable
4. **Operability** - Prometheus metrics, health checks, infrastructure as code

For detailed rationale on technology choices, trade-offs, scaling strategy, and future improvements, see [DESIGN.md](./DESIGN.md).
