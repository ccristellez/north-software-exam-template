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
│  │  POST /v1/pings          - Receive device location pings (with speed)      │  │
│  │  GET  /v1/congestion     - Query single cell congestion (Z-score based)    │  │
│  │  GET  /v1/congestion/area - Query area congestion (k-ring)                 │  │
│  │  GET  /v1/baseline       - Get historical baseline for a cell              │  │
│  │  POST /v1/baseline/update - Update baseline from current bucket            │  │
│  │  GET  /health            - Health check                                    │  │
│  │  GET  /metrics           - Prometheus metrics                              │  │
│  └────────────────────────────────────────────────────────────────────────────┘  │
│                                                                                   │
│  ┌─────────────────────┐  ┌─────────────────────┐  ┌────────────────────────┐   │
│  │   H3 Grid Module    │  │   Time Bucketing    │  │   Congestion Module    │   │
│  │                     │  │                     │  │                        │   │
│  │ • lat/lon → cell_id │  │ • 5-min windows     │  │ • Z-score calculation  │   │
│  │ • Resolution 8      │  │ • Auto-expiring     │  │ • Historical baselines │   │
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
│ • Device counts (SET)   │  │ • Baseline avg_speed     │
│ • Speed readings (LIST) │  │ • Baseline avg_count     │
│ • TTL: 300 seconds      │  │ • Variance values        │
│ • ElastiCache in prod   │  │ • Sample counts          │
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


### 2. Congestion Query Flow (Z-Score Based)

┌────────┐   GET /v1/congestion   ┌─────────┐
│ Client │ ──────────────────────▶│ FastAPI │
│        │    ?lat=X&lon=Y        │         │
└────────┘                        └────┬────┘
                                       │
                        ┌──────────────┴──────────────┐
                        ▼                             ▼
                 ┌─────────────┐              ┌──────────────┐
                 │    Redis    │              │   Supabase   │
                 │ count+speed │              │   baseline   │
                 └──────┬──────┘              └──────┬───────┘
                        │                            │
                        └──────────────┬─────────────┘
                                       ▼
                               ┌───────────────┐
                               │ Z-Score Calc: │
                               │ Z > 1.5 →HIGH │
                               │ Z > 0.5 →MOD  │
                               │ else   →LOW   │
                               └───────────────┘


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


### 4. Baseline Update Flow

Baselines are updated from bucket data and stored in Supabase for persistence.

```
┌─────────┐  POST /v1/baseline/update  ┌─────────┐
│ Client  │ ──────────────────────────▶│ FastAPI │
└─────────┘                            └────┬────┘
                                            │
                            ┌───────────────┴───────────────┐
                            ▼                               ▼
                     ┌─────────────┐               ┌───────────────┐
                     │    Redis    │               │   Supabase    │
                     │ get bucket  │               │ get baseline  │
                     │ count+speed │               │               │
                     └──────┬──────┘               └───────┬───────┘
                            │                              │
                            └──────────────┬───────────────┘
                                           ▼
                                   ┌───────────────┐
                                   │   EMA Update  │
                                   │ α=0.1 weight  │
                                   │ to new data   │
                                   └───────┬───────┘
                                           │
                                           ▼
                                   ┌───────────────┐
                                   │   Supabase    │
                                   │ save baseline │
                                   └───────────────┘
```

### 5. Event-Driven Flow (Redis Streams)

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

```
┌────────────────────────────────────────────────────────────────┐
│                    Redis Keys - Device Counts                   │
├────────────────────────────────────────────────────────────────┤
│ Key Pattern: cell:{h3_cell_id}:bucket:{time_bucket}            │
│ Example:     cell:882a100d63fffff:bucket:6043212               │
│ Value Type:  SET of device_id strings                          │
│ TTL:         300 seconds                                        │
├────────────────────────────────────────────────────────────────┤
│                    Redis Keys - Speed Readings                  │
├────────────────────────────────────────────────────────────────┤
│ Key Pattern: cell:{h3_cell_id}:bucket:{time_bucket}:speeds     │
│ Example:     cell:882a100d63fffff:bucket:6043212:speeds        │
│ Value Type:  LIST of speed_kmh floats                          │
│ TTL:         300 seconds                                        │
└────────────────────────────────────────────────────────────────┘

Benefits:
• SET ensures unique device counting (no duplicates)
• LIST stores all speed readings for averaging
• SADD/RPUSH are O(1) - fast writes
• SCARD is O(1) - fast reads
• TTL auto-cleans old data - no manual cleanup needed
```


## Supabase Data Model (Historical)

```
┌────────────────────────────────────────────────────────────────┐
│                   Table: hex_baselines                          │
├────────────────────────────────────────────────────────────────┤
│ cell_id         VARCHAR(20)  PRIMARY KEY   H3 cell identifier  │
│ avg_speed       FLOAT        DEFAULT 0     Historical avg km/h │
│ avg_count       FLOAT        DEFAULT 0     Historical avg count│
│ speed_variance  FLOAT        DEFAULT 0     For std deviation   │
│ count_variance  FLOAT        DEFAULT 0     For std deviation   │
│ sample_count    INT          DEFAULT 0     Calibration progress│
│ updated_at      TIMESTAMPTZ  DEFAULT NOW() Last update time    │
└────────────────────────────────────────────────────────────────┘

Benefits:
• Durable storage survives restarts (unlike Redis)
• Easy to query and analyze baseline data
• Exponential moving average (EMA) weights recent data
• Cell is "calibrated" after 50+ samples
```


## Production Architecture (AWS)

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                                    AWS                                           │
│                                                                                  │
│  ┌─────────────┐     ┌─────────────────────────────────────────────────────┐   │
│  │ CloudWatch  │     │                      VPC                             │   │
│  │  Alarms     │     │  ┌────────────────────────────────────────────────┐  │   │
│  │  Dashboards │     │  │              Public Subnets                    │  │   │
│  └─────────────┘     │  │  ┌─────────────────────────────────────────┐   │  │   │
│         │            │  │  │           API Gateway                    │   │  │   │
│         ▼            │  │  │     (Rate Limit, WAF, Auth)              │   │  │   │
│  ┌─────────────┐     │  │  └──────────────────┬──────────────────────┘   │  │   │
│  │ Prometheus  │     │  └────────────────────┼───────────────────────────┘  │   │
│  │  /metrics   │     │                       │                               │   │
│  └─────────────┘     │  ┌────────────────────┼───────────────────────────┐  │   │
│                      │  │              Private Subnets                    │  │   │
│                      │  │                    │                            │  │   │
│                      │  │         ┌──────────▼──────────┐                 │  │   │
│                      │  │         │   Lambda Function   │                 │  │   │
│                      │  │         │   (FastAPI+Mangum)  │                 │  │   │
│                      │  │         │                     │                 │  │   │
│                      │  │         │ Auto-scaling:       │                 │  │   │
│                      │  │         │ 0 → 1000 instances  │                 │  │   │
│                      │  │         └──────────┬──────────┘                 │  │   │
│                      │  │                    │                            │  │   │
│                      │  │         ┌──────────▼──────────┐                 │  │   │
│                      │  │         │   ElastiCache       │                 │  │   │
│                      │  │         │   Redis Cluster     │                 │  │   │
│                      │  │         │                     │                 │  │   │
│                      │  │         │ • Multi-AZ          │                 │  │   │
│                      │  │         │ • Auto-failover     │                 │  │   │
│                      │  │         │ • 2 read replicas   │                 │  │   │
│                      │  │         └─────────────────────┘                 │  │   │
│                      │  └────────────────────────────────────────────────┘  │   │
│                      └──────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────────────┘
```


## Redis Streams (Event-Driven)

```
┌────────────────────────────────────────────────────────────────┐
│                      Redis Stream                               │
├────────────────────────────────────────────────────────────────┤
│ Stream Name: congestion:events                                  │
│ Max Length:  ~10,000 events (auto-trimmed)                      │
│                                                                 │
│ Event: ping_received                                            │
│ ├── event_type: "ping_received"                                 │
│ ├── device_id: "car_001"                                        │
│ ├── cell_id: "882a100d63fffff"                                  │
│ ├── lat, lon: coordinates                                       │
│ ├── bucket: time bucket number                                  │
│ ├── vehicle_count: current count in cell                        │
│ └── timestamp: ISO 8601                                         │
│                                                                 │
│ Event: high_congestion                                          │
│ ├── event_type: "high_congestion"                               │
│ ├── cell_id: "882a100d63fffff"                                  │
│ ├── vehicle_count: 30+                                          │
│ ├── lat, lon: cell center                                       │
│ └── timestamp: ISO 8601                                         │
└────────────────────────────────────────────────────────────────┘

Benefits of Redis Streams:
• Decouples API from downstream processing
• Consumers can read at their own pace
• Supports multiple consumers (fan-out)
• Built-in message persistence
• MAXLEN prevents unbounded growth
```


## Component Responsibilities

| Component | Responsibility | Technology |
|-----------|---------------|------------|
| API Layer | Request handling, validation, routing | FastAPI |
| Grid Module | Spatial indexing, coordinate conversion | H3 library |
| Time Module | Temporal bucketing, window management | Python datetime |
| Real-time Data | Device counting, TTL-based expiration | Redis Sets |
| Historical Data | Baseline storage for Z-score calibration | Supabase PostgreSQL |
| Congestion | Z-score based level detection | Python (congestion.py) |
| Events | Event publishing for downstream consumers | Redis Streams |
| Metrics | Observability, performance monitoring | Prometheus |
| Infrastructure | Deployment, scaling, networking | Terraform + AWS |
