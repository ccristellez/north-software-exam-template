# Congestion Monitor

Real-time traffic congestion monitoring using FastAPI, Redis, and H3 hexagonal grids.

Devices send location pings. The service tracks unique devices per geographic cell and returns congestion levels (LOW, MODERATE, HIGH) in near real-time.

## Documentation

| Document | Description |
|----------|-------------|
| [Architecture](docs/ARCHITECTURE.md) | System diagrams and component overview |
| [Design Decisions](docs/DESIGN.md) | Trade-offs, technology choices, scalability |
| [Walkthrough](docs/WALKTHROUGH.md) | Step-by-step code flow explanation |
| [Terraform](terraform/README.md) | AWS deployment infrastructure |

## How It Works

- **H3 hexagonal grid** (resolution 8, ~460m cells) for spatial indexing
- **5-minute time buckets** for temporal aggregation
- **Redis Sets** track unique devices per cell+bucket (auto-deduplicated)
- **Redis Streams** for event-driven processing (alerts, analytics)
- **TTL-based expiration** - keys expire after 5 minutes, no cleanup needed
- **Percentile-based congestion detection** comparing current speed to historical patterns
- **Supabase PostgreSQL** stores raw bucket data for percentile queries (PERCENTILE_CONT)
- **Self-calibrating system** - each cell learns what "normal" looks like after 20+ samples
- **Fallback thresholds** for new cells without enough historical data

## Quick Start

```bash
# Start Redis
docker-compose up -d

# Python environment
py -3.12 -m venv venv
venv\Scripts\activate
pip install -r requirements.txt

# Configure database (optional - for historical baselines)
# Create .env file with Supabase connection pooler URL:
# DATABASE_URL=postgresql://user:pass@your-project.pooler.supabase.com:6543/postgres

# Run API
uvicorn src.api.main:app --reload

# Verify
curl http://127.0.0.1:8000/health
```

## API

```bash
# Send ping with speed data
curl -X POST http://127.0.0.1:8000/v1/pings \
  -H "Content-Type: application/json" \
  -d '{"device_id":"car1","lat":40.743,"lon":-73.989,"speed_kmh":45.5}'

# Query congestion (returns percentile-based level)
curl "http://127.0.0.1:8000/v1/congestion?lat=40.743&lon=-73.989"

# Query area (7 hexagons)
curl "http://127.0.0.1:8000/v1/congestion/area?lat=40.743&lon=-73.989&radius=1"

# View historical percentiles for a cell
curl "http://127.0.0.1:8000/v1/history?lat=40.743&lon=-73.989"
```

Docs: http://127.0.0.1:8000/docs

## Testing

```bash
# Run all unit tests
pytest -v

# Run tests with coverage
pytest -v --cov=src

# Run a specific test file
pytest tests/test_api.py -v
```

**Note:** Unit tests use mocked Redis, so no Redis server is required.

### Load Testing

```bash
# Populate historical data + run load test
python tests/load_test.py --all

# Just populate historical data
python tests/load_test.py --populate --days 7 --cells 10

# Just run load test (requires API running)
python tests/load_test.py --load --users 50 --requests 20
```

## Demo Guide

This section shows how to demonstrate the system's features. You'll need **4 terminal windows**.

### Setup (Do This First)

```bash
# Terminal 1: Start Redis
docker-compose up -d

# Terminal 2: Start API server
venv\Scripts\activate
uvicorn src.api.main:app --reload

# Terminal 3: Start event consumer (shows real-time events)
venv\Scripts\activate
python scripts/event_consumer.py

# Terminal 4: Run demos (see scenarios below)
venv\Scripts\activate
```

### Demo Scenarios

**Scenario 1: Basic Congestion Detection (Fallback Mode)**
Shows how the system works without historical data using absolute thresholds.

```bash
# Send traffic and watch congestion build up
python scripts/demo_congestion.py

# Try different traffic patterns:
python scripts/demo_congestion.py --slow   # Heavy traffic (5-15 km/h) → HIGH
python scripts/demo_congestion.py --fast   # Free flow (50-70 km/h) → LOW
```

**Scenario 2: Percentile-Based Detection (Calibrated Mode)**
Shows how cells learn "normal" traffic patterns.

```bash
# First, populate historical data (requires DATABASE_URL in .env)
python tests/load_test.py --populate --days 7 --cells 5

# Now run demo - it will compare to historical percentiles
python scripts/demo_congestion.py
```

**Scenario 3: Load Testing**
Shows system performance under load.

```bash
# Run 1000 requests with 50 concurrent users
python tests/load_test.py --load --users 50 --requests 20

# Watch Terminal 3 - events stream in real-time
```

**Scenario 4: API Exploration**
Show the interactive API docs.

```bash
# Open in browser
http://127.0.0.1:8000/docs
```

### Key Features to Highlight

| Feature | How to Show | Terminal |
|---------|-------------|----------|
| **Real-time counting** | Send pings, show count increasing | 4 |
| **Event streaming** | Watch events appear as pings arrive | 3 |
| **HIGH congestion alert** | Run `--slow` demo, watch alert fire | 3 |
| **Percentile detection** | Populate data, then run demo | 4 |
| **API docs** | Open /docs in browser | Browser |
| **Load performance** | Run load test, show req/s | 4 |

### Key Talking Points

1. **H3 Hexagons** - "I use Uber's H3 for spatial indexing because hexagons have equidistant neighbors"
2. **Redis SETs** - "Devices are auto-deduplicated - same car pinging twice only counts once"
3. **TTL expiration** - "Old data cleans itself up - no cron jobs needed"
4. **Percentile detection** - "Each cell learns what's normal - a highway vs side street calibrates differently"
5. **Event-driven** - "Redis Streams decouple the API from downstream processing"

## Event-Driven Architecture

The system publishes events to a Redis Stream when pings are received or when congestion goes HIGH:

```bash
# Terminal 3: Watch events
python scripts/event_consumer.py

# Terminal 4: Trigger events
python scripts/demo_congestion.py
```

The consumer shows ping events streaming in, and alerts when congestion hits HIGH (30+ vehicles).

## Prometheus Metrics

The API exposes Prometheus metrics at `GET /metrics` for monitoring and observability.

### Accessing Metrics

```bash
# View raw metrics
curl http://127.0.0.1:8000/metrics

# With Prometheus server, add to prometheus.yml:
scrape_configs:
  - job_name: 'congestion-monitor'
    static_configs:
      - targets: ['localhost:8000']
```

### Available Metrics

| Metric | Type | Labels | What It Tells You |
|--------|------|--------|-------------------|
| `ping_requests_total` | Counter | `status` | Total pings received. High `rate_limited` count = devices hitting limits |
| `congestion_requests_total` | Counter | `endpoint`, `status` | Query volume per endpoint |
| `request_duration_seconds` | Histogram | `endpoint` | Latency distribution (p50, p95, p99) |
| `unique_devices_per_bucket` | Gauge | `cell_id` | Current device count per cell |
| `congestion_level_count` | Counter | `level` | Distribution of LOW/MODERATE/HIGH classifications |
| `redis_operations_total` | Counter | `operation`, `status` | Redis health - watch for `error` status |

### Interpreting the Metrics

**Throughput:**
- `rate(ping_requests_total[5m])` = pings per second
- Healthy: scales with expected traffic. Sudden drops may indicate client issues.

**Latency:**
- `histogram_quantile(0.95, request_duration_seconds_bucket)` = p95 latency
- Healthy: < 100ms for single cell, < 200ms for area queries
- High latency: check Redis connectivity, database queries

**Error Rate:**
- `rate(ping_requests_total{status="error"}[5m]) / rate(ping_requests_total[5m])` = error rate
- Healthy: < 1%. High rate = investigate logs

**Congestion Distribution:**
- `congestion_level_count{level="HIGH"}` should be small fraction of total
- Sudden spike in HIGH = real congestion event or detection issue

**Redis Health:**
- `redis_operations_total{status="error"}` should be 0
- Any errors = Redis connectivity issues

### Grafana Dashboard (Example Queries)

```promql
# Request rate
sum(rate(ping_requests_total[5m]))

# p95 latency by endpoint
histogram_quantile(0.95, sum(rate(request_duration_seconds_bucket[5m])) by (le, endpoint))

# Error rate percentage
sum(rate(ping_requests_total{status="error"}[5m])) / sum(rate(ping_requests_total[5m])) * 100

# Congestion level distribution
sum by (level) (congestion_level_count)
```

## Cloud Deployment

See [terraform/README.md](terraform/README.md) for AWS deployment using:
- AWS Lambda + API Gateway (serverless)
- ElastiCache Redis (managed)
- Multi-AZ with auto-failover (production)

```bash
cd terraform
terraform init
terraform plan -var-file=environments/dev/terraform.tfvars
```

## Project Structure

```
congestion-monitor/
├── src/api/              # FastAPI application
│   ├── main.py           # API endpoints
│   ├── grid.py           # H3 spatial indexing
│   ├── time_utils.py     # Time bucketing
│   ├── models.py         # Pydantic models
│   ├── metrics.py        # Prometheus metrics
│   ├── events.py         # Redis Stream event publishing
│   ├── congestion.py     # Percentile-based congestion detection
│   └── database.py       # Supabase PostgreSQL connection
├── tests/                # Unit tests
├── scripts/
│   ├── load_test.py      # Load testing with speed simulation
│   ├── event_consumer.py # Event stream consumer
│   └── demo_congestion.py # Demo script for congestion
├── docs/                 # Architecture & design docs
└── terraform/            # AWS infrastructure (not deployed)
```

## Database

**No setup required.** The Supabase PostgreSQL database is pre-configured and ready to use.

The database stores historical bucket data for percentile-based congestion detection. When you run the API, it automatically connects to the shared database with existing historical data.

**Note:** For a production deployment, you would move the database URL to environment variables or a secrets manager. See `src/api/database.py` for details.
