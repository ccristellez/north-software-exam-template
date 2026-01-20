# Design Decisions & Trade-offs

This document explains the **why** behind architectural choices: technology decisions, trade-offs accepted, and scaling strategy.

For system diagrams, data models, and component structure, see [ARCHITECTURE.md](./ARCHITECTURE.md).

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

**Why H3 Resolution 8?**

Resolution 8 (~460m cells) was chosen because it matches the natural scale of traffic variation:

- **Approximates 2-4 city blocks** - This is the granularity at which congestion actually varies in urban environments. One intersection can be jammed while the next block flows freely.
- **Small enough for localized detection** - Can distinguish congestion at one intersection vs. another a few hundred meters away.
- **Large enough for meaningful counts** - A reasonable number of devices (10-30) can populate a single cell during rush hour, giving statistically meaningful congestion readings.
- **Aligns with industry visualization** - Google Maps and Waze color traffic at roughly this granularity—road segments of a few hundred meters, not entire neighborhoods.
- **Practical query radius** - Area queries with k=2 (~19 cells) cover approximately 1km, suitable for "what's traffic like around here?" queries.

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

## 4. Congestion Detection: Percentile-Based with Historical Buckets

### Decision
Self-calibrating congestion detection using percentile comparison against historical bucket data, with fallback to simple thresholds for uncalibrated cells.

### How It Works

Each hexagon cell learns its own "normal" traffic patterns over time:

```
1. Store raw bucket data: vehicle_count, avg_speed, hour_of_day, day_of_week
2. After 20+ samples, cell is "calibrated"
3. Query percentiles using PostgreSQL's PERCENTILE_CONT
4. Compare current values to historical percentiles:
   - Speed < p25  → HIGH congestion (slower than 75% of history)
   - Speed < p50  → MODERATE congestion (slower than typical)
   - Speed >= p50 → LOW congestion (normal or better)
```

### Fallback Thresholds (Uncalibrated Cells)

When a cell has < 20 samples, use absolute thresholds:
```
Speed-based:
- < 15 km/h → HIGH
- < 40 km/h → MODERATE

Count-based:
- 30+ vehicles → HIGH
- 10+ vehicles → MODERATE
```

### Why Percentiles?

**Chose percentile-based detection because:**
- Easy to understand: "below 25th percentile" vs "1.5 standard deviations"
- Self-calibrating (highway vs. residential adapts automatically)
- More robust to outliers than mean/std deviation
- Simple SQL queries (PERCENTILE_CONT) instead of complex algorithms
- Easy to explain in interviews

**Trade-offs accepted:**
- Requires time to build history (20 samples minimum)
- Storage needed for historical data (Supabase PostgreSQL)
- Database query per congestion check (but fast with indexes)

### Storage

Historical bucket data stored in Supabase PostgreSQL (`bucket_history` table):
- `cell_id`, `bucket_time`: Unique identifier
- `vehicle_count`, `avg_speed`: Raw bucket data
- `hour_of_day`, `day_of_week`: For time-aware queries
- Saved automatically via update-on-write pattern

---

## 5. History Update Strategy

### Decision
Update-on-Write pattern: save previous bucket to history when a new ping arrives.

### Why Update-on-Write?

| Approach | Pros | Cons |
|----------|------|------|
| **Update-on-Write (chosen)** | No external dependencies, simple, auto-updates | Data lost if cell has traffic then goes quiet |
| Update-on-Read | Simple | Data lost for unqueried cells |
| Background Job (cron/Lambda) | Complete data capture | Requires external scheduler, more infrastructure |
| Save Every Ping | No data loss | Corrupts variance with partial bucket data |

### Trade-offs Accepted

**Chose Update-on-Write because:**
- No cron jobs, Lambda functions, or external schedulers required
- Cells with consistent traffic always get baselines updated
- Acceptable for MVP: cells that go quiet probably don't need baseline updates

**Accepted limitation:**
If a cell receives traffic in bucket N but zero traffic in bucket N+1, bucket N's data is lost when Redis TTL expires. Rare in practice for active cells.

---

## 6. Historical Data Storage

### Decision
Store raw bucket data in PostgreSQL, compute percentiles on read.

### Why Raw Buckets?

| Approach | Pros | Cons |
|----------|------|------|
| **Store raw buckets (chosen)** | Debuggable, flexible queries, easy to explain | Slightly more storage |
| Store computed stats (Welford) | Less storage | Complex math, hard to debug/explain |
| Store all individual pings | Maximum flexibility | Excessive storage growth |

### Trade-offs Accepted

**Chose raw bucket storage because:**
- Easy to query and debug with standard SQL
- PERCENTILE_CONT handles the math
- Supports time-of-day filtering with simple WHERE clauses
- No complex algorithms to explain
- Storage is cheap; simplicity is valuable

---

## 7. Area Query Optimization

### Decision
Use Redis pipeline for batch queries across multiple cells.

### Why Pipelines?

| Approach | Pros | Cons |
|----------|------|------|
| **Redis pipeline (chosen)** | 1 round-trip for N cells | Slightly more complex code |
| Individual queries | Simple code | N round-trips = high latency |
| Cached aggregates | Fast reads | Stale data, complex invalidation |

### Trade-offs Accepted

**Chose Redis pipelines because:**
- For radius=2 (19 cells): 38 round-trips → 1 round-trip
- Dramatically reduces latency for area queries
- Minimal code complexity increase

---

## 8. API Design

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

## 9. Event-Driven Architecture: Redis Streams

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

## 10. Observability: Prometheus Metrics

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

## 11. Infrastructure: Serverless on AWS

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

## 12. Scalability Analysis

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
├── Lambda:(need concurrency increase)
├── Redis: (need cluster mode)
└── Network: ✓
```

### Cost Estimate (AWS us-east-1)

| Traffic Level | Requests/Day | Lambda | ElastiCache | API Gateway | Supabase | Total |
|---------------|--------------|--------|-------------|-------------|----------|-------|
| **Dev/Test** | 10k | $0 (free tier) | $12 (cache.t3.micro) | $0.04 | $0 (free) | **~$12/mo** |
| **Light** | 100k | $0 (free tier) | $12 (cache.t3.micro) | $0.35 | $25 (Pro) | **~$37/mo** |
| **Medium** | 1M | $2 | $25 (cache.t3.small) | $3.50 | $25 (Pro) | **~$56/mo** |
| **Heavy** | 10M | $20 | $50 (cache.m6g.large) | $35 | $25 (Pro) | **~$130/mo** |
| **Scale** | 100M | $200 | $200 (cluster) | $350 | $599 (Team) | **~$1,350/mo** |

**Pricing breakdown:**
- **Lambda**: $0.20 per 1M requests + $0.0000166667/GB-sec (128MB, 100ms avg)
- **API Gateway**: $3.50 per million requests (REST API)
- **ElastiCache**: cache.t3.micro=$0.017/hr, cache.t3.small=$0.034/hr, cache.m6g.large=$0.068/hr
- **Supabase**: Free (500MB), Pro $25/mo (8GB), Team $599/mo (unlimited)

---

## 13. Security Considerations

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

## 14. Future Improvements

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

#### Historical Comparison (IMPLEMENTED)
Compare current values to historical percentiles for that cell:
- Store raw bucket data per cell (vehicle_count, avg_speed, timestamps)
- Query percentiles using SQL PERCENTILE_CONT
- Congestion = current speed below historical 25th/50th percentile
- Automatically adapts to each location's normal traffic patterns

**Benefits:**
- Self-calibrating (highway vs. residential street)
- Easy to understand and explain ("below 25th percentile")
- Simple SQL queries, no complex algorithms
- Supports time-aware filtering (hour_of_day, day_of_week)

**Implementation:** Uses Supabase PostgreSQL to store bucket_history table with raw data.

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
1. WebSocket endpoint for real-time congestion push
2. Per-region threshold configuration overrides
3. Batch ping ingestion endpoint

**Medium-term:**
1. Vehicle tracking for velocity-based detection (across multiple pings)
2. Event-driven architecture (SQS/SNS for decoupling)
3. ML-based anomaly detection
4. Integration with external traffic data (road network, incidents)

**Long-term:**
1. Global deployment (multi-region)
2. Edge computing for lower latency
3. Predictive routing recommendations
4. Real-time incident detection

---

## Technology Summary

| Technology | Why Chosen | Alternatives Considered |
|------------|------------|------------------------|
| **FastAPI** | Async support, automatic OpenAPI docs, Pydantic validation, Lambda-ready | Flask (no async), Django (too heavy) |
| **Redis** | O(1) SET operations, built-in TTL, perfect for ephemeral counting | PostgreSQL (too slow for writes), DynamoDB (more complex) |
| **H3 Hexagons** | Uniform cell sizes, equidistant neighbors, efficient k-ring queries | Geohash (rectangular, edge distortion), S2 (more complex) |
| **PostgreSQL** | Durable storage, PERCENTILE_CONT for stats, time-of-day queries | Store stats in Redis (no durability), NoSQL (harder queries) |
| **Redis Streams** | Already have Redis, built-in persistence, simple XREAD consumer | Kafka (overkill), SQS (cloud-only) |
| **Prometheus** | Industry standard, pull-based simplicity, Grafana integration | CloudWatch (AWS-only), custom metrics |
| **Lambda** | Auto-scaling, pay-per-use, zero ops | ECS (more ops), EC2 (manual scaling) |

**Core principle:** Use the simplest technology that solves the problem well.

---

## Summary

This design prioritizes:
1. **Simplicity** - Minimal components, clear data flow
2. **Scalability** - Serverless, auto-scaling, horizontally scalable
3. **Correctness** - Unique device counting, proper spatial indexing
4. **Operability** - Metrics, health checks, infrastructure as code

Trade-offs favor operational simplicity over theoretical optimality—appropriate for an MVP that can evolve based on real usage patterns.
