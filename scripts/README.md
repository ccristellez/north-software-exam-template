# Scripts

Utility scripts for testing and demoing the Congestion Monitor API.

**Note:** The main load testing script is now in `tests/load_test.py` - see below.

---

## Congestion Demo

`demo_congestion.py` - Interactive demo showing percentile-based congestion detection.

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
3. Shows final congestion level with percentile comparison
4. Displays historical percentile data
5. Explains how the system calibrates over time

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

## Load Testing (Recommended)

The main load testing script is in **`tests/load_test.py`** and supports:
- Populating historical data directly to database
- Running concurrent load tests against the API
- Realistic traffic pattern simulation

### Usage

```bash
# Populate database with historical data + run load test
python tests/load_test.py --all

# Just populate historical data (for percentile calibration)
python tests/load_test.py --populate --days 7 --cells 10

# Just run load test (API must be running)
python tests/load_test.py --load --users 50 --requests 20
```

See `tests/load_test.py --help` for all options.

---

## Quick Start

1. Start Redis and the API:
   ```bash
   docker-compose up -d
   uvicorn src.api.main:app --reload
   ```

2. (Optional) Populate historical data for percentile calibration:
   ```bash
   python tests/load_test.py --populate --days 7 --cells 5
   ```

3. Run demo (in another terminal):
   ```bash
   python scripts/demo_congestion.py
   ```

4. Watch events (in a third terminal):
   ```bash
   python scripts/event_consumer.py
   ```
