# Congestion Monitor

Real-time congestion monitoring using FastAPI and Redis.

Devices send location pings. The service tracks unique devices per H3 hexagon and returns congestion levels (LOW, MODERATE, HIGH).

## How it works

- H3 hexagonal grid (resolution 8, ~460m cells) for spatial indexing
- 5-minute time buckets for temporal aggregation
- Redis Sets track unique devices per cell+bucket
- Keys expire after 5 minutes
- Congestion thresholds: LOW (0-9), MODERATE (10-29), HIGH (30+)

## Setup

```bash
# Start Redis
docker-compose up -d

# Python environment
py -3.12 -m venv venv
venv\Scripts\activate
pip install -r requirements.txt

# Run API
uvicorn src.api.main:app --reload

# Verify
curl http://127.0.0.1:8000/health
```

## API

```bash
# Send ping
curl -X POST http://127.0.0.1:8000/v1/pings \
  -H "Content-Type: application/json" \
  -d '{"device_id":"car1","lat":40.743,"lon":-73.989}'

# Query congestion
curl "http://127.0.0.1:8000/v1/congestion?lat=40.743&lon=-73.989"

# Query area (7 hexagons)
curl "http://127.0.0.1:8000/v1/congestion/area?lat=40.743&lon=-73.989&radius=1"
```

Docs: http://127.0.0.1:8000/docs

## Testing

```bash
# Unit tests
pytest -v

# Load test
python scripts/load_test.py
```
