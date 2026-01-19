"""
Integration tests for FastAPI endpoints.
"""
import pytest
from datetime import datetime, timezone
from fastapi.testclient import TestClient
from unittest.mock import Mock, patch, MagicMock
from src.api.main import app


@pytest.fixture
def client():
    """Create a test client for the FastAPI app."""
    return TestClient(app)


@pytest.fixture
def mock_redis():
    """Create a mock Redis client."""
    return Mock()


@pytest.mark.unit
class TestHealthEndpoint:
    """Test suite for /health endpoint."""

    def test_health_redis_connected(self, client, mock_redis):
        """Test health check when Redis is connected."""
        mock_redis.ping.return_value = True

        with patch("src.api.main.get_redis_client", return_value=mock_redis):
            response = client.get("/health")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert data["redis"] == "connected"
        mock_redis.ping.assert_called_once()

    def test_health_redis_disconnected(self, client, mock_redis):
        """Test health check when Redis is disconnected."""
        from redis.exceptions import RedisError
        mock_redis.ping.side_effect = RedisError("Connection failed")

        with patch("src.api.main.get_redis_client", return_value=mock_redis):
            response = client.get("/health")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert data["redis"] == "disconnected"


@pytest.mark.unit
class TestCreatePingEndpoint:
    """Test suite for POST /v1/pings endpoint."""

    def test_create_ping_success(self, client, mock_redis):
        """Test successful ping creation."""
        mock_redis.sadd.return_value = 1
        mock_redis.scard.return_value = 5
        mock_redis.expire.return_value = True
        mock_redis.xadd.return_value = "1234567890-0"  # Mock stream event ID

        ping_data = {
            "device_id": "device123",
            "lat": 40.7128,
            "lon": -74.0060
        }

        with patch("src.api.main.get_redis_client", return_value=mock_redis):
            response = client.post("/v1/pings", json=ping_data)

        assert response.status_code == 200
        data = response.json()
        assert data["message"] == "Ping received"
        assert data["device_id"] == "device123"
        assert "cell_id" in data
        assert "bucket" in data
        assert data["bucket_count"] == 5

        # Verify Redis operations
        mock_redis.sadd.assert_called_once()
        mock_redis.scard.assert_called_once()
        mock_redis.expire.assert_called_once()
        expire_call_args = mock_redis.expire.call_args
        assert expire_call_args[0][1] == 300  # TTL = 300 seconds

        # Verify event was published to stream
        mock_redis.xadd.assert_called()

    def test_create_ping_with_timestamp(self, client, mock_redis):
        """Test ping creation with explicit timestamp."""
        mock_redis.sadd.return_value = 1
        mock_redis.scard.return_value = 1
        mock_redis.expire.return_value = True
        mock_redis.xadd.return_value = "1234567890-0"

        ts = datetime(2024, 1, 15, 10, 0, 0, tzinfo=timezone.utc)
        ping_data = {
            "device_id": "device456",
            "lat": 51.5074,
            "lon": -0.1278,
            "timestamp": ts.isoformat()
        }

        with patch("src.api.main.get_redis_client", return_value=mock_redis):
            response = client.post("/v1/pings", json=ping_data)

        assert response.status_code == 200
        data = response.json()
        assert data["device_id"] == "device456"

    def test_create_ping_invalid_data(self, client):
        """Test ping creation with invalid data."""
        ping_data = {
            "device_id": "device123",
            "lat": "invalid",
            "lon": -74.0060
        }

        response = client.post("/v1/pings", json=ping_data)
        assert response.status_code == 422  # Validation error

    def test_create_ping_missing_device_id(self, client):
        """Test ping creation without device_id."""
        ping_data = {
            "lat": 40.7128,
            "lon": -74.0060
        }

        response = client.post("/v1/pings", json=ping_data)
        assert response.status_code == 422

    def test_create_ping_empty_device_id(self, client):
        """Test ping creation with empty device_id."""
        ping_data = {
            "device_id": "",
            "lat": 40.7128,
            "lon": -74.0060
        }

        response = client.post("/v1/pings", json=ping_data)
        assert response.status_code == 422

    def test_duplicate_pings_counted_once(self, client, mock_redis):
        """Test that multiple pings from same device are counted only once."""
        # Simulate the behavior of Redis SADD and SCARD
        # First SADD returns 1 (new member added), second returns 0 (already exists)
        mock_redis.sadd.side_effect = [1, 0]  # 1st ping adds, 2nd ping already exists
        mock_redis.scard.side_effect = [1, 1]  # Count remains 1 for both
        mock_redis.expire.return_value = True
        mock_redis.xadd.return_value = "1234567890-0"

        ping_data = {
            "device_id": "device123",
            "lat": 40.7128,
            "lon": -74.0060
        }

        with patch("src.api.main.get_redis_client", return_value=mock_redis):
            # First ping from device123
            response1 = client.post("/v1/pings", json=ping_data)
            assert response1.status_code == 200
            assert response1.json()["bucket_count"] == 1

            # Second ping from same device123 (spam)
            response2 = client.post("/v1/pings", json=ping_data)
            assert response2.status_code == 200
            assert response2.json()["bucket_count"] == 1  # Still 1, not 2!

        # Verify SADD was called twice (once per ping)
        assert mock_redis.sadd.call_count == 2
        # Verify SCARD was called twice (once per ping)
        assert mock_redis.scard.call_count == 2


@pytest.mark.unit
class TestPingCountEndpoint:
    """Test suite for GET /v1/pings/count endpoint."""

    def test_ping_count_with_data(self, client, mock_redis):
        """Test ping count when data exists."""
        mock_redis.get.return_value = "42"

        with patch("src.api.main.get_redis_client", return_value=mock_redis):
            response = client.get("/v1/pings/count")

        assert response.status_code == 200
        data = response.json()
        assert data["total_pings"] == 42
        mock_redis.get.assert_called_once_with("pings:total")

    def test_ping_count_no_data(self, client, mock_redis):
        """Test ping count when no data exists."""
        mock_redis.get.return_value = None

        with patch("src.api.main.get_redis_client", return_value=mock_redis):
            response = client.get("/v1/pings/count")

        assert response.status_code == 200
        data = response.json()
        assert data["total_pings"] == 0


@pytest.mark.unit
class TestCongestionEndpoint:
    """Test suite for GET /v1/congestion endpoint."""

    def test_congestion_low(self, client, mock_redis):
        """Test congestion endpoint with low traffic."""
        mock_redis.scard.return_value = 5

        with patch("src.api.main.get_redis_client", return_value=mock_redis):
            response = client.get("/v1/congestion?lat=40.7128&lon=-74.0060")

        assert response.status_code == 200
        data = response.json()
        assert data["congestion_level"] == "LOW"
        assert data["vehicle_count"] == 5
        assert "cell_id" in data
        assert data["window_seconds"] == 300

    def test_congestion_moderate(self, client, mock_redis):
        """Test congestion endpoint with moderate traffic."""
        mock_redis.scard.return_value = 15

        with patch("src.api.main.get_redis_client", return_value=mock_redis):
            response = client.get("/v1/congestion?lat=40.7128&lon=-74.0060")

        assert response.status_code == 200
        data = response.json()
        assert data["congestion_level"] == "MODERATE"
        assert data["vehicle_count"] == 15

    def test_congestion_high(self, client, mock_redis):
        """Test congestion endpoint with high traffic."""
        mock_redis.scard.return_value = 35

        with patch("src.api.main.get_redis_client", return_value=mock_redis):
            response = client.get("/v1/congestion?lat=40.7128&lon=-74.0060")

        assert response.status_code == 200
        data = response.json()
        assert data["congestion_level"] == "HIGH"
        assert data["vehicle_count"] == 35

    def test_congestion_threshold_boundaries(self, client, mock_redis):
        """Test congestion level at threshold boundaries."""
        # Exactly 10 vehicles = MODERATE
        mock_redis.scard.return_value = 10
        with patch("src.api.main.get_redis_client", return_value=mock_redis):
            response = client.get("/v1/congestion?lat=40.7128&lon=-74.0060")
            assert response.json()["congestion_level"] == "MODERATE"

        # Exactly 30 vehicles = HIGH
        mock_redis.scard.return_value = 30
        with patch("src.api.main.get_redis_client", return_value=mock_redis):
            response = client.get("/v1/congestion?lat=40.7128&lon=-74.0060")
            assert response.json()["congestion_level"] == "HIGH"

        # 9 vehicles = LOW
        mock_redis.scard.return_value = 9
        with patch("src.api.main.get_redis_client", return_value=mock_redis):
            response = client.get("/v1/congestion?lat=40.7128&lon=-74.0060")
            assert response.json()["congestion_level"] == "LOW"

    def test_congestion_missing_parameters(self, client):
        """Test congestion endpoint without required parameters."""
        response = client.get("/v1/congestion")
        assert response.status_code == 422

    def test_congestion_invalid_coordinates(self, client):
        """Test congestion endpoint with invalid coordinates."""
        response = client.get("/v1/congestion?lat=invalid&lon=-74.0060")
        assert response.status_code == 422


@pytest.mark.unit
class TestCongestionAreaEndpoint:
    """Test suite for GET /v1/congestion/area endpoint."""

    def test_congestion_area_radius_0(self, client, mock_redis):
        """Test area congestion with radius=0 (single cell)."""
        mock_redis.scard.return_value = 5

        with patch("src.api.main.get_redis_client", return_value=mock_redis):
            response = client.get("/v1/congestion/area?lat=40.7128&lon=-74.0060&radius=0")

        assert response.status_code == 200
        data = response.json()
        assert data["radius"] == 0
        assert data["total_cells"] == 1
        assert data["area_congestion_level"] == "LOW"
        assert len(data["cells"]) == 1

    def test_congestion_area_radius_1(self, client, mock_redis):
        """Test area congestion with radius=1 (7 cells)."""
        # Return different counts for different cells
        call_count = [0]

        def mock_scard(key):
            call_count[0] += 1
            return call_count[0] * 2  # Return 2, 4, 6, 8, ...

        mock_redis.scard.side_effect = mock_scard

        with patch("src.api.main.get_redis_client", return_value=mock_redis):
            response = client.get("/v1/congestion/area?lat=40.7128&lon=-74.0060&radius=1")

        assert response.status_code == 200
        data = response.json()
        assert data["radius"] == 1
        assert data["total_cells"] == 7
        assert len(data["cells"]) == 7
        assert data["total_vehicles"] > 0
        assert "avg_vehicles_per_cell" in data

    def test_congestion_area_default_radius(self, client, mock_redis):
        """Test area congestion with default radius (should be 1)."""
        mock_redis.scard.return_value = 5

        with patch("src.api.main.get_redis_client", return_value=mock_redis):
            response = client.get("/v1/congestion/area?lat=40.7128&lon=-74.0060")

        assert response.status_code == 200
        data = response.json()
        assert data["radius"] == 1
        assert data["total_cells"] == 7

    def test_congestion_area_high_congestion(self, client, mock_redis):
        """Test area congestion with high traffic."""
        mock_redis.scard.return_value = 40  # High count for all cells

        with patch("src.api.main.get_redis_client", return_value=mock_redis):
            response = client.get("/v1/congestion/area?lat=40.7128&lon=-74.0060&radius=1")

        assert response.status_code == 200
        data = response.json()
        assert data["area_congestion_level"] == "HIGH"
        assert data["high_congestion_cells"] == 7

    def test_congestion_area_cells_sorted(self, client, mock_redis):
        """Test that cells are sorted by count (highest first)."""
        counts = [5, 35, 10, 25, 8, 15, 20]
        call_index = [0]

        def mock_scard(key):
            result = counts[call_index[0] % len(counts)]
            call_index[0] += 1
            return result

        mock_redis.scard.side_effect = mock_scard

        with patch("src.api.main.get_redis_client", return_value=mock_redis):
            response = client.get("/v1/congestion/area?lat=40.7128&lon=-74.0060&radius=1")

        assert response.status_code == 200
        data = response.json()
        cells = data["cells"]

        # Verify cells are sorted by count descending
        for i in range(len(cells) - 1):
            assert cells[i]["count"] >= cells[i + 1]["count"]

    def test_congestion_area_center_cell_marked(self, client, mock_redis):
        """Test that center cell is marked with is_center=True."""
        mock_redis.scard.return_value = 5

        with patch("src.api.main.get_redis_client", return_value=mock_redis):
            response = client.get("/v1/congestion/area?lat=40.7128&lon=-74.0060&radius=1")

        assert response.status_code == 200
        data = response.json()
        cells = data["cells"]

        # Exactly one cell should be marked as center
        center_cells = [c for c in cells if c["is_center"]]
        assert len(center_cells) == 1
        assert center_cells[0]["cell_id"] == data["center_cell"]

    def test_congestion_area_invalid_radius(self, client, mock_redis):
        """Test area congestion with invalid radius."""
        mock_redis.scard.return_value = 5

        with patch("src.api.main.get_redis_client", return_value=mock_redis):
            response = client.get("/v1/congestion/area?lat=40.7128&lon=-74.0060&radius=invalid")

        assert response.status_code == 422


@pytest.mark.integration
class TestEndpointsWithRealRedis:
    """Integration tests that require actual Redis connection."""

    def test_health_with_real_redis(self, client):
        """Test health endpoint with real Redis (if available)."""
        response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert "status" in data
        assert "redis" in data
        # Redis may be connected or disconnected, both are valid responses
        assert data["redis"] in ["connected", "disconnected"]
