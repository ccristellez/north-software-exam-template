#!/usr/bin/env python3
"""
Load Testing Script for Congestion Monitor API

Tests concurrent request handling and measures performance metrics.
Simulates realistic traffic patterns with multiple devices sending location pings.
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
    import rich
    from rich.console import Console
    from rich.table import Table
    from rich.progress import Progress, SpinnerColumn, TimeElapsedColumn
    from rich.panel import Panel
except ImportError:
    print("Error: Missing dependencies. Install with: pip install httpx rich")
    exit(1)

console = Console()


# NYC area coordinates for realistic test data
NYC_CENTER = (40.7128, -74.0060)
NYC_RADIUS = 0.05  # ~5km radius


def generate_random_location() -> tuple[float, float]:
    """Generate random coordinates within NYC area."""
    lat_offset = random.uniform(-NYC_RADIUS, NYC_RADIUS)
    lon_offset = random.uniform(-NYC_RADIUS, NYC_RADIUS)
    return (
        NYC_CENTER[0] + lat_offset,
        NYC_CENTER[1] + lon_offset
    )


def generate_device_id(device_num: int) -> str:
    """Generate device ID."""
    return f"device_{device_num:04d}"


async def send_ping(
    client: httpx.AsyncClient,
    base_url: str,
    device_id: str,
    lat: float,
    lon: float
) -> Dict[str, Any]:
    """
    Send a single ping request and measure response time.

    Returns:
        dict with status, duration, and response data
    """
    start_time = time.perf_counter()

    try:
        response = await client.post(
            f"{base_url}/v1/pings",
            json={
                "device_id": device_id,
                "lat": lat,
                "lon": lon,
                "timestamp": datetime.now(timezone.utc).isoformat()
            },
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
    include_queries: bool = False
) -> Dict[str, Any]:
    """
    Run load test with specified parameters.

    Args:
        base_url: API base URL
        num_requests: Total number of ping requests to send
        num_devices: Number of unique devices
        concurrent_limit: Maximum concurrent requests
        include_queries: Whether to include congestion queries

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
                result = await send_ping(client, base_url, device_id, lat, lon)
                return result

        # Send ping requests
        with Progress(
            SpinnerColumn(),
            *Progress.get_default_columns(),
            TimeElapsedColumn(),
            console=console
        ) as progress:

            task = progress.add_task(
                f"Sending {num_requests} pings...",
                total=num_requests
            )

            # Create tasks for all requests
            tasks = [limited_send_ping(i) for i in range(num_requests)]

            # Execute with progress updates
            for coro in asyncio.as_completed(tasks):
                result = await coro
                results["ping_results"].append(result)
                progress.advance(task)

        # Optionally send congestion queries
        if include_queries:
            num_queries = num_requests // 10  # 10% of pings

            async def limited_send_query():
                async with semaphore:
                    lat, lon = generate_random_location()
                    result = await send_congestion_query(client, base_url, lat, lon)
                    return result

            with Progress(
                SpinnerColumn(),
                *Progress.get_default_columns(),
                TimeElapsedColumn(),
                console=console
            ) as progress:

                task = progress.add_task(
                    f"Sending {num_queries} congestion queries...",
                    total=num_queries
                )

                tasks = [limited_send_query() for i in range(num_queries)]

                for coro in asyncio.as_completed(tasks):
                    result = await coro
                    results["query_results"].append(result)
                    progress.advance(task)

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
    console.print()
    console.print(Panel.fit(
        "Load Test Results"
    ))
    console.print()

    # Configuration table
    config_table = Table(title="Test Configuration", show_header=False)
    config_table.add_column("Parameter")
    config_table.add_column("Value")

    config_table.add_row("Base URL", config["base_url"])
    config_table.add_row("Total Requests", str(config["num_requests"]))
    config_table.add_row("Unique Devices", str(config["num_devices"]))
    config_table.add_row("Concurrent Limit", str(config["concurrent_limit"]))
    config_table.add_row("Total Duration", f"{metrics['total_duration']:.2f}s")

    console.print(config_table)
    console.print()

    # Ping metrics table
    ping_metrics = metrics["ping_metrics"]

    results_table = Table(title="Ping Request Metrics")
    results_table.add_column("Metric")
    results_table.add_column("Value")

    results_table.add_row("Total Requests", str(ping_metrics["total"]))
    results_table.add_row("Successful", f"{ping_metrics['success']} ({ping_metrics['success_rate']:.1f}%)")
    results_table.add_row("Failed", str(ping_metrics["failed"]))
    results_table.add_row("Throughput", f"{ping_metrics['throughput']:.1f} req/s")

    console.print(results_table)
    console.print()

    # Latency table
    latency = ping_metrics["latency"]

    latency_table = Table(title="Ping Latency (milliseconds)")
    latency_table.add_column("Percentile")
    latency_table.add_column("Latency")

    latency_table.add_row("Min", f"{latency['min']:.2f} ms")
    latency_table.add_row("Mean", f"{latency['mean']:.2f} ms")
    latency_table.add_row("Median", f"{latency['median']:.2f} ms")
    latency_table.add_row("P95", f"{latency['p95']:.2f} ms")
    latency_table.add_row("P99", f"{latency['p99']:.2f} ms")
    latency_table.add_row("Max", f"{latency['max']:.2f} ms")

    console.print(latency_table)
    console.print()

    # Query metrics (if available)
    if "query_metrics" in metrics:
        query_metrics = metrics["query_metrics"]

        query_table = Table(title="Congestion Query Metrics")
        query_table.add_column("Metric")
        query_table.add_column("Value")

        query_table.add_row("Total Queries", str(query_metrics["total"]))
        query_table.add_row("Successful", f"{query_metrics['success']} ({query_metrics['success_rate']:.1f}%)")
        query_table.add_row("Failed", str(query_metrics["failed"]))
        query_table.add_row("Throughput", f"{query_metrics['throughput']:.1f} req/s")
        query_table.add_row("Mean Latency", f"{query_metrics['latency']['mean']:.2f} ms")

        console.print(query_table)
        console.print()

    # Performance summary
    if ping_metrics["success_rate"] >= 99.9:
        status = "EXCELLENT"
    elif ping_metrics["success_rate"] >= 95:
        status = "GOOD"
    else:
        status = "NEEDS IMPROVEMENT"

    console.print(Panel.fit(
        f"Performance Status: {status}\n"
        f"Handled {ping_metrics['throughput']:.0f} requests/second with "
        f"{latency['p95']:.1f}ms P95 latency"
    ))


def save_results(metrics: Dict[str, Any], config: Dict[str, Any], filename: str):
    """Save test results to JSON file."""
    output = {
        "config": config,
        "metrics": metrics,
        "timestamp": datetime.now(timezone.utc).isoformat()
    }

    with open(filename, "w") as f:
        json.dump(output, f, indent=2)

    console.print(f"\nResults saved to {filename}")


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
        "--output",
        default="load_test_results.json",
        help="Output file for results (default: load_test_results.json)"
    )

    args = parser.parse_args()

    config = {
        "base_url": args.url,
        "num_requests": args.requests,
        "num_devices": args.devices,
        "concurrent_limit": args.concurrent,
        "include_queries": args.with_queries
    }

    console.print(Panel.fit(
        "Congestion Monitor API Load Test\n"
        f"Preparing to send {args.requests} requests with {args.concurrent} concurrent connections"
    ))
    console.print()

    # Check if API is reachable
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{args.url}/health", timeout=5.0)
            if response.status_code == 200:
                console.print("OK API is reachable")
            else:
                console.print(f"WARN API returned status {response.status_code}")
    except Exception as e:
        console.print(f"ERROR Cannot reach API: {e}")
        console.print("Make sure the API is running on the specified URL")
        return

    console.print()

    # Run load test
    results = await run_load_test(
        args.url,
        args.requests,
        args.devices,
        args.concurrent,
        args.with_queries
    )

    # Calculate and display metrics
    metrics = calculate_metrics(results)
    display_results(metrics, config)

    # Save results
    save_results(metrics, config, args.output)


if __name__ == "__main__":
    asyncio.run(main())
