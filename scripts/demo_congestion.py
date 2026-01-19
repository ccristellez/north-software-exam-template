"""
Demo script to trigger HIGH congestion and show event flow.

This script sends 35 pings from different devices to the same location,
which triggers HIGH congestion (threshold is 30) and generates alerts.

Run the event_consumer.py in another terminal to see the events:
    Terminal 1: python scripts/event_consumer.py
    Terminal 2: python scripts/demo_congestion.py

Usage:
    python scripts/demo_congestion.py
"""
import requests
import time

# Times Square, NYC - a single location to concentrate traffic
DEMO_LOCATION = {
    "lat": 40.758,
    "lon": -73.9855
}

API_URL = "http://localhost:8000"


def main():
    print("=" * 50)
    print("CONGESTION DEMO")
    print("=" * 50)
    print()
    print("This will send 35 pings to Times Square, NYC")
    print("to trigger HIGH congestion (threshold = 30 vehicles)")
    print()
    print("Tip: Run 'python scripts/event_consumer.py' in another")
    print("terminal to watch the events stream in real-time!")
    print()
    print("-" * 50)

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

    # Send pings one by one so you can see them in the consumer
    print()
    print("Sending pings...")

    for i in range(1, 36):
        device_id = f"car_{i:03d}"

        response = requests.post(
            f"{API_URL}/v1/pings",
            json={
                "device_id": device_id,
                "lat": DEMO_LOCATION["lat"],
                "lon": DEMO_LOCATION["lon"]
            }
        )

        data = response.json()
        count = data["bucket_count"]

        # Show progress
        if count < 10:
            level = "LOW"
        elif count < 30:
            level = "MODERATE"
        else:
            level = "HIGH"

        bar = "#" * min(count, 40)
        print(f"  {device_id}: count={count:2d} [{level:8s}] {bar}")

        # Small delay so consumer can keep up visually
        time.sleep(0.1)

    print()
    print("-" * 50)

    # Query final congestion
    response = requests.get(
        f"{API_URL}/v1/congestion",
        params=DEMO_LOCATION
    )
    data = response.json()

    print("FINAL CONGESTION STATUS:")
    print(f"  Location: Times Square, NYC")
    print(f"  Cell ID:  {data['cell_id']}")
    print(f"  Vehicles: {data['vehicle_count']}")
    print(f"  Level:    {data['congestion_level']}")
    print()

    if data["congestion_level"] == "HIGH":
        print("HIGH CONGESTION triggered - check the event consumer")
        print("for the high_congestion alert!")

    print("=" * 50)


if __name__ == "__main__":
    main()
