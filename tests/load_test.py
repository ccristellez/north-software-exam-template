"""
Load test script for the Traffic Congestion API.

This script:
1. Populates the bucket_history table with realistic historical data (direct DB insert)
2. Runs concurrent load tests against the API endpoints
3. Measures response times and throughput

Usage:
    python tests/load_test.py --populate     # Populate historical data only
    python tests/load_test.py --load         # Run load test only
    python tests/load_test.py --all          # Both populate and load test

Requirements:
    pip install httpx asyncio
"""
import argparse
import asyncio
import random
import time
import statistics
import sys
import os
from datetime import datetime, timezone, timedelta
from typing import Optional
from dataclasses import dataclass, field

import httpx

# Add project root to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from src.api.database import get_db_session, BucketHistory, is_database_configured
from src.api.grid import latlon_to_cell

# Configuration
API_BASE_URL = "http://localhost:8000"
DEFAULT_CONCURRENT_USERS = 50
DEFAULT_REQUESTS_PER_USER = 20
DEFAULT_HISTORY_DAYS = 14
DEFAULT_CELLS_COUNT = 25

# San Francisco area coordinates for realistic test data
SF_LAT_CENTER = 37.7749
SF_LNG_CENTER = -122.4194
SF_LAT_SPREAD = 0.05  # ~5km spread
SF_LNG_SPREAD = 0.05


@dataclass
class LoadTestResult:
    """Results from a load test run."""
    total_requests: int = 0
    successful_requests: int = 0
    failed_requests: int = 0
    response_times: list = field(default_factory=list)
    errors: list = field(default_factory=list)
    start_time: float = 0
    end_time: float = 0

    @property
    def duration(self) -> float:
        return self.end_time - self.start_time

    @property
    def requests_per_second(self) -> float:
        return self.total_requests / self.duration if self.duration > 0 else 0

    @property
    def avg_response_time(self) -> float:
        return statistics.mean(self.response_times) if self.response_times else 0

    @property
    def p50_response_time(self) -> float:
        if not self.response_times:
            return 0
        sorted_times = sorted(self.response_times)
        idx = len(sorted_times) // 2
        return sorted_times[idx]

    @property
    def p95_response_time(self) -> float:
        if not self.response_times:
            return 0
        sorted_times = sorted(self.response_times)
        idx = int(len(sorted_times) * 0.95)
        return sorted_times[min(idx, len(sorted_times) - 1)]

    @property
    def p99_response_time(self) -> float:
        if not self.response_times:
            return 0
        sorted_times = sorted(self.response_times)
        idx = int(len(sorted_times) * 0.99)
        return sorted_times[min(idx, len(sorted_times) - 1)]

    @property
    def success_rate(self) -> float:
        return (self.successful_requests / self.total_requests * 100) if self.total_requests > 0 else 0


def generate_random_location() -> tuple[float, float]:
    """Generate a random location in the SF area."""
    lat = SF_LAT_CENTER + random.uniform(-SF_LAT_SPREAD, SF_LAT_SPREAD)
    lng = SF_LNG_CENTER + random.uniform(-SF_LNG_SPREAD, SF_LNG_SPREAD)
    return lat, lng


def generate_realistic_speed(hour: int, is_weekend: bool) -> Optional[float]:
    """
    Generate a realistic speed based on time of day and day of week.

    Rush hours (7-9 AM, 5-7 PM on weekdays) have slower speeds.
    Night hours have faster speeds.
    Some readings have no speed (parked/stationary vehicles).
    """
    # 10% chance of no speed data (parked vehicle)
    if random.random() < 0.10:
        return None

    # Base speed range
    if is_weekend:
        # Weekends: generally lighter traffic
        base_speed = random.gauss(45, 15)
    else:
        # Weekdays: depends on hour
        if 7 <= hour <= 9 or 17 <= hour <= 19:
            # Rush hour: slower
            base_speed = random.gauss(25, 10)
        elif 10 <= hour <= 16:
            # Daytime: moderate
            base_speed = random.gauss(40, 12)
        elif 22 <= hour or hour <= 5:
            # Night: faster
            base_speed = random.gauss(55, 10)
        else:
            # Other times: normal
            base_speed = random.gauss(45, 12)

    # Clamp to realistic range
    return max(5, min(80, base_speed))


def generate_realistic_count(hour: int, is_weekend: bool) -> int:
    """
    Generate a realistic vehicle count based on time of day.

    Rush hours have higher counts.
    Night hours have lower counts.
    """
    if is_weekend:
        # Weekends: moderate, peaks around midday
        if 10 <= hour <= 18:
            base_count = random.gauss(20, 8)
        else:
            base_count = random.gauss(10, 5)
    else:
        # Weekdays
        if 7 <= hour <= 9 or 17 <= hour <= 19:
            # Rush hour: high counts
            base_count = random.gauss(35, 12)
        elif 10 <= hour <= 16:
            # Daytime: moderate
            base_count = random.gauss(20, 8)
        elif 22 <= hour or hour <= 5:
            # Night: low
            base_count = random.gauss(5, 3)
        else:
            # Other times
            base_count = random.gauss(15, 6)

    return max(1, int(base_count))


def populate_historical_data(
    days: int = DEFAULT_HISTORY_DAYS,
    cells_count: int = DEFAULT_CELLS_COUNT,
    verbose: bool = True
) -> int:
    """
    Populate the bucket_history table with realistic historical data.

    Uses direct database inserts for efficiency (not the API).

    Args:
        days: Number of days of history to generate
        cells_count: Number of unique cells to generate data for
        verbose: Print progress updates

    Returns:
        Number of records created
    """
    if verbose:
        print(f"\n{'='*60}")
        print("POPULATING HISTORICAL DATA")
        print(f"{'='*60}")
        print(f"Days of history: {days}")
        print(f"Number of cells: {cells_count}")

    if not is_database_configured():
        print("ERROR: Database is not configured. Check DATABASE_URL.")
        return 0

    # Generate fixed cell locations for consistency
    cell_locations = [(generate_random_location(), latlon_to_cell(*generate_random_location()))
                      for _ in range(cells_count)]

    # Pre-generate cell_ids to ensure consistency
    cell_data = []
    for _ in range(cells_count):
        lat, lng = generate_random_location()
        cell_id = latlon_to_cell(lat, lng)
        cell_data.append((lat, lng, cell_id))

    # Calculate total buckets (5-minute intervals)
    buckets_per_day = 24 * 12  # 288 buckets per day
    total_buckets = days * buckets_per_day * cells_count

    if verbose:
        print(f"Total records to create: {total_buckets:,}")
        print(f"Starting population...")

    records_created = 0
    start_time = time.time()

    session = get_db_session()
    if session is None:
        print("ERROR: Could not get database session.")
        return 0

    try:
        batch = []
        batch_size = 500  # Commit every 500 records

        for day_offset in range(days, 0, -1):
            date = datetime.now(timezone.utc) - timedelta(days=day_offset)
            is_weekend = date.weekday() >= 5

            for hour in range(24):
                for bucket_in_hour in range(12):  # 5-minute buckets
                    minute = bucket_in_hour * 5
                    bucket_time = date.replace(hour=hour, minute=minute, second=0, microsecond=0)

                    for lat, lng, cell_id in cell_data:
                        # Generate realistic data
                        count = generate_realistic_count(hour, is_weekend)
                        speed = generate_realistic_speed(hour, is_weekend)

                        record = BucketHistory(
                            cell_id=cell_id,
                            bucket_time=bucket_time,
                            vehicle_count=count,
                            avg_speed=speed,
                            hour_of_day=hour,
                            day_of_week=date.weekday()
                        )
                        batch.append(record)

                        # Commit batch when full
                        if len(batch) >= batch_size:
                            try:
                                session.add_all(batch)
                                session.commit()
                                records_created += len(batch)
                            except Exception as e:
                                session.rollback()
                                if verbose:
                                    print(f"  Batch error (likely duplicates): {str(e)[:50]}")
                            batch = []

                            if verbose and records_created % 5000 == 0:
                                elapsed = time.time() - start_time
                                rate = records_created / elapsed if elapsed > 0 else 0
                                print(f"  Progress: {records_created:,} records ({rate:.1f}/sec)")

        # Commit remaining batch
        if batch:
            try:
                session.add_all(batch)
                session.commit()
                records_created += len(batch)
            except Exception as e:
                session.rollback()
                if verbose:
                    print(f"  Final batch error: {str(e)[:50]}")

    finally:
        session.close()

    elapsed = time.time() - start_time

    if verbose:
        print(f"\nCompleted in {elapsed:.1f} seconds")
        print(f"Records created: {records_created:,}")
        print(f"Rate: {records_created / elapsed:.1f} records/second")

    return records_created


async def send_ping(
    client: httpx.AsyncClient,
    device_id: str,
    result: LoadTestResult
) -> None:
    """Send a single ping request and record the result."""
    lat, lng = generate_random_location()
    speed = generate_realistic_speed(datetime.now().hour, datetime.now().weekday() >= 5)

    payload = {
        "device_id": device_id,
        "lat": lat,
        "lon": lng,
        "timestamp": datetime.now(timezone.utc).isoformat()
    }
    if speed is not None:
        payload["speed_kmh"] = speed

    start = time.perf_counter()
    try:
        response = await client.post(f"{API_BASE_URL}/v1/pings", json=payload)
        elapsed = (time.perf_counter() - start) * 1000  # Convert to ms

        result.response_times.append(elapsed)
        result.total_requests += 1

        if response.status_code == 200:
            result.successful_requests += 1
        else:
            result.failed_requests += 1
            result.errors.append(f"HTTP {response.status_code}: {response.text[:100]}")
    except Exception as e:
        elapsed = (time.perf_counter() - start) * 1000
        result.response_times.append(elapsed)
        result.total_requests += 1
        result.failed_requests += 1
        result.errors.append(str(e))


async def send_congestion_query(
    client: httpx.AsyncClient,
    result: LoadTestResult
) -> None:
    """Send a congestion query and record the result."""
    lat, lng = generate_random_location()

    start = time.perf_counter()
    try:
        response = await client.get(
            f"{API_BASE_URL}/v1/congestion",
            params={"lat": lat, "lon": lng}
        )
        elapsed = (time.perf_counter() - start) * 1000

        result.response_times.append(elapsed)
        result.total_requests += 1

        if response.status_code == 200:
            result.successful_requests += 1
        else:
            result.failed_requests += 1
            result.errors.append(f"HTTP {response.status_code}: {response.text[:100]}")
    except Exception as e:
        elapsed = (time.perf_counter() - start) * 1000
        result.response_times.append(elapsed)
        result.total_requests += 1
        result.failed_requests += 1
        result.errors.append(str(e))


async def simulate_user(
    user_id: int,
    requests_per_user: int,
    result: LoadTestResult
) -> None:
    """Simulate a single user making requests."""
    device_id = f"load_test_device_{user_id}"

    async with httpx.AsyncClient(timeout=30.0) as client:
        for _ in range(requests_per_user):
            # Mix of pings (70%) and congestion queries (30%)
            if random.random() < 0.7:
                await send_ping(client, device_id, result)
            else:
                await send_congestion_query(client, result)

            # Small delay between requests (simulate real user behavior)
            await asyncio.sleep(random.uniform(0.01, 0.05))


async def run_load_test(
    concurrent_users: int = DEFAULT_CONCURRENT_USERS,
    requests_per_user: int = DEFAULT_REQUESTS_PER_USER,
    verbose: bool = True
) -> LoadTestResult:
    """
    Run a load test against the API.

    Args:
        concurrent_users: Number of concurrent simulated users
        requests_per_user: Number of requests each user makes
        verbose: Print progress updates

    Returns:
        LoadTestResult with timing and success metrics
    """
    if verbose:
        print(f"\n{'='*60}")
        print("RUNNING LOAD TEST")
        print(f"{'='*60}")
        print(f"Concurrent users: {concurrent_users}")
        print(f"Requests per user: {requests_per_user}")
        print(f"Total requests: {concurrent_users * requests_per_user:,}")
        print(f"Starting load test...")

    result = LoadTestResult()
    result.start_time = time.time()

    # Create tasks for all users
    tasks = [
        simulate_user(user_id, requests_per_user, result)
        for user_id in range(concurrent_users)
    ]

    # Run all users concurrently
    await asyncio.gather(*tasks)

    result.end_time = time.time()

    if verbose:
        print_results(result)

    return result


def print_results(result: LoadTestResult) -> None:
    """Print formatted load test results."""
    print(f"\n{'='*60}")
    print("LOAD TEST RESULTS")
    print(f"{'='*60}")
    print(f"Duration: {result.duration:.2f} seconds")
    print(f"Total requests: {result.total_requests:,}")
    print(f"Successful: {result.successful_requests:,}")
    print(f"Failed: {result.failed_requests:,}")
    print(f"Success rate: {result.success_rate:.1f}%")
    print(f"\nThroughput: {result.requests_per_second:.1f} requests/second")
    print(f"\nResponse Times (ms):")
    print(f"  Average: {result.avg_response_time:.1f}")
    print(f"  P50 (median): {result.p50_response_time:.1f}")
    print(f"  P95: {result.p95_response_time:.1f}")
    print(f"  P99: {result.p99_response_time:.1f}")

    if result.errors:
        unique_errors = list(set(result.errors))[:5]  # Show first 5 unique errors
        print(f"\nSample errors ({len(result.errors)} total):")
        for error in unique_errors:
            print(f"  - {error[:80]}")

    print(f"{'='*60}\n")


async def health_check() -> bool:
    """Check if the API is running."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(f"{API_BASE_URL}/health")
            return response.status_code == 200
    except Exception:
        return False


async def main():
    global API_BASE_URL

    parser = argparse.ArgumentParser(description="Load test for Traffic Congestion API")
    parser.add_argument("--populate", action="store_true", help="Populate historical data")
    parser.add_argument("--load", action="store_true", help="Run load test")
    parser.add_argument("--all", action="store_true", help="Both populate and load test")
    parser.add_argument("--days", type=int, default=DEFAULT_HISTORY_DAYS, help="Days of history to generate")
    parser.add_argument("--cells", type=int, default=DEFAULT_CELLS_COUNT, help="Number of cells to generate")
    parser.add_argument("--users", type=int, default=DEFAULT_CONCURRENT_USERS, help="Concurrent users for load test")
    parser.add_argument("--requests", type=int, default=DEFAULT_REQUESTS_PER_USER, help="Requests per user")
    parser.add_argument("--url", type=str, default=API_BASE_URL, help="API base URL")

    args = parser.parse_args()
    API_BASE_URL = args.url

    # Default to --all if no action specified
    if not (args.populate or args.load or args.all):
        args.all = True

    print(f"\n{'#'*60}")
    print("# TRAFFIC CONGESTION API - LOAD TEST SUITE")
    print(f"# Target: {API_BASE_URL}")
    print(f"{'#'*60}")

    # Populate historical data (direct DB, doesn't need API)
    if args.populate or args.all:
        populate_historical_data(
            days=args.days,
            cells_count=args.cells
        )

    # Run load test (needs API running)
    if args.load or args.all:
        print("\nChecking API health...")
        if not await health_check():
            print("ERROR: API is not responding. Make sure the server is running:")
            print("  uvicorn src.api.main:app --reload")
            return
        print("API is healthy!")

        await run_load_test(
            concurrent_users=args.users,
            requests_per_user=args.requests
        )

    print("Load test complete!")


if __name__ == "__main__":
    asyncio.run(main())
