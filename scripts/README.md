# Scripts

Utility scripts for testing and demoing the Congestion Monitor API.

## Load Testing

`load_test.py` - Async load testing with performance metrics and speed data.

### Usage

```bash
# Default: 1000 requests, 50 concurrent, mixed traffic speeds
python scripts/load_test.py

# Custom configuration
python scripts/load_test.py --requests 5000 --devices 200 --concurrent 100

# Different traffic modes
python scripts/load_test.py --traffic free_flow    # 50-70 km/h
python scripts/load_test.py --traffic moderate     # 25-45 km/h
python scripts/load_test.py --traffic congested    # 5-20 km/h
python scripts/load_test.py --traffic mixed        # Weighted mix (default)

# Include congestion queries
python scripts/load_test.py --with-queries

# Save baseline data to Supabase database after test
python scripts/load_test.py --requests 500 --traffic moderate --save-baselines
```

### Options

```
--url            API base URL (default: http://localhost:8000)
--requests       Number of requests (default: 1000)
--devices        Unique devices (default: 100)
--concurrent     Max concurrent requests (default: 50)
--traffic        Traffic mode: mixed, free_flow, moderate, congested (default: mixed)
--with-queries   Include congestion query tests
--save-baselines Save baseline data to database after test
--output         JSON output file (default: load_test_results.json)
```

### Metrics

- Throughput (req/s)
- Success rate (%)
- Latency percentiles (min, mean, median, P95, P99, max)
- Speed data statistics (avg, min, max)
- Results exported to JSON

---

## Congestion Demo

`demo_congestion.py` - Interactive demo showing speed-based congestion detection.

### Usage

```bash
# Default: moderate traffic (20-40 km/h)
python scripts/demo_congestion.py

# Simulate heavy traffic (slow speeds)
python scripts/demo_congestion.py --slow

# Simulate free-flowing traffic (fast speeds)
python scripts/demo_congestion.py --fast

# Custom number of pings
python scripts/demo_congestion.py --count 50
```

### What it shows

1. Sends pings with speed data to a single location
2. Displays vehicle count and speed for each ping
3. Shows final congestion level with Z-score debug info
4. Displays historical baseline data
5. Explains how to build up baselines for calibration

---

## Event Consumer

`event_consumer.py` - Real-time event stream viewer.

### Usage

```bash
# Watch events in real-time
python scripts/event_consumer.py

# Run alongside the demo
# Terminal 1:
python scripts/event_consumer.py
# Terminal 2:
python scripts/demo_congestion.py
```

Shows:
- Ping events as they arrive
- High congestion alerts
- Event metadata (cell ID, count, timestamp)

---

## Quick Start

1. Start the API:
   ```bash
   uvicorn src.api.main:app --reload
   ```

2. Run demo (in another terminal):
   ```bash
   python scripts/demo_congestion.py
   ```

3. Run load test and save baselines to database:
   ```bash
   python scripts/load_test.py --requests 500 --traffic moderate --save-baselines
   ```
