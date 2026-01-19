"""
Demo script to show congestion detection with speed data.

This script sends pings with varying speeds to demonstrate:
1. How speed + count determines congestion level
2. How the Z-score system works with historical baselines
3. How to trigger baseline updates for calibration

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
    print("CONGESTION DEMO - With Speed Data")
    print("=" * 60)
    print()
    print(f"Location: Times Square, NYC")
    print(f"Mode:     {mode_name}")
    print(f"Pings:    {args.count}")
    print(f"Speed:    {speed_range[0]}-{speed_range[1]} km/h")
    print()
    print("The system uses both COUNT and SPEED to determine congestion:")
    print("  - High count + slow speed = HIGH congestion")
    print("  - Low count + fast speed = LOW congestion")
    print("  - Z-scores compare current vs historical baseline")
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
    print(f"  Cell ID:      {data['cell_id']}")
    print(f"  Vehicle Count: {data['vehicle_count']}")
    print(f"  Avg Speed:     {data['avg_speed_kmh']} km/h")
    print(f"  Level:         {data['congestion_level']}")
    print(f"  Calibrated:    {data['calibrated']}")
    print()

    # Show debug info if available
    if "debug" in data:
        debug = data["debug"]
        print("Z-SCORE DEBUG:")
        print(f"  Method:        {debug.get('method', 'N/A')}")
        print(f"  Sample Count:  {debug.get('sample_count', 0)}")
        if debug.get("method") == "calibrated":
            print(f"  Count Z-score: {debug.get('count_z', 'N/A')}")
            print(f"  Speed Z-score: {debug.get('speed_z', 'N/A')}")
            print(f"  Combined Z:    {debug.get('combined_z', 'N/A')}")
        print(f"  Reason:        {debug.get('level_reason', 'N/A')}")
    print()

    # Show baseline info
    print("-" * 60)
    response = requests.get(f"{API_URL}/v1/baseline", params=DEMO_LOCATION)
    baseline = response.json()

    print("BASELINE (Historical Data):")
    print(f"  Avg Speed:     {baseline['avg_speed_kmh']} km/h")
    print(f"  Avg Count:     {baseline['avg_count']}")
    print(f"  Speed Std:     {baseline['speed_std']}")
    print(f"  Count Std:     {baseline['count_std']}")
    print(f"  Samples:       {baseline['sample_count']}")
    print(f"  Calibrated:    {baseline['is_calibrated']} (needs {baseline['min_samples_required']})")
    print()

    # Offer to update baseline
    print("-" * 60)
    print("TIP: To build up the baseline, run this command:")
    print(f"  curl -X POST '{API_URL}/v1/baseline/update?lat={DEMO_LOCATION['lat']}&lon={DEMO_LOCATION['lon']}'")
    print()
    print("Or run the load test to generate lots of data:")
    print("  python scripts/load_test.py --requests 500 --traffic moderate")
    print("=" * 60)


if __name__ == "__main__":
    main()
