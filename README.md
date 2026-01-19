# Congestion Monitor

Real-time traffic congestion monitoring using FastAPI, Redis, and H3 hexagonal grids.

Devices send location pings. The service tracks unique devices per geographic cell and returns congestion levels (LOW, MODERATE, HIGH) in near real-time.

## Documentation

| Document | Description |
|----------|-------------|
| [Architecture](docs/ARCHITECTURE.md) | System diagrams and component overview |
| [Design Decisions](docs/DESIGN.md) | Trade-offs, technology choices, scalability |
| [Terraform](terraform/README.md) | AWS deployment infrastructure |

## How It Works

- **H3 hexagonal grid** (resolution 8, ~460m cells) for spatial indexing
- **5-minute time buckets** for temporal aggregation
- **Redis Sets** track unique devices per cell+bucket (auto-deduplicated)
- **Redis Streams** for event-driven processing (alerts, analytics)
- **TTL-based expiration** - keys expire after 5 minutes, no cleanup needed
- **Speed-based congestion detection** using Z-scores against historical baselines
- **Supabase PostgreSQL** stores historical baselines (avg speed, avg count, variance)
- **Self-calibrating system** - each cell learns what "normal" looks like over time

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

# Query congestion (returns Z-score based level)
curl "http://127.0.0.1:8000/v1/congestion?lat=40.743&lon=-73.989"

# Query area (7 hexagons)
curl "http://127.0.0.1:8000/v1/congestion/area?lat=40.743&lon=-73.989&radius=1"

# Update baseline for a cell (triggers historical learning)
curl -X POST "http://127.0.0.1:8000/v1/baseline/update?lat=40.743&lon=-73.989"
```

Docs: http://127.0.0.1:8000/docs

## Testing

```bash
# Unit tests
pytest -v

# Load test with traffic simulation
python scripts/load_test.py --requests 500 --traffic moderate

# Load test and save baselines to database
python scripts/load_test.py --requests 500 --traffic moderate --save-baselines
```

## Event-Driven Demo

The system publishes events to a Redis Stream when pings are received or when congestion goes HIGH. Run the event consumer in one terminal, then trigger events in another:

```bash
# Terminal 1: Start the event consumer
python scripts/event_consumer.py

# Terminal 2: Run the demo to trigger HIGH congestion
python scripts/demo_congestion.py
```

The consumer will show ping events streaming in, and alert when congestion hits HIGH.

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
│   ├── congestion.py     # Z-score based congestion detection
│   └── database.py       # Supabase PostgreSQL connection
├── tests/                # Unit tests
├── scripts/
│   ├── load_test.py      # Load testing with speed simulation
│   ├── event_consumer.py # Event stream consumer
│   └── demo_congestion.py # Demo script for congestion
├── docs/                 # Architecture & design docs
└── terraform/            # AWS infrastructure (not deployed)
```

## Database Setup (Supabase)

The system uses Supabase PostgreSQL to store historical baselines. Create the table:

```sql
CREATE TABLE hex_baselines (
    cell_id VARCHAR(20) PRIMARY KEY,
    avg_speed FLOAT DEFAULT 0,
    avg_count FLOAT DEFAULT 0,
    speed_variance FLOAT DEFAULT 0,
    count_variance FLOAT DEFAULT 0,
    sample_count INT DEFAULT 0,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
```

**Note:** Use the Supabase connection **pooler** URL (port 6543) in your `.env` file for IPv4 compatibility.
