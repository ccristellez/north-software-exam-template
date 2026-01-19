# Design Decisions & Trade-offs

## Overview

This document explains the key architectural decisions, their trade-offs, and how the system would scale in production.

---

## 1. Spatial Indexing: H3 Hexagonal Grid

### Decision
Use Uber's H3 hexagonal grid system at resolution 8 (~460m hexagon edge) instead of alternatives like geohash, S2, or simple lat/lon rounding.

### Why H3?

| Approach | Pros | Cons |
|----------|------|------|
| **H3 (chosen)** | Uniform cell sizes, consistent neighbors, no edge distortion | Library dependency, learning curve |
| Geohash | Simple string-based, widely understood | Rectangular cells, edge effects, inconsistent neighbor distances |
| S2 | Google-backed, good for global scale | More complex API, square cells |
| Lat/lon rounding | Zero dependencies | Cells vary in size by latitude, poor neighbor queries |

### Trade-offs Accepted

**Chose H3 because:**
- Hexagons have equidistant neighbors (6 neighbors all same distance from center)
- No "corner neighbor" problem that rectangles have
- `grid_disk` provides efficient k-ring queries for area congestion
- Resolution 8 (~460m) matches typical city block granularity

**Accepted downsides:**
- Added `h3` library dependency
- Slightly more complex than simple rounding
- H3 cell IDs are longer strings (more Redis memory)

### Resolution Choice

```
Resolution 7:  ~1.2km edge  → Too coarse, loses local detail
Resolution 8:  ~460m edge   → Good balance for city traffic ✓
Resolution 9:  ~174m edge   → Too fine, too many cells to query
```

Resolution 8 chosen because:
- Covers roughly one city block
- Area queries with k=2 (~19 cells) cover ~1km radius
- Balances granularity vs. query cost

---

## 2. Temporal Bucketing: 5-Minute Windows

### Decision
Aggregate pings into 5-minute time buckets with automatic expiration.

### Why 5 Minutes?

| Window Size | Pros | Cons |
|-------------|------|------|
| 1 minute | More real-time | Noisy data, higher write frequency |
| **5 minutes** | Smooth data, reasonable freshness | Slight lag |
| 15 minutes | Very stable | Too stale for "real-time" |

### Trade-offs Accepted

**Chose 5-minute windows because:**
- Balances freshness vs. stability
- Matches typical traffic signal cycle times
- Provides enough data points for meaningful congestion
- Redis TTL of 300s auto-cleans data

**Accepted downsides:**
- Congestion data can be up to 5 minutes stale
- Sudden congestion spikes take time to register
- Clearing congestion also has lag

### Alternative Considered: Sliding Windows

Could implement true sliding windows with Redis sorted sets (ZADD with timestamp scores), but:
- More complex queries (ZRANGEBYSCORE)
- Higher memory usage
- Marginal benefit for this use case

Fixed buckets are simpler and sufficient for traffic patterns.

---

## 3. Data Store: Redis with Sets

### Decision
Use Redis Sets to track unique devices per cell+bucket.

### Why Redis Sets?

| Approach | Pros | Cons |
|----------|------|------|
| **Redis SET** | O(1) add/count, auto-dedup, TTL support | Single point of failure, memory-bound |
| Redis HyperLogLog | Lower memory for huge cardinalities | ~0.81% error, can't list members |
| PostgreSQL | ACID, familiar | Slower for this access pattern |
| DynamoDB | Serverless, scalable | More complex for set operations |

### Key Design

```
Key:   cell:{h3_cell_id}:bucket:{unix_ts // 300}
Value: SET of device_id strings
TTL:   300 seconds
```

### Trade-offs Accepted

**Chose Redis Sets because:**
- `SADD` is O(1) - handles high write throughput
- `SCARD` is O(1) - instant count without scanning
- Sets auto-deduplicate (same device pinging twice = 1 count)
- TTL auto-expires old buckets (no cleanup jobs needed)

**Accepted downsides:**
- Memory grows with unique devices (not an issue at expected scale)
- Single Redis instance is a bottleneck (solved with ElastiCache cluster in prod)
- No persistence by default (acceptable - data is ephemeral anyway)

### Why Not HyperLogLog?

HyperLogLog would use less memory but:
- We expect <1000 devices per cell per bucket
- We might want to list devices later (Sets allow this)
- Exact counts are preferred for congestion thresholds

---

## 4. Congestion Thresholds

### Decision
Simple threshold-based classification:

```
LOW:      0-9 vehicles
MODERATE: 10-29 vehicles
HIGH:     30+ vehicles
```

### Why These Numbers?

Based on typical urban traffic density:
- H3 resolution 8 cell ≈ 0.74 km²
- 30 vehicles in this area = significant congestion
- 10 vehicles = notable but flowing traffic

### Trade-offs Accepted

**Chose simple thresholds because:**
- Easy to understand and explain
- Fast to compute (no ML, no historical comparison)
- Can be tuned via configuration

**Accepted downsides:**
- Doesn't account for road capacity (highway vs. residential)
- Doesn't consider time-of-day patterns
- Same thresholds everywhere (NYC vs. suburbs)

### Future Improvements

If given more time:
- Per-cell threshold configuration
- ML-based anomaly detection
- Historical baseline comparison
- Road network awareness

---

## 5. API Design

### Decision
RESTful JSON API with three main endpoints.

### Endpoints

| Endpoint | Purpose | Complexity |
|----------|---------|------------|
| `POST /v1/pings` | Ingest device locations | Simple write |
| `GET /v1/congestion` | Query single cell | Single Redis read |
| `GET /v1/congestion/area` | Query area (k-ring) | Multiple Redis reads |

### Trade-offs Accepted

**Chose REST over alternatives:**

| Protocol | Pros | Cons |
|----------|------|------|
| **REST** | Simple, cacheable, familiar | Not real-time |
| WebSocket | Real-time push | More complex, stateful |
| gRPC | Efficient, typed | Requires protobuf, less portable |

**Why REST:**
- Simpler for MVP
- Works with any HTTP client
- Easy to test with curl
- Can add WebSocket/SSE later for push notifications

---

## 6. Event-Driven Architecture: Redis Streams

### Decision
Publish events to a Redis Stream when pings are received, enabling downstream consumers without coupling them to the API.

### Why Redis Streams?

| Approach | Pros | Cons |
|----------|------|------|
| **Redis Streams** | Already have Redis, simple, persistent | Single Redis dependency |
| Kafka | High throughput, mature ecosystem | Separate infrastructure, complexity |
| RabbitMQ | Flexible routing, mature | Another service to manage |
| AWS SQS/SNS | Managed, scalable | Cloud-only, latency |

### How It Works

```
API receives ping
    │
    ├──▶ Write to Redis SET (counting)
    │
    └──▶ XADD to Redis Stream (event)
              │
              ▼
         Consumer reads with XREAD
         (blocking or polling)
```

### Events Published

| Event Type | When | Use Case |
|------------|------|----------|
| `ping_received` | Every ping | Analytics, audit logging |
| `high_congestion` | Count reaches 30+ | Alerts, notifications |

### Trade-offs Accepted

**Chose Redis Streams because:**
- Already running Redis - no new infrastructure
- Built-in persistence (events survive restarts)
- Simple consumer model with XREAD
- MAXLEN prevents unbounded growth
- Can add consumer groups later for scaling

**Accepted downsides:**
- Tied to Redis (not portable to other message brokers)
- No built-in dead-letter queue (would need to implement)
- Single consumer per stream (can add consumer groups if needed)

### Why Not Just Pub/Sub?

Redis Pub/Sub is simpler but:
- Messages are lost if no subscriber is listening
- No persistence
- No replay capability

Streams provide durability - consumers can catch up after downtime.

---

## 7. Observability: Prometheus Metrics

### Decision
Built-in Prometheus metrics endpoint at `/metrics`.

### Metrics Exposed

| Metric | Type | Purpose |
|--------|------|---------|
| `ping_requests_total` | Counter | Track ingestion rate |
| `congestion_requests_total` | Counter | Track query load |
| `request_duration_seconds` | Histogram | Latency monitoring |
| `unique_devices_per_bucket` | Gauge | Business metric |
| `congestion_level_count` | Counter | Distribution of levels |
| `redis_operations_total` | Counter | Backend health |

### Trade-offs Accepted

**Chose Prometheus because:**
- Industry standard, widely supported
- Pull-based (simpler than push)
- Grafana integration
- Low overhead

**Accepted downsides:**
- Cardinality explosion risk with `cell_id` labels
- No distributed tracing (would add OpenTelemetry for prod)

---

## 8. Infrastructure: Serverless on AWS

### Decision
Deploy as AWS Lambda behind API Gateway, with ElastiCache Redis.

### Why Serverless?

| Approach | Pros | Cons |
|----------|------|------|
| **Lambda** | Auto-scaling, pay-per-use, no servers | Cold starts, 15min limit |
| ECS/Fargate | More control, no cold starts | More ops overhead |
| EC2 | Full control | Manual scaling, more cost |
| Kubernetes | Portable, powerful | Overkill for this scale |

### Trade-offs Accepted

**Chose Lambda because:**
- Scales from 0 to thousands automatically
- Pay nothing when idle
- No server management
- Matches North's stated stack

**Accepted downsides:**
- Cold start latency (~100-500ms first request)
- Need Mangum adapter for FastAPI
- VPC Lambda needs NAT Gateway for internet (cost)

### Mitigations

- Provisioned concurrency for consistent latency (if needed)
- Keep Lambda warm with scheduled pings
- Use VPC endpoints to avoid NAT costs

---

## 9. Scalability Analysis

### Current Limits

| Component | Limit | Mitigation |
|-----------|-------|------------|
| Single Redis | ~100k ops/sec | ElastiCache cluster |
| Lambda concurrency | 1000 default | Request limit increase |
| API Gateway | 10k req/sec default | Can increase |

### Scaling Strategy

**Horizontal scaling:**
- Lambda auto-scales (no action needed)
- ElastiCache supports read replicas
- API Gateway handles distribution

**Vertical scaling:**
- Larger ElastiCache node types
- More Lambda memory = faster execution

### Bottleneck Analysis

```
At 10,000 pings/second:
├── API Gateway: ✓ (handles easily)
├── Lambda: ✓ (auto-scales to ~100 instances)
├── Redis SADD: ✓ (~100k ops/sec capacity)
└── Network: ✓ (VPC internal)

At 100,000 pings/second:
├── API Gateway: ✓ (still fine)
├── Lambda: ⚠️ (need concurrency increase)
├── Redis: ⚠️ (need cluster mode)
└── Network: ✓
```

### Cost Estimate (AWS)

| Component | Estimate/month | Notes |
|-----------|----------------|-------|
| Lambda | $0-50 | Free tier covers light use |
| API Gateway | $3.50/million requests | |
| ElastiCache | $25-100 | cache.t3.micro to cache.r6g.large |
| CloudWatch | $5-20 | Logs and metrics |
| **Total** | **$35-200** | Depending on traffic |

---

## 10. Security Considerations

### Implemented
- Input validation via Pydantic
- No SQL/injection vectors (Redis only)
- Health endpoint doesn't expose internals

### Production Additions (in Terraform)
- VPC isolation (Redis not public)
- Security groups (least privilege)
- API Gateway authentication (API keys or Cognito)
- WAF for rate limiting and DDoS protection
- IAM roles with minimal permissions

---

## 11. Future Improvements

### Alternative Congestion Detection Approaches

The current implementation uses simple count-based thresholds. Here are more sophisticated approaches that could improve accuracy:

#### Speed-Based Detection (Industry Standard)
How Google Maps and Waze actually detect congestion:
- Track the same vehicle across multiple pings to calculate velocity
- Compare current speed to expected speed for that road segment
- 50 cars at 60mph = flowing traffic; 50 cars at 5mph = congested

**Requirements:**
- Vehicle IDs to track individual devices over time
- Frequent ping intervals (every few seconds)
- Multiple data points per vehicle for speed calculation

**Challenges:**
- Latency variance between pings affects time delta accuracy
- GPS jitter (±5-10m) introduces noise on short distances
- Network delays mean ping timestamp ≠ actual measurement time
- Would need smoothing/averaging across multiple readings

**Why not implemented:** Current data model treats pings as independent events. Would need vehicle tracking and more frequent pings to calculate reliable speeds.

#### Historical Comparison
Compare current vehicle count to historical average for that cell/time:
- Store rolling averages per cell per hour-of-day per day-of-week
- Congestion = current count significantly exceeds historical baseline
- Automatically adapts to each location's normal traffic patterns

**Benefits:**
- Self-calibrating (highway vs. residential street)
- Accounts for time-of-day patterns (rush hour vs. midnight)
- No manual threshold tuning needed

**Implementation:** Could use Redis sorted sets or a time-series database to store historical baselines.

#### Density-Based (Vehicles per km)
Normalize vehicle count by road segment length:
- Requires road network data (OpenStreetMap integration)
- More accurate than raw counts for varying segment sizes
- Formula: `density = vehicle_count / segment_length_km`

#### Relative Change Detection
Trigger on sudden changes rather than absolute values:
- 5 vehicles → 50 vehicles in 5 minutes = incident
- More sensitive to accidents/events than static thresholds
- Could use rate-of-change calculations on current counts

### If Given More Time

**Short-term:**
1. Historical baseline comparison (most impactful improvement)
2. WebSocket endpoint for real-time congestion push
3. Per-region threshold configuration
4. Batch ping ingestion endpoint

**Medium-term:**
1. Speed-based detection (if vehicle tracking data available)
2. Event-driven architecture (SQS/SNS for decoupling)
3. ML-based anomaly detection
4. Integration with external traffic data (road network, incidents)

**Long-term:**
1. Global deployment (multi-region)
2. Edge computing for lower latency
3. Predictive routing recommendations
4. Real-time incident detection

---

## Summary

This design prioritizes:
1. **Simplicity** - Minimal components, clear data flow
2. **Scalability** - Serverless, auto-scaling, horizontally scalable
3. **Correctness** - Unique device counting, proper spatial indexing
4. **Operability** - Metrics, health checks, infrastructure as code

Trade-offs favor operational simplicity over theoretical optimality, which is appropriate for an MVP that can evolve based on real usage patterns.
