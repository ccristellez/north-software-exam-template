"""
Integration tests for FastAPI endpoints.
"""
import pytest
from datetime import datetime, timezone
from fastapi.testclient import TestClient
from unittest.mock import Mock, patch, MagicMock
from src.api.main import app
from src.api.congestion import CellPercentiles


@pytest.fixture
def client():
    """Create a test client for the FastAPI app."""
    return TestClient(app)


@pytest.fixture
def mock_redis():
    """Create a mock Redis client for real-time data (speeds, counts)."""
    mock = Mock()
    # Default returns for speed data (still in Redis)
    mock.lrange.return_value = []  # No speeds by default
    return mock


@pytest.fixture
def mock_empty_baseline():
    """Patch get_cell_percentiles to return empty percentiles (no history)."""
    return patch("src.api.congestion.get_cell_percentiles", return_value=CellPercentiles())


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
        mock_redis.incr.return_value = 1  # Rate limit check passes
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
        # expire is called twice: once for rate limit (60s), once for cell bucket (300s)
        assert mock_redis.expire.call_count == 2
        # Check that 300 TTL was used for the cell bucket
        expire_calls = mock_redis.expire.call_args_list
        ttls = [call[0][1] for call in expire_calls]
        assert 300 in ttls  # Cell bucket TTL
        assert 60 in ttls   # Rate limit TTL

        # Verify event was published to stream
        mock_redis.xadd.assert_called()

    def test_create_ping_with_timestamp(self, client, mock_redis):
        """Test ping creation with explicit timestamp."""
        mock_redis.incr.return_value = 1  # Rate limit check passes
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
        mock_redis.incr.return_value = 1  # Rate limit check passes
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
class TestRateLimiting:
    """Test suite for rate limiting functionality."""

    def test_rate_limit_allows_normal_traffic(self, client, mock_redis):
        """Test that normal traffic is allowed through."""
        mock_redis.incr.return_value = 1  # First request
        mock_redis.sadd.return_value = 1
        mock_redis.scard.return_value = 1
        mock_redis.expire.return_value = True
        mock_redis.xadd.return_value = "1234567890-0"

        ping_data = {"device_id": "device123", "lat": 40.7128, "lon": -74.0060}

        with patch("src.api.main.get_redis_client", return_value=mock_redis):
            response = client.post("/v1/pings", json=ping_data)

        assert response.status_code == 200

    def test_rate_limit_blocks_excessive_traffic(self, client, mock_redis):
        """Test that excessive traffic is blocked with 429."""
        mock_redis.incr.return_value = 101  # Over the limit

        ping_data = {"device_id": "device123", "lat": 40.7128, "lon": -74.0060}

        with patch("src.api.main.get_redis_client", return_value=mock_redis):
            response = client.post("/v1/pings", json=ping_data)

        assert response.status_code == 429
        assert "Rate limit exceeded" in response.json()["detail"]


@pytest.mark.unit
class TestBatchPingEndpoint:
    """Test suite for POST /v1/pings/batch endpoint."""

    def test_batch_ping_success(self, client, mock_redis):
        """Test successful batch ping processing."""
        mock_redis.incr.return_value = 1
        mock_redis.expire.return_value = True
        mock_pipe = Mock()
        mock_pipe.execute.return_value = [1, True, 1, True]  # sadd, expire for 2 pings
        mock_redis.pipeline.return_value = mock_pipe

        batch_data = {
            "pings": [
                {"device_id": "car1", "lat": 40.7128, "lon": -74.0060},
                {"device_id": "car2", "lat": 40.7130, "lon": -74.0062}
            ]
        }

        with patch("src.api.main.get_redis_client", return_value=mock_redis):
            response = client.post("/v1/pings/batch", json=batch_data)

        assert response.status_code == 200
        data = response.json()
        assert data["total_pings"] == 2
        assert data["unique_devices"] == 2

    def test_batch_ping_rate_limited(self, client, mock_redis):
        """Test batch ping rate limiting."""
        mock_redis.incr.return_value = 101  # Over limit

        batch_data = {
            "pings": [
                {"device_id": "device123", "lat": 40.7128, "lon": -74.0060}
            ]
        }

        with patch("src.api.main.get_redis_client", return_value=mock_redis):
            response = client.post("/v1/pings/batch", json=batch_data)

        assert response.status_code == 429

    def test_batch_ping_max_size(self, client):
        """Test batch ping rejects over 1000 pings."""
        batch_data = {
            "pings": [{"device_id": f"car{i}", "lat": 40.7128, "lon": -74.0060} for i in range(1001)]
        }

        response = client.post("/v1/pings/batch", json=batch_data)
        assert response.status_code == 422  # Validation error


@pytest.mark.unit
class TestCongestionEndpoint:
    """Test suite for GET /v1/congestion endpoint."""

    def test_congestion_low(self, client, mock_redis, mock_empty_baseline):
        """Test congestion endpoint with low traffic (fallback mode, no baseline)."""
        mock_redis.scard.return_value = 5
        mock_redis.lrange.return_value = []  # No speed data

        with patch("src.api.main.get_redis_client", return_value=mock_redis):
            with mock_empty_baseline:
                response = client.get("/v1/congestion?lat=40.7128&lon=-74.0060")

        assert response.status_code == 200
        data = response.json()
        assert data["congestion_level"] == "LOW"
        assert data["vehicle_count"] == 5
        assert "cell_id" in data
        assert data["window_seconds"] == 300
        assert data["calibrated"] == False  # No baseline yet

    def test_congestion_moderate(self, client, mock_redis, mock_empty_baseline):
        """Test congestion endpoint with moderate traffic (fallback mode)."""
        mock_redis.scard.return_value = 15
        mock_redis.lrange.return_value = []

        with patch("src.api.main.get_redis_client", return_value=mock_redis):
            with mock_empty_baseline:
                response = client.get("/v1/congestion?lat=40.7128&lon=-74.0060")

        assert response.status_code == 200
        data = response.json()
        assert data["congestion_level"] == "MODERATE"
        assert data["vehicle_count"] == 15

    def test_congestion_high(self, client, mock_redis, mock_empty_baseline):
        """Test congestion endpoint with high traffic (fallback mode)."""
        mock_redis.scard.return_value = 35
        mock_redis.lrange.return_value = []

        with patch("src.api.main.get_redis_client", return_value=mock_redis):
            with mock_empty_baseline:
                response = client.get("/v1/congestion?lat=40.7128&lon=-74.0060")

        assert response.status_code == 200
        data = response.json()
        assert data["congestion_level"] == "HIGH"
        assert data["vehicle_count"] == 35

    def test_congestion_threshold_boundaries(self, client, mock_redis, mock_empty_baseline):
        """Test congestion level at threshold boundaries (fallback mode)."""
        mock_redis.lrange.return_value = []

        with mock_empty_baseline:
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

    def test_congestion_with_speed_data(self, client, mock_redis, mock_empty_baseline):
        """Test congestion with speed data (fallback mode, low speed = high congestion)."""
        mock_redis.scard.return_value = 5  # Low count
        mock_redis.lrange.return_value = [b'10', b'12', b'8']  # Very slow speeds

        with patch("src.api.main.get_redis_client", return_value=mock_redis):
            with mock_empty_baseline:
                response = client.get("/v1/congestion?lat=40.7128&lon=-74.0060")

        assert response.status_code == 200
        data = response.json()
        assert data["avg_speed_kmh"] == 10.0  # Average of 10, 12, 8
        assert data["congestion_level"] == "HIGH"  # Low speed = high congestion

    def test_congestion_missing_parameters(self, client):
        """Test congestion endpoint without required parameters."""
        response = client.get("/v1/congestion")
        assert response.status_code == 422

    def test_congestion_invalid_coordinates(self, client):
        """Test congestion endpoint with invalid coordinates."""
        response = client.get("/v1/congestion?lat=invalid&lon=-74.0060")
        assert response.status_code == 422


@pytest.fixture
def mock_pipeline():
    """Create a mock Redis pipeline for area queries."""
    mock_pipe = Mock()
    return mock_pipe


@pytest.mark.unit
class TestCongestionAreaEndpoint:
    """Test suite for GET /v1/congestion/area endpoint."""

    def test_congestion_area_radius_0(self, client, mock_redis, mock_empty_baseline):
        """Test area congestion with radius=0 (single cell)."""
        # Pipeline returns: 1 count (scard) + 1 speeds list (lrange)
        mock_pipe = Mock()
        mock_pipe.execute.return_value = [5, []]  # count=5, no speeds
        mock_redis.pipeline.return_value = mock_pipe

        with patch("src.api.main.get_redis_client", return_value=mock_redis):
            with mock_empty_baseline:
                response = client.get("/v1/congestion/area?lat=40.7128&lon=-74.0060&radius=0")

        assert response.status_code == 200
        data = response.json()
        assert data["radius"] == 0
        assert data["total_cells"] == 1
        assert data["area_congestion_level"] == "LOW"
        assert len(data["cells"]) == 1

    def test_congestion_area_radius_1(self, client, mock_redis, mock_empty_baseline):
        """Test area congestion with radius=1 (7 cells)."""
        # Pipeline returns: 7 counts + 7 speed lists
        # Counts: 2, 4, 6, 8, 10, 12, 14 (varying counts)
        # Speeds: all empty
        counts = [2, 4, 6, 8, 10, 12, 14]
        speeds = [[], [], [], [], [], [], []]
        mock_pipe = Mock()
        mock_pipe.execute.return_value = counts + speeds
        mock_redis.pipeline.return_value = mock_pipe

        with patch("src.api.main.get_redis_client", return_value=mock_redis):
            with mock_empty_baseline:
                response = client.get("/v1/congestion/area?lat=40.7128&lon=-74.0060&radius=1")

        assert response.status_code == 200
        data = response.json()
        assert data["radius"] == 1
        assert data["total_cells"] == 7
        assert len(data["cells"]) == 7
        assert data["total_vehicles"] == sum(counts)
        assert "avg_vehicles_per_cell" in data

    def test_congestion_area_default_radius(self, client, mock_redis, mock_empty_baseline):
        """Test area congestion with default radius (should be 1)."""
        # 7 cells for radius=1
        mock_pipe = Mock()
        mock_pipe.execute.return_value = [5] * 7 + [[]] * 7  # 7 counts + 7 empty speed lists
        mock_redis.pipeline.return_value = mock_pipe

        with patch("src.api.main.get_redis_client", return_value=mock_redis):
            with mock_empty_baseline:
                response = client.get("/v1/congestion/area?lat=40.7128&lon=-74.0060")

        assert response.status_code == 200
        data = response.json()
        assert data["radius"] == 1
        assert data["total_cells"] == 7

    def test_congestion_area_high_congestion(self, client, mock_redis, mock_empty_baseline):
        """Test area congestion with high traffic."""
        # All 7 cells have high counts (40 vehicles each)
        mock_pipe = Mock()
        mock_pipe.execute.return_value = [40] * 7 + [[]] * 7
        mock_redis.pipeline.return_value = mock_pipe

        with patch("src.api.main.get_redis_client", return_value=mock_redis):
            with mock_empty_baseline:
                response = client.get("/v1/congestion/area?lat=40.7128&lon=-74.0060&radius=1")

        assert response.status_code == 200
        data = response.json()
        assert data["area_congestion_level"] == "HIGH"
        assert data["high_congestion_cells"] == 7

    def test_congestion_area_cells_sorted(self, client, mock_redis, mock_empty_baseline):
        """Test that cells are sorted by count (highest first)."""
        counts = [5, 35, 10, 25, 8, 15, 20]
        mock_pipe = Mock()
        mock_pipe.execute.return_value = counts + [[]] * 7
        mock_redis.pipeline.return_value = mock_pipe

        with patch("src.api.main.get_redis_client", return_value=mock_redis):
            with mock_empty_baseline:
                response = client.get("/v1/congestion/area?lat=40.7128&lon=-74.0060&radius=1")

        assert response.status_code == 200
        data = response.json()
        cells = data["cells"]

        # Verify cells are sorted by count descending
        for i in range(len(cells) - 1):
            assert cells[i]["count"] >= cells[i + 1]["count"]

    def test_congestion_area_center_cell_marked(self, client, mock_redis, mock_empty_baseline):
        """Test that center cell is marked with is_center=True."""
        mock_pipe = Mock()
        mock_pipe.execute.return_value = [5] * 7 + [[]] * 7
        mock_redis.pipeline.return_value = mock_pipe

        with patch("src.api.main.get_redis_client", return_value=mock_redis):
            with mock_empty_baseline:
                response = client.get("/v1/congestion/area?lat=40.7128&lon=-74.0060&radius=1")

        assert response.status_code == 200
        data = response.json()
        cells = data["cells"]

        # Exactly one cell should be marked as center
        center_cells = [c for c in cells if c["is_center"]]
        assert len(center_cells) == 1
        assert center_cells[0]["cell_id"] == data["center_cell"]

    def test_congestion_area_invalid_radius(self, client, mock_redis, mock_empty_baseline):
        """Test area congestion with invalid radius."""
        mock_pipe = Mock()
        mock_pipe.execute.return_value = [5] + [[]]
        mock_redis.pipeline.return_value = mock_pipe

        with patch("src.api.main.get_redis_client", return_value=mock_redis):
            with mock_empty_baseline:
                response = client.get("/v1/congestion/area?lat=40.7128&lon=-74.0060&radius=invalid")

        assert response.status_code == 422


@pytest.mark.unit
class TestFlushCompletedBucketToHistory:
    """Test suite for flush_completed_bucket_to_history function."""

    def test_flush_skips_when_already_saved(self, mock_redis):
        """Test that flush returns False when bucket was already saved."""
        from src.api.main import flush_completed_bucket_to_history

        # Mock: history_saved flag exists
        mock_redis.exists.return_value = True

        result = flush_completed_bucket_to_history(mock_redis, "test_cell", 100)

        assert result is False
        mock_redis.exists.assert_called_once()
        # Should not proceed to check bucket data
        mock_redis.scard.assert_not_called()

    def test_flush_skips_when_no_data(self, mock_redis):
        """Test that flush returns False when previous bucket has no data."""
        from src.api.main import flush_completed_bucket_to_history

        # Mock: no saved flag, but no data in previous bucket
        mock_redis.exists.return_value = False
        mock_redis.scard.return_value = 0

        result = flush_completed_bucket_to_history(mock_redis, "test_cell", 100)

        assert result is False
        mock_redis.exists.assert_called_once()
        mock_redis.scard.assert_called_once()

    def test_flush_saves_when_data_exists(self, mock_redis):
        """Test that flush saves data and returns True when previous bucket has data."""
        from src.api.main import flush_completed_bucket_to_history

        # Mock: no saved flag, previous bucket has data
        mock_redis.exists.return_value = False
        mock_redis.scard.return_value = 15
        mock_redis.lrange.return_value = [b'45.5', b'50.2', b'38.1']
        mock_redis.setex.return_value = True

        with patch("src.api.main.cong.get_bucket_speeds", return_value=[45.5, 50.2, 38.1]):
            with patch("src.api.main.cong.save_bucket_to_history", return_value=True) as mock_save:
                result = flush_completed_bucket_to_history(mock_redis, "test_cell", 100)

        assert result is True
        # Should mark as saved with 600s TTL
        mock_redis.setex.assert_called_once()
        call_args = mock_redis.setex.call_args[0]
        assert "history_saved" in call_args[0]
        assert call_args[1] == 600
        # Should have called save_bucket_to_history
        mock_save.assert_called_once()


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
