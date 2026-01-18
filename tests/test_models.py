"""
Unit tests for Pydantic models.
"""
import pytest
from datetime import datetime, timezone
from pydantic import ValidationError
from src.api.models import Ping


@pytest.mark.unit
class TestPingModel:
    """Test suite for Ping model."""

    def test_ping_valid_data(self):
        """Test creating Ping with valid data."""
        ping = Ping(
            device_id="device123",
            lat=40.7128,
            lon=-74.0060
        )

        assert ping.device_id == "device123"
        assert ping.lat == 40.7128
        assert ping.lon == -74.0060
        assert ping.timestamp is None

    def test_ping_with_timestamp(self):
        """Test creating Ping with timestamp."""
        ts = datetime(2024, 1, 15, 10, 0, 0, tzinfo=timezone.utc)
        ping = Ping(
            device_id="device123",
            lat=40.7128,
            lon=-74.0060,
            timestamp=ts
        )

        assert ping.timestamp == ts

    def test_ping_missing_device_id(self):
        """Test that device_id is required."""
        with pytest.raises(ValidationError) as exc_info:
            Ping(lat=40.7128, lon=-74.0060)

        errors = exc_info.value.errors()
        assert any(error["loc"] == ("device_id",) for error in errors)

    def test_ping_empty_device_id(self):
        """Test that device_id cannot be empty string."""
        with pytest.raises(ValidationError) as exc_info:
            Ping(device_id="", lat=40.7128, lon=-74.0060)

        errors = exc_info.value.errors()
        assert any(
            error["loc"] == ("device_id",) and "at least 1" in str(error["msg"])
            for error in errors
        )

    def test_ping_missing_lat(self):
        """Test that lat is required."""
        with pytest.raises(ValidationError) as exc_info:
            Ping(device_id="device123", lon=-74.0060)

        errors = exc_info.value.errors()
        assert any(error["loc"] == ("lat",) for error in errors)

    def test_ping_missing_lon(self):
        """Test that lon is required."""
        with pytest.raises(ValidationError) as exc_info:
            Ping(device_id="device123", lat=40.7128)

        errors = exc_info.value.errors()
        assert any(error["loc"] == ("lon",) for error in errors)

    def test_ping_invalid_lat_type(self):
        """Test that lat must be a number."""
        with pytest.raises(ValidationError) as exc_info:
            Ping(device_id="device123", lat="invalid", lon=-74.0060)

        errors = exc_info.value.errors()
        assert any(error["loc"] == ("lat",) for error in errors)

    def test_ping_invalid_lon_type(self):
        """Test that lon must be a number."""
        with pytest.raises(ValidationError) as exc_info:
            Ping(device_id="device123", lat=40.7128, lon="invalid")

        errors = exc_info.value.errors()
        assert any(error["loc"] == ("lon",) for error in errors)

    def test_ping_extreme_latitudes(self):
        """Test Ping with extreme latitude values."""
        # Valid extreme latitudes
        ping_north = Ping(device_id="device123", lat=90.0, lon=0.0)
        assert ping_north.lat == 90.0

        ping_south = Ping(device_id="device123", lat=-90.0, lon=0.0)
        assert ping_south.lat == -90.0

    def test_ping_extreme_longitudes(self):
        """Test Ping with extreme longitude values."""
        # Valid extreme longitudes
        ping_east = Ping(device_id="device123", lat=0.0, lon=180.0)
        assert ping_east.lon == 180.0

        ping_west = Ping(device_id="device123", lat=0.0, lon=-180.0)
        assert ping_west.lon == -180.0

    def test_ping_timestamp_none_by_default(self):
        """Test that timestamp defaults to None."""
        ping = Ping(device_id="device123", lat=40.7128, lon=-74.0060)
        assert ping.timestamp is None

    def test_ping_from_dict(self):
        """Test creating Ping from dictionary."""
        data = {
            "device_id": "device456",
            "lat": 51.5074,
            "lon": -0.1278
        }
        ping = Ping(**data)

        assert ping.device_id == "device456"
        assert ping.lat == 51.5074
        assert ping.lon == -0.1278

    def test_ping_to_dict(self):
        """Test converting Ping to dictionary."""
        ping = Ping(device_id="device123", lat=40.7128, lon=-74.0060)
        data = ping.model_dump()

        assert isinstance(data, dict)
        assert data["device_id"] == "device123"
        assert data["lat"] == 40.7128
        assert data["lon"] == -74.0060
        assert data["timestamp"] is None

    def test_ping_with_naive_datetime(self):
        """Test Ping with naive datetime (no timezone)."""
        ts = datetime(2024, 1, 15, 10, 0, 0)
        ping = Ping(
            device_id="device123",
            lat=40.7128,
            lon=-74.0060,
            timestamp=ts
        )

        assert ping.timestamp == ts

    def test_ping_numeric_device_id(self):
        """Test that device_id can be numeric string."""
        ping = Ping(device_id="12345", lat=40.7128, lon=-74.0060)
        assert ping.device_id == "12345"

    def test_ping_special_characters_device_id(self):
        """Test device_id with special characters."""
        ping = Ping(device_id="device-123_abc", lat=40.7128, lon=-74.0060)
        assert ping.device_id == "device-123_abc"

    def test_ping_float_coordinates_precision(self):
        """Test that float coordinates maintain precision."""
        ping = Ping(
            device_id="device123",
            lat=40.712776,
            lon=-74.005974
        )

        assert pytest.approx(ping.lat, rel=1e-6) == 40.712776
        assert pytest.approx(ping.lon, rel=1e-6) == -74.005974
