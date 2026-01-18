# Load Testing

`load_test.py` - Async load testing with performance metrics.

## Usage

```bash
# Default: 1000 requests, 50 concurrent
python scripts/load_test.py

# Custom configuration
python scripts/load_test.py --requests 5000 --devices 200 --concurrent 100

# Different URL
python scripts/load_test.py --url http://production.example.com:8000

# Include congestion queries
python scripts/load_test.py --with-queries
```

## Options

```
--url         API base URL (default: http://localhost:8000)
--requests    Number of requests (default: 1000)
--devices     Unique devices (default: 100)
--concurrent  Max concurrent requests (default: 50)
--with-queries  Include congestion query tests
--output      JSON output file (default: load_test_results.json)
```

## Metrics

- Throughput (req/s)
- Success rate (%)
- Latency percentiles (min, mean, median, P95, P99, max)
- Results exported to JSON
