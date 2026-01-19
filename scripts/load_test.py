#!/usr/bin/env python3
"""
Load Testing Script for Congestion Monitor API

Tests concurrent request handling and measures performance metrics.
Simulates realistic traffic patterns with multiple devices sending location pings.

Now includes speed data for historical baseline calibration:
- Normal traffic: 40-70 km/h
- Moderate congestion: 20-40 km/h
- Heavy congestion: 5-20 km/h
"""
import asyncio
import time
import random
import statistics
from datetime import datetime, timezone
from typing import List, Dict, Any
import argparse
import json

try:
    import httpx
except ImportError:
    print("Error: Missing httpx. Install with: pip install httpx")
    exit(1)

try:
    import redis
    REDIS_AVAILABLE = True
except ImportError:
    REDIS_AVAILABLE = False


# NYC area coordinates for realistic test data
NYC_CENTER = (40.7128, -74.0060)
NYC_RADIUS = 0.05  # ~5km radius

# Speed ranges for realistic traffic simulation (km/h)
SPEED_RANGES = {
    "free_flow": (50, 70),      # Light traffic, moving freely
    "moderate": (25, 45),       # Some slowdown
    "congested": (5, 20),       # Heavy traffic, crawling
}


def generate_random_location() -> tuple[float, float]:
    """Generate random coordinates within NYC area."""
    lat_offset = random.uniform(-NYC_RADIUS, NYC_RADIUS)
    lon_offset = random.uniform(-NYC_RADIUS, NYC_RADIUS)
    return (
        NYC_CENTER[0] + lat_offset,
        NYC_CENTER[1] + lon_offset
    )


def generate_random_speed(congestion_mode: str = "mixed") -> float:
    """
    Generate realistic speed based on traffic conditions.

    Args:
        congestion_mode: "free_flow", "moderate", "congested", or "mixed"

    Returns:
        Speed in km/h
    """
    if congestion_mode == "mixed":
        # Weighted random: 60% free flow, 30% moderate, 10% congested
        roll = random.random()
        if roll < 0.6:
            speed_range = SPEED_RANGES["free_flow"]
        elif roll < 0.9:
            speed_range = SPEED_RANGES["moderate"]
        else:
            speed_range = SPEED_RANGES["congested"]
    else:
        speed_range = SPEED_RANGES.get(congestion_mode, SPEED_RANGES["moderate"])

    return round(random.uniform(*speed_range), 1)


def generate_device_id(device_num: int) -> str:
    """Generate device ID."""
    return f"device_{device_num:04d}"


async def send_ping(
    client: httpx.AsyncClient,
    base_url: str,
    device_id: str,
    lat: float,
    lon: float,
    speed_kmh: float = None,
    congestion_mode: str = "mixed"
) -> Dict[str, Any]:
    """
    Send a single ping request and measure response time.

    Args:
        client: HTTP client
        base_url: API base URL
        device_id: Device identifier
        lat, lon: GPS coordinates
        speed_kmh: Speed in km/h (if None, generates random speed)
        congestion_mode: Traffic mode for speed generation

    Returns:
        dict with status, duration, and response data
    """
    start_time = time.perf_counter()

    # Generate speed if not provided
    if speed_kmh is None:
        speed_kmh = generate_random_speed(congestion_mode)

    try:
        response = await client.post(
            f"{base_url}/v1/pings",
            json={
                "device_id": device_id,
                "lat": lat,
                "lon": lon,
                "speed_kmh": speed_kmh,
                "timestamp": datetime.now(timezone.utc).isoformat()
            },
            timeout=10.0
        )

        duration = time.perf_counter() - start_time

        return {
            "success": response.status_code == 200,
            "status_code": response.status_code,
            "duration": duration,
            "speed_sent": speed_kmh,
            "response": response.json() if response.status_code == 200 else None,
            "error": None
        }

    except Exception as e:
        duration = time.perf_counter() - start_time
        return {
            "success": False,
            "status_code": 0,
            "duration": duration,
            "speed_sent": speed_kmh,
            "response": None,
            "error": str(e)
        }


async def send_congestion_query(
    client: httpx.AsyncClient,
    base_url: str,
    lat: float,
    lon: float
) -> Dict[str, Any]:
    """
    Send a congestion query request.

    Returns:
        dict with status, duration, and response data
    """
    start_time = time.perf_counter()

    try:
        response = await client.get(
            f"{base_url}/v1/congestion",
            params={"lat": lat, "lon": lon},
            timeout=10.0
        )

        duration = time.perf_counter() - start_time

        return {
            "success": response.status_code == 200,
            "status_code": response.status_code,
            "duration": duration,
            "response": response.json() if response.status_code == 200 else None,
            "error": None
        }

    except Exception as e:
        duration = time.perf_counter() - start_time
        return {
            "success": False,
            "status_code": 0,
            "duration": duration,
            "response": None,
            "error": str(e)
        }


async def run_load_test(
    base_url: str,
    num_requests: int,
    num_devices: int,
    concurrent_limit: int,
    include_queries: bool = False,
    congestion_mode: str = "mixed"
) -> Dict[str, Any]:
    """
    Run load test with specified parameters.

    Args:
        base_url: API base URL
        num_requests: Total number of ping requests to send
        num_devices: Number of unique devices
        concurrent_limit: Maximum concurrent requests
        include_queries: Whether to include congestion queries
        congestion_mode: Traffic simulation mode (free_flow, moderate, congested, mixed)

    Returns:
        dict with test results and metrics
    """
    results = {
        "ping_results": [],
        "query_results": [],
        "start_time": time.time(),
        "end_time": None
    }

    # Create async HTTP client with connection pooling
    async with httpx.AsyncClient(
        limits=httpx.Limits(
            max_keepalive_connections=concurrent_limit,
            max_connections=concurrent_limit * 2
        )
    ) as client:

        # Create semaphore to limit concurrent requests
        semaphore = asyncio.Semaphore(concurrent_limit)

        async def limited_send_ping(device_num: int):
            async with semaphore:
                device_id = generate_device_id(device_num % num_devices)
                lat, lon = generate_random_location()
                result = await send_ping(
                    client, base_url, device_id, lat, lon,
                    congestion_mode=congestion_mode
                )
                return result

        # Send ping requests
        print(f"Sending {num_requests} pings (mode: {congestion_mode})...")
        tasks = [limited_send_ping(i) for i in range(num_requests)]

        completed = 0
        for coro in asyncio.as_completed(tasks):
            result = await coro
            results["ping_results"].append(result)
            completed += 1
            if completed % 100 == 0:
                print(f"  {completed}/{num_requests} completed")

        # Optionally send congestion queries
        if include_queries:
            num_queries = num_requests // 10  # 10% of pings

            async def limited_send_query():
                async with semaphore:
                    lat, lon = generate_random_location()
                    result = await send_congestion_query(client, base_url, lat, lon)
                    return result

            print(f"Sending {num_queries} congestion queries...")
            tasks = [limited_send_query() for i in range(num_queries)]

            completed = 0
            for coro in asyncio.as_completed(tasks):
                result = await coro
                results["query_results"].append(result)
                completed += 1
                if completed % 10 == 0:
                    print(f"  {completed}/{num_queries} completed")

    results["end_time"] = time.time()
    return results


def calculate_metrics(results: Dict[str, Any]) -> Dict[str, Any]:
    """Calculate performance metrics from test results."""
    ping_results = results["ping_results"]
    query_results = results["query_results"]

    total_duration = results["end_time"] - results["start_time"]

    # Ping metrics
    ping_successes = [r for r in ping_results if r["success"]]
    ping_failures = [r for r in ping_results if not r["success"]]
    ping_durations = [r["duration"] for r in ping_successes]

    # Speed data from pings
    speeds_sent = [r.get("speed_sent") for r in ping_successes if r.get("speed_sent")]

    # Query metrics (if any)
    query_successes = [r for r in query_results if r["success"]]
    query_failures = [r for r in query_results if not r["success"]]
    query_durations = [r["duration"] for r in query_successes]

    metrics = {
        "total_duration": total_duration,
        "ping_metrics": {
            "total": len(ping_results),
            "success": len(ping_successes),
            "failed": len(ping_failures),
            "success_rate": len(ping_successes) / len(ping_results) * 100 if ping_results else 0,
            "throughput": len(ping_results) / total_duration if total_duration > 0 else 0,
            "latency": {
                "min": min(ping_durations) * 1000 if ping_durations else 0,
                "max": max(ping_durations) * 1000 if ping_durations else 0,
                "mean": statistics.mean(ping_durations) * 1000 if ping_durations else 0,
                "median": statistics.median(ping_durations) * 1000 if ping_durations else 0,
                "p95": statistics.quantiles(ping_durations, n=20)[18] * 1000 if len(ping_durations) > 20 else 0,
                "p99": statistics.quantiles(ping_durations, n=100)[98] * 1000 if len(ping_durations) > 100 else 0,
            },
            "speed_data": {
                "pings_with_speed": len(speeds_sent),
                "avg_speed_kmh": round(statistics.mean(speeds_sent), 1) if speeds_sent else 0,
                "min_speed_kmh": round(min(speeds_sent), 1) if speeds_sent else 0,
                "max_speed_kmh": round(max(speeds_sent), 1) if speeds_sent else 0,
            }
        }
    }

    if query_results:
        metrics["query_metrics"] = {
            "total": len(query_results),
            "success": len(query_successes),
            "failed": len(query_failures),
            "success_rate": len(query_successes) / len(query_results) * 100 if query_results else 0,
            "throughput": len(query_results) / total_duration if total_duration > 0 else 0,
            "latency": {
                "min": min(query_durations) * 1000 if query_durations else 0,
                "max": max(query_durations) * 1000 if query_durations else 0,
                "mean": statistics.mean(query_durations) * 1000 if query_durations else 0,
                "median": statistics.median(query_durations) * 1000 if query_durations else 0,
            }
        }

    return metrics


def display_results(metrics: Dict[str, Any], config: Dict[str, Any]):
    """Display test results in a formatted table."""
    print("\n" + "="*60)
    print("LOAD TEST RESULTS")
    print("="*60 + "\n")

    # Configuration
    print("Test Configuration:")
    print(f"  Base URL:         {config['base_url']}")
    print(f"  Total Requests:   {config['num_requests']}")
    print(f"  Unique Devices:   {config['num_devices']}")
    print(f"  Concurrent Limit: {config['concurrent_limit']}")
    print(f"  Traffic Mode:     {config.get('congestion_mode', 'mixed')}")
    print(f"  Total Duration:   {metrics['total_duration']:.2f}s")
    print()

    # Ping metrics
    ping_metrics = metrics["ping_metrics"]
    print("Ping Request Metrics:")
    print(f"  Total Requests: {ping_metrics['total']}")
    print(f"  Successful:     {ping_metrics['success']} ({ping_metrics['success_rate']:.1f}%)")
    print(f"  Failed:         {ping_metrics['failed']}")
    print(f"  Throughput:     {ping_metrics['throughput']:.1f} req/s")
    print()

    # Latency
    latency = ping_metrics["latency"]
    print("Ping Latency (milliseconds):")
    print(f"  Min:    {latency['min']:.2f} ms")
    print(f"  Mean:   {latency['mean']:.2f} ms")
    print(f"  Median: {latency['median']:.2f} ms")
    print(f"  P95:    {latency['p95']:.2f} ms")
    print(f"  P99:    {latency['p99']:.2f} ms")
    print(f"  Max:    {latency['max']:.2f} ms")
    print()

    # Speed data (for baseline calibration)
    speed_data = ping_metrics.get("speed_data", {})
    if speed_data.get("pings_with_speed", 0) > 0:
        print("Speed Data Sent:")
        print(f"  Pings with speed: {speed_data['pings_with_speed']}")
        print(f"  Avg speed:  {speed_data['avg_speed_kmh']:.1f} km/h")
        print(f"  Range:      {speed_data['min_speed_kmh']:.1f} - {speed_data['max_speed_kmh']:.1f} km/h")
        print()

    # Query metrics (if available)
    if "query_metrics" in metrics:
        query_metrics = metrics["query_metrics"]
        print("Congestion Query Metrics:")
        print(f"  Total Queries: {query_metrics['total']}")
        print(f"  Successful:    {query_metrics['success']} ({query_metrics['success_rate']:.1f}%)")
        print(f"  Failed:        {query_metrics['failed']}")
        print(f"  Throughput:    {query_metrics['throughput']:.1f} req/s")
        print(f"  Mean Latency:  {query_metrics['latency']['mean']:.2f} ms")
        print()

    # Performance summary
    if ping_metrics["success_rate"] >= 99.9:
        status = "EXCELLENT"
    elif ping_metrics["success_rate"] >= 95:
        status = "GOOD"
    else:
        status = "NEEDS IMPROVEMENT"

    print("="*60)
    print(f"Performance Status: {status}")
    print(f"Handled {ping_metrics['throughput']:.0f} requests/second with {latency['p95']:.1f}ms P95 latency")
    print("="*60 + "\n")


def save_results(metrics: Dict[str, Any], config: Dict[str, Any], filename: str):
    """Save test results to JSON file."""
    output = {
        "config": config,
        "metrics": metrics,
        "timestamp": datetime.now(timezone.utc).isoformat()
    }

    with open(filename, "w") as f:
        json.dump(output, f, indent=2)

    print(f"Results saved to {filename}")


async def main():
    parser = argparse.ArgumentParser(
        description="Load test the Congestion Monitor API"
    )
    parser.add_argument(
        "--url",
        default="http://localhost:8000",
        help="Base URL of the API (default: http://localhost:8000)"
    )
    parser.add_argument(
        "--requests",
        type=int,
        default=1000,
        help="Number of ping requests to send (default: 1000)"
    )
    parser.add_argument(
        "--devices",
        type=int,
        default=100,
        help="Number of unique devices (default: 100)"
    )
    parser.add_argument(
        "--concurrent",
        type=int,
        default=50,
        help="Maximum concurrent requests (default: 50)"
    )
    parser.add_argument(
        "--with-queries",
        action="store_true",
        help="Include congestion query requests"
    )
    parser.add_argument(
        "--traffic",
        choices=["mixed", "free_flow", "moderate", "congested"],
        default="mixed",
        help="Traffic simulation mode (default: mixed)"
    )
    parser.add_argument(
        "--output",
        default="load_test_results.json",
        help="Output file for results (default: load_test_results.json)"
    )
    parser.add_argument(
        "--save-baselines",
        action="store_true",
        help="Save baseline data to database after test (triggers /v1/baseline/update for each cell)"
    )

    args = parser.parse_args()

    config = {
        "base_url": args.url,
        "num_requests": args.requests,
        "num_devices": args.devices,
        "concurrent_limit": args.concurrent,
        "include_queries": args.with_queries,
        "congestion_mode": args.traffic
    }

    print("\n" + "="*60)
    print("Congestion Monitor API Load Test")
    print(f"Preparing to send {args.requests} requests with {args.concurrent} concurrent connections")
    print("="*60 + "\n")

    # Check if API is reachable
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{args.url}/health", timeout=5.0)
            if response.status_code == 200:
                print("OK API is reachable\n")
            else:
                print(f"WARN API returned status {response.status_code}\n")
    except Exception as e:
        print(f"ERROR Cannot reach API: {e}")
        print("Make sure the API is running on the specified URL\n")
        return

    # Run load test
    results = await run_load_test(
        args.url,
        args.requests,
        args.devices,
        args.concurrent,
        args.with_queries,
        args.traffic
    )

    # Calculate and display metrics
    metrics = calculate_metrics(results)
    display_results(metrics, config)

    # Save results
    save_results(metrics, config, args.output)

    # Save baselines to database if requested
    if args.save_baselines:
        await save_baselines_to_db(args.url, results)

    # Show event stream stats if Redis is available
    if REDIS_AVAILABLE:
        print_stream_stats()


async def save_baselines_to_db(base_url: str, results: Dict[str, Any]):
    """
    Save baseline data to Supabase by triggering /v1/baseline/update for each cell.

    This extracts unique cell IDs from successful ping responses and triggers
    a baseline update for each, which saves the Redis data to Supabase.
    """
    # Collect unique cell IDs from successful pings
    cell_ids = set()
    for result in results["ping_results"]:
        if result["success"] and result.get("response"):
            cell_id = result["response"].get("cell_id")
            if cell_id:
                cell_ids.add(cell_id)

    if not cell_ids:
        print("\nNo cell IDs found in responses - skipping baseline save")
        return

    print(f"\nSaving baselines to database for {len(cell_ids)} cells...")

    async with httpx.AsyncClient() as client:
        success_count = 0
        error_count = 0
        first_error = None

        for cell_id in cell_ids:
            try:
                response = await client.post(
                    f"{base_url}/v1/baseline/update",
                    params={"cell_id": cell_id},
                    timeout=10.0
                )
                if response.status_code == 200:
                    success_count += 1
                else:
                    error_count += 1
                    if first_error is None:
                        first_error = f"HTTP {response.status_code}"
            except Exception as e:
                error_count += 1
                if first_error is None:
                    first_error = str(e)

        print(f"  Saved {success_count}/{len(cell_ids)} baselines to database")
        if error_count > 0 and first_error:
            print(f"  WARNING: {error_count} failed - first error: {first_error}")
            print("  TIP: Make sure DATABASE_URL is set in .env file for Supabase")
        print()


def print_stream_stats():
    """Print Redis Stream statistics after the test."""
    try:
        r = redis.Redis(host="127.0.0.1", port=6379, decode_responses=True)
        r.ping()

        stream_name = "congestion:events"
        stream_length = r.xlen(stream_name)

        print("\nEvent Stream Stats:")
        print(f"  Stream: {stream_name}")
        print(f"  Total events: {stream_length}")

        # Count event types
        events = r.xrange(stream_name, count=1000)
        ping_count = sum(1 for _, data in events if data.get("event_type") == "ping_received")
        alert_count = sum(1 for _, data in events if data.get("event_type") == "high_congestion")

        print(f"  Ping events: {ping_count}")
        print(f"  High congestion alerts: {alert_count}")
        print()

    except Exception as e:
        # Silently skip if Redis not available or stream doesn't exist
        pass


if __name__ == "__main__":
    asyncio.run(main())
