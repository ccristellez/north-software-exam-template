"""
Unit tests for time_utils module.
"""
import pytest
from datetime import datetime, timezone, timedelta
from src.api.time_utils import current_bucket, WINDOW_SECONDS


@pytest.mark.unit
class TestCurrentBucket:
    """Test suite for current_bucket function."""

    def test_current_bucket_with_utc_timestamp(self):
        """Test bucket calculation with UTC timestamp."""
        ts = datetime(2024, 1, 15, 10, 0, 0, tzinfo=timezone.utc)
        bucket = current_bucket(ts)

        # Expected: 1705312800 // 300 = 5684376
        assert bucket == 5684376
        assert isinstance(bucket, int)

    def test_current_bucket_with_naive_timestamp(self):
        """Test bucket calculation with naive datetime (no timezone)."""
        ts = datetime(2024, 1, 15, 10, 0, 0)
        bucket = current_bucket(ts)

        # Should treat as UTC and calculate correctly
        assert isinstance(bucket, int)
        assert bucket > 0

    def test_current_bucket_same_window(self):
        """Test that timestamps within same 5-minute window return same bucket."""
        base_time = datetime(2024, 1, 15, 10, 0, 0, tzinfo=timezone.utc)

        # Same window: 10:00:00 to 10:04:59
        bucket1 = current_bucket(base_time)
        bucket2 = current_bucket(base_time + timedelta(seconds=30))
        bucket3 = current_bucket(base_time + timedelta(seconds=299))

        assert bucket1 == bucket2 == bucket3

    def test_current_bucket_different_windows(self):
        """Test that timestamps in different windows return different buckets."""
        base_time = datetime(2024, 1, 15, 10, 0, 0, tzinfo=timezone.utc)

        bucket1 = current_bucket(base_time)
        bucket2 = current_bucket(base_time + timedelta(seconds=300))
        bucket3 = current_bucket(base_time + timedelta(seconds=600))

        assert bucket1 != bucket2
        assert bucket2 != bucket3
        assert bucket3 == bucket1 + 2

    def test_current_bucket_boundary(self):
        """Test bucket calculation at exact window boundaries."""
        # At exact 5-minute mark
        ts1 = datetime(2024, 1, 15, 10, 0, 0, tzinfo=timezone.utc)
        ts2 = datetime(2024, 1, 15, 10, 5, 0, tzinfo=timezone.utc)

        bucket1 = current_bucket(ts1)
        bucket2 = current_bucket(ts2)

        # Should be consecutive buckets
        assert bucket2 == bucket1 + 1

    def test_current_bucket_sequential_increments(self):
        """Test that buckets increment by 1 for each 5-minute window."""
        base_time = datetime(2024, 1, 15, 10, 0, 0, tzinfo=timezone.utc)

        buckets = [
            current_bucket(base_time + timedelta(minutes=5 * i))
            for i in range(5)
        ]

        # Verify sequential increments
        for i in range(1, len(buckets)):
            assert buckets[i] == buckets[i-1] + 1

    def test_window_seconds_constant(self):
        """Test that WINDOW_SECONDS constant is set correctly."""
        assert WINDOW_SECONDS == 300
        assert WINDOW_SECONDS == 5 * 60
