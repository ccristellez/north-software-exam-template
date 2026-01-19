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
│  │  POST /v1/pings          - Receive device location pings                   │  │
│  │  GET  /v1/congestion     - Query single cell congestion                    │  │
│  │  GET  /v1/congestion/area - Query area congestion (k-ring)                 │  │
│  │  GET  /health            - Health check                                    │  │
│  │  GET  /metrics           - Prometheus metrics                              │  │
│  └────────────────────────────────────────────────────────────────────────────┘  │
│                                                                                   │
│  ┌─────────────────────┐  ┌─────────────────────┐  ┌────────────────────────┐   │
│  │   H3 Grid Module    │  │   Time Bucketing    │  │   Metrics Module       │   │
│  │                     │  │                     │  │                        │   │
│  │ • lat/lon → cell_id │  │ • 5-min windows     │  │ • Request counters     │   │
│  │ • Resolution 8      │  │ • Auto-expiring     │  │ • Latency histograms   │   │
│  │ • ~460m hexagons    │  │   buckets           │  │ • Business metrics     │   │
│  │ • k-ring neighbors  │  │                     │  │                        │   │
│  └─────────────────────┘  └─────────────────────┘  └────────────────────────┘   │
└──────────────────────────────────────────────────────────────────────────────────┘
                       │
                       │ Redis Protocol
                       ▼
         ┌─────────────────────────────┐
         │          Redis              │            (ElastiCache in prod)
         │                             │
         │  Key: cell:{h3_id}:bucket:{t}│
         │  Value: SET of device_ids   │
         │  TTL: 300 seconds           │
         │                             │
         └─────────────────────────────┘


## Data Flow

### 1. Ping Ingestion Flow

┌────────┐    POST /v1/pings     ┌─────────┐    SADD      ┌───────┐
│ Device │ ──────────────────────▶│ FastAPI │─────────────▶│ Redis │
│        │  {device_id,lat,lon}  │         │              │       │
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


### 2. Congestion Query Flow

┌────────┐   GET /v1/congestion   ┌─────────┐   SCARD     ┌───────┐
│ Client │ ──────────────────────▶│ FastAPI │────────────▶│ Redis │
│        │    ?lat=X&lon=Y        │         │◀────────────│       │
└────────┘                        └─────────┘   count     └───────┘
                                       │
                                       ▼
                               ┌───────────────┐
                               │ Classify:     │
                               │ <10  → LOW    │
                               │ <30  → MOD    │
                               │ 30+  → HIGH   │
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


### 4. Event-Driven Flow (Redis Streams)

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

## Redis Data Model

```
┌────────────────────────────────────────────────────────────────┐
│                        Redis Keys                               │
├────────────────────────────────────────────────────────────────┤
│ Key Pattern: cell:{h3_cell_id}:bucket:{time_bucket}            │
│                                                                 │
│ Example:     cell:882a100d63fffff:bucket:6043212               │
│                    └──────┬──────┘       └───┬───┘             │
│                      H3 cell ID        Unix ts / 300           │
│                                                                 │
│ Value Type:  SET                                                │
│ Members:     device_id strings                                  │
│                                                                 │
│ TTL:         300 seconds (auto-expire after window closes)      │
└────────────────────────────────────────────────────────────────┘

Benefits:
• SET ensures unique device counting (no duplicates)
• SADD is O(1) - fast writes
• SCARD is O(1) - fast reads
• TTL auto-cleans old data - no manual cleanup needed
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
| Data Store | Device counting, TTL-based expiration | Redis Sets |
| Events | Event publishing for downstream consumers | Redis Streams |
| Metrics | Observability, performance monitoring | Prometheus |
| Infrastructure | Deployment, scaling, networking | Terraform + AWS |
