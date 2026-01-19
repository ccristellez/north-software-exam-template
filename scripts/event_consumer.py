"""
Event Consumer - Listens to Redis Stream and processes events.

This script demonstrates event-driven architecture by:
1. Connecting to the Redis Stream
2. Listening for new events in real-time
3. Processing events (printing alerts for high congestion)

Run this in a separate terminal while sending pings to see events flow through.

Usage:
    python scripts/event_consumer.py

The consumer will print events as they arrive. Press Ctrl+C to stop.
"""
import redis
import os
import sys
from datetime import datetime

# Add project root to path so we can import our modules
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.api.events import STREAM_NAME, read_events, get_stream_length


def format_timestamp(iso_string: str) -> str:
    """Convert ISO timestamp to readable format."""
    try:
        dt = datetime.fromisoformat(iso_string.replace('Z', '+00:00'))
        return dt.strftime("%H:%M:%S")
    except:
        return iso_string


def print_event(event_id: str, event_data: dict):
    """Print an event in a readable format."""
    event_type = event_data.get("event_type", "unknown")
    timestamp = format_timestamp(event_data.get("timestamp", ""))

    if event_type == "ping_received":
        device = event_data.get("device_id", "?")
        cell = event_data.get("cell_id", "?")[:12] + "..."  # Truncate long cell ID
        count = event_data.get("vehicle_count", "?")
        print(f"  [{timestamp}] PING: device={device}, cell={cell}, count={count}")

    elif event_type == "high_congestion":
        cell = event_data.get("cell_id", "?")[:12] + "..."
        count = event_data.get("vehicle_count", "?")
        lat = event_data.get("lat", "?")
        lon = event_data.get("lon", "?")
        # Print alert in red/bold for visibility
        print(f"  [{timestamp}] ⚠️  HIGH CONGESTION ALERT!")
        print(f"              Cell: {cell}")
        print(f"              Vehicles: {count}")
        print(f"              Location: ({lat}, {lon})")
        print()

    else:
        print(f"  [{timestamp}] {event_type}: {event_data}")


def main():
    """Main consumer loop."""
    # Connect to Redis
    redis_host = os.getenv("REDIS_HOST", "127.0.0.1")
    redis_port = int(os.getenv("REDIS_PORT", "6379"))

    print("=" * 60)
    print("CONGESTION MONITOR - Event Consumer")
    print("=" * 60)
    print(f"Connecting to Redis at {redis_host}:{redis_port}...")

    try:
        r = redis.Redis(host=redis_host, port=redis_port, decode_responses=True)
        r.ping()
        print("Connected!")
    except redis.ConnectionError:
        print("ERROR: Could not connect to Redis.")
        print("Make sure Redis is running: docker-compose up -d")
        sys.exit(1)

    # Check current stream length
    stream_length = get_stream_length(r)
    print(f"Stream '{STREAM_NAME}' has {stream_length} events")
    print()
    print("Listening for new events... (press Ctrl+C to stop)")
    print("-" * 60)

    # Start reading from the end of the stream (only new events)
    # Use "$" to start from now, or "0" to read all historical events
    last_id = "$"

    try:
        while True:
            # Block for 1 second waiting for new events
            # This is more efficient than polling
            events_list = read_events(r, last_id=last_id, count=10, block_ms=1000)

            for event_id, event_data in events_list:
                print_event(event_id, event_data)
                last_id = event_id  # Update position in stream

    except KeyboardInterrupt:
        print()
        print("-" * 60)
        print("Consumer stopped.")

        # Print final stats
        final_length = get_stream_length(r)
        print(f"Final stream length: {final_length} events")


if __name__ == "__main__":
    main()
