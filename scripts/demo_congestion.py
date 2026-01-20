"""
Demo script to show congestion detection with speed data.

This script sends pings with varying speeds to demonstrate:
1. How speed + count determines congestion level
2. How the percentile-based system compares to historical data
3. How cells become calibrated after enough samples (20+)

Run the event_consumer.py in another terminal to see the events:
    Terminal 1: python scripts/event_consumer.py
    Terminal 2: python scripts/demo_congestion.py

Usage:
    python scripts/demo_congestion.py
    python scripts/demo_congestion.py --slow    # Simulate heavy traffic
    python scripts/demo_congestion.py --fast    # Simulate free-flowing traffic
"""
import requests
import time
import random
import argparse

# Times Square, NYC - a single location to concentrate traffic
DEMO_LOCATION = {
    "lat": 40.758,
    "lon": -73.9855
}

API_URL = "http://localhost:8000"


def main():
    parser = argparse.ArgumentParser(description="Demo congestion detection")
    parser.add_argument("--slow", action="store_true", help="Simulate heavy traffic (5-15 km/h)")
    parser.add_argument("--fast", action="store_true", help="Simulate free-flowing traffic (50-70 km/h)")
    parser.add_argument("--count", type=int, default=35, help="Number of pings to send (default: 35)")
    args = parser.parse_args()

    # Determine speed range based on mode
    if args.slow:
        speed_range = (5, 15)
        mode_name = "HEAVY TRAFFIC (crawling)"
    elif args.fast:
        speed_range = (50, 70)
        mode_name = "FREE FLOW (moving fast)"
    else:
        speed_range = (20, 40)
        mode_name = "MODERATE TRAFFIC (mixed speeds)"

    print("=" * 60)
    print("CONGESTION DEMO - Percentile-Based Detection")
    print("=" * 60)
    print()
    print(f"Location: Times Square, NYC")
    print(f"Mode:     {mode_name}")
    print(f"Pings:    {args.count}")
    print(f"Speed:    {speed_range[0]}-{speed_range[1]} km/h")
    print()
    print("The system uses SPEED compared to historical percentiles:")
    print("  - Speed < 25th percentile = HIGH congestion")
    print("  - Speed < 50th percentile = MODERATE congestion")
    print("  - Speed >= 50th percentile = LOW congestion")
    print("  - Falls back to absolute thresholds if < 20 samples")
    print()
    print("-" * 60)

    # Check API is running
    try:
        response = requests.get(f"{API_URL}/health", timeout=5)
        if response.status_code != 200:
            print("ERROR: API not healthy")
            return
        print("API is running")
    except requests.ConnectionError:
        print("ERROR: Cannot connect to API at", API_URL)
        print("Make sure to run: uvicorn src.api.main:app --reload")
        return

    # Send pings with speed data
    print()
    print("Sending pings with speed data...")
    print()

    for i in range(1, args.count + 1):
        device_id = f"car_{i:03d}"
        speed = round(random.uniform(*speed_range), 1)

        response = requests.post(
            f"{API_URL}/v1/pings",
            json={
                "device_id": device_id,
                "lat": DEMO_LOCATION["lat"],
                "lon": DEMO_LOCATION["lon"],
                "speed_kmh": speed
            }
        )

        data = response.json()
        count = data["bucket_count"]

        # Show progress with speed
        bar = "#" * min(count, 40)
        print(f"  {device_id}: count={count:2d}, speed={speed:4.1f} km/h  {bar}")

        # Small delay so consumer can keep up
        time.sleep(0.05)

    print()
    print("-" * 60)

    # Query congestion with debug info
    response = requests.get(
        f"{API_URL}/v1/congestion",
        params={**DEMO_LOCATION, "debug": "true"}
    )
    data = response.json()

    print()
    print("CONGESTION STATUS:")
    print(f"  Cell ID:       {data['cell_id']}")
    print(f"  Vehicle Count: {data['vehicle_count']}")
    print(f"  Avg Speed:     {data.get('avg_speed_kmh', 'N/A')} km/h")
    print(f"  Level:         {data['congestion_level']}")
    print(f"  Calibrated:    {data.get('calibrated', False)}")
    print()

    # Show debug info if available
    if "debug" in data:
        debug = data["debug"]
        print("PERCENTILE DEBUG:")
        print(f"  Method:        {debug.get('method', 'N/A')}")
        print(f"  Sample Count:  {debug.get('sample_count', 0)}")
        if debug.get("method") == "percentile":
            print(f"  Speed p25:     {debug.get('speed_p25', 'N/A')} km/h")
            print(f"  Speed p50:     {debug.get('speed_p50', 'N/A')} km/h")
            print(f"  Count p75:     {debug.get('count_p75', 'N/A')}")
        print(f"  Reason:        {debug.get('level_reason', 'N/A')}")
    print()

    # Show historical percentiles
    print("-" * 60)
    response = requests.get(f"{API_URL}/v1/history", params=DEMO_LOCATION)

    if response.status_code == 200:
        history = response.json()
        print("HISTORICAL PERCENTILES:")
        print(f"  Speed p25:     {history.get('speed_p25', 'N/A')} km/h (slow)")
        print(f"  Speed p50:     {history.get('speed_p50', 'N/A')} km/h (median)")
        print(f"  Count p75:     {history.get('count_p75', 'N/A')} (busy)")
        print(f"  Samples:       {history.get('sample_count', 0)}")
        print(f"  Calibrated:    {history.get('is_calibrated', False)} (needs 20)")
    else:
        print("HISTORICAL PERCENTILES: Not available (no database configured)")
    print()

    # Tips for building up history
    print("-" * 60)
    print("TIP: To build historical data for percentile calibration:")
    print()
    print("  python tests/load_test.py --populate --days 7 --cells 5")
    print()
    print("This will insert realistic traffic data directly into the database.")
    print("After 20+ samples, the cell becomes 'calibrated' and uses percentiles.")
    print("=" * 60)


if __name__ == "__main__":
    main()
