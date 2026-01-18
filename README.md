# Congestion Monitor

A simple real-time congestion monitoring service built with FastAPI and Redis.

Devices send location pings to the service. The service aggregates recent activity by area and returns a basic congestion level (LOW, MODERATE, HIGH) for a given location.

---

## How it works

* **Areas** are defined by rounding latitude and longitude to 2 decimal places

  * Example: `40.743, -73.989` → `40.74_-73.99`

* **Time** is divided into fixed 5-minute buckets

* Each ping increments a Redis counter using the key format:

  ```
  cell:<cell_id>:bucket:<time_bucket>
  ```

* Redis keys automatically expire after 5 minutes (TTL), so only recent traffic is counted

* Congestion levels are derived from the number of recent pings:

  * LOW: 0–9
  * MODERATE: 10–29
  * HIGH: 30+

No raw events are stored. Only aggregated counters are kept.

---

## Requirements

* Python 3.12+
* Docker Desktop
* Git

---

## Installation & running locally

### 1. Start Redis

From the project root:

```
docker-compose up -d
```

Verify Redis:

```
docker-compose exec redis redis-cli ping
```

Expected output:

```
PONG
```

---

### 2. Set up Python environment

```
py -3.12 -m venv venv
venv\Scripts\activate
python -m pip install --upgrade pip
pip install -r requirements.txt
```

---

### 3. Run the API

```
uvicorn src.api.main:app --reload --host 127.0.0.1 --port 8000
```

---

### 4. Verify the service

```
curl http://127.0.0.1:8000/health
```

Expected response:

```
{"status":"healthy","redis":"connected"}
```

---

## API usage

### Send a ping

```
curl -X POST http://127.0.0.1:8000/v1/pings \
  -H "Content-Type: application/json" \
  -d '{"device_id":"car1","lat":40.743,"lon":-73.989}'
```

---

### Query congestion

```
curl "http://127.0.0.1:8000/v1/congestion?lat=40.743&lon=-73.989"
```

Example response:

```
{
  "cell_id": "40.74_-73.99",
  "vehicle_count": 12,
  "congestion_level": "MODERATE",
  "window_seconds": 300
}
```

---

## API documentation

Swagger UI is available at:

```
http://127.0.0.1:8000/docs
```

---

## Project structure

```
src/api/main.py          FastAPI app and endpoints
src/api/models.py       Request models
src/api/redis_client.py Redis connection helper
src/api/grid.py         Grid cell logic
src/api/time_utils.py   Time bucket logic
```

---

## Notes

This project is intentionally simple and optimized for clarity in a take-home setting. It demonstrates real-time aggregation, time-bucketed counters, and practical system trade-offs without over-engineering.
