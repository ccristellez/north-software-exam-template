"""
Tests for Redis Stream event publishing.
"""
import pytest
from unittest.mock import Mock
from src.api.events import (
    publish_ping_event,
    publish_high_congestion_alert,
    read_events,
    get_stream_length,
    STREAM_NAME,
    MAX_STREAM_LENGTH
)


@pytest.fixture
def mock_redis():
    """Create a mock Redis client."""
    return Mock()


@pytest.mark.unit
class TestPublishPingEvent:
    """Tests for publish_ping_event function."""

    def test_publish_ping_event_returns_event_id(self, mock_redis):
        """Test that publishing returns an event ID."""
        mock_redis.xadd.return_value = "1234567890123-0"

        event_id = publish_ping_event(
            redis_client=mock_redis,
            device_id="car_001",
            cell_id="882a100d63fffff",
            lat=40.758,
            lon=-73.9855,
            bucket=12345,
            vehicle_count=5
        )

        assert event_id == "1234567890123-0"

    def test_publish_ping_event_calls_xadd(self, mock_redis):
        """Test that XADD is called with correct parameters."""
        mock_redis.xadd.return_value = "1234567890123-0"

        publish_ping_event(
            redis_client=mock_redis,
            device_id="car_001",
            cell_id="882a100d63fffff",
            lat=40.758,
            lon=-73.9855,
            bucket=12345,
            vehicle_count=5
        )

        # Check XADD was called
        mock_redis.xadd.assert_called_once()

        # Get the call arguments
        call_args = mock_redis.xadd.call_args
        stream_name = call_args[0][0]
        event_data = call_args[0][1]

        # Verify stream name
        assert stream_name == STREAM_NAME

        # Verify event data contains expected fields
        assert event_data["event_type"] == "ping_received"
        assert event_data["device_id"] == "car_001"
        assert event_data["cell_id"] == "882a100d63fffff"
        assert event_data["lat"] == "40.758"
        assert event_data["lon"] == "-73.9855"
        assert event_data["bucket"] == "12345"
        assert event_data["vehicle_count"] == "5"
        assert "timestamp" in event_data

    def test_publish_ping_event_sets_maxlen(self, mock_redis):
        """Test that XADD is called with MAXLEN to prevent unbounded growth."""
        mock_redis.xadd.return_value = "1234567890123-0"

        publish_ping_event(
            redis_client=mock_redis,
            device_id="car_001",
            cell_id="882a100d63fffff",
            lat=40.758,
            lon=-73.9855,
            bucket=12345,
            vehicle_count=5
        )

        # Check MAXLEN parameter
        call_kwargs = mock_redis.xadd.call_args[1]
        assert call_kwargs["maxlen"] == MAX_STREAM_LENGTH
        assert call_kwargs["approximate"] is True


@pytest.mark.unit
class TestPublishHighCongestionAlert:
    """Tests for publish_high_congestion_alert function."""

    def test_publish_alert_returns_event_id(self, mock_redis):
        """Test that publishing alert returns an event ID."""
        mock_redis.xadd.return_value = "1234567890123-1"

        event_id = publish_high_congestion_alert(
            redis_client=mock_redis,
            cell_id="882a100d63fffff",
            vehicle_count=35,
            lat=40.758,
            lon=-73.9855
        )

        assert event_id == "1234567890123-1"

    def test_publish_alert_has_correct_event_type(self, mock_redis):
        """Test that alert has event_type=high_congestion."""
        mock_redis.xadd.return_value = "1234567890123-1"

        publish_high_congestion_alert(
            redis_client=mock_redis,
            cell_id="882a100d63fffff",
            vehicle_count=35,
            lat=40.758,
            lon=-73.9855
        )

        call_args = mock_redis.xadd.call_args
        event_data = call_args[0][1]

        assert event_data["event_type"] == "high_congestion"
        assert event_data["vehicle_count"] == "35"


@pytest.mark.unit
class TestReadEvents:
    """Tests for read_events function."""

    def test_read_events_returns_empty_list_when_no_events(self, mock_redis):
        """Test that empty result is handled correctly."""
        mock_redis.xread.return_value = []

        events = read_events(mock_redis, last_id="0", count=10)

        assert events == []

    def test_read_events_returns_events(self, mock_redis):
        """Test that events are returned correctly."""
        # Simulate Redis XREAD response format
        mock_redis.xread.return_value = [
            (STREAM_NAME, [
                ("1234567890123-0", {"event_type": "ping_received", "device_id": "car_001"}),
                ("1234567890123-1", {"event_type": "ping_received", "device_id": "car_002"}),
            ])
        ]

        events = read_events(mock_redis, last_id="0", count=10)

        assert len(events) == 2
        assert events[0][0] == "1234567890123-0"
        assert events[0][1]["device_id"] == "car_001"
        assert events[1][0] == "1234567890123-1"
        assert events[1][1]["device_id"] == "car_002"

    def test_read_events_with_blocking(self, mock_redis):
        """Test blocking read passes block parameter."""
        mock_redis.xread.return_value = []

        read_events(mock_redis, last_id="$", count=10, block_ms=1000)

        call_kwargs = mock_redis.xread.call_args[1]
        assert call_kwargs["block"] == 1000


@pytest.mark.unit
class TestGetStreamLength:
    """Tests for get_stream_length function."""

    def test_get_stream_length(self, mock_redis):
        """Test getting stream length."""
        mock_redis.xlen.return_value = 42

        length = get_stream_length(mock_redis)

        assert length == 42
        mock_redis.xlen.assert_called_once_with(STREAM_NAME)

    def test_get_stream_length_empty(self, mock_redis):
        """Test getting length of empty stream."""
        mock_redis.xlen.return_value = 0

        length = get_stream_length(mock_redis)

        assert length == 0
