"""
Unit tests for the congestion calculation module.
Tests percentile-based congestion detection with historical bucket data.

Note: get_cell_percentiles and save_bucket_to_history use Supabase.
These tests mock the database module to avoid needing a real database connection.
"""
import pytest
from unittest.mock import Mock, patch, MagicMock
from datetime import datetime, timezone
from src.api.congestion import (
    CellPercentiles,
    calculate_congestion_level,
    get_cell_percentiles,
    save_bucket_to_history,
    _calculate_congestion_fallback,
    MIN_SAMPLES_FOR_PERCENTILES,
    FALLBACK_SPEED_HIGH,
    FALLBACK_SPEED_MODERATE,
    FALLBACK_COUNT_HIGH,
    FALLBACK_COUNT_MODERATE,
)


@pytest.mark.unit
class TestCellPercentiles:
    """Test suite for CellPercentiles dataclass."""

    def test_percentiles_defaults(self):
        """Test default values for empty percentiles."""
        percentiles = CellPercentiles()
        assert percentiles.speed_p25 is None
        assert percentiles.speed_p50 is None
        assert percentiles.count_p75 is None
        assert percentiles.sample_count == 0

    def test_percentiles_has_speed_data_true(self):
        """Test has_speed_data returns True when both percentiles exist."""
        percentiles = CellPercentiles(speed_p25=30.0, speed_p50=45.0)
        assert percentiles.has_speed_data == True

    def test_percentiles_has_speed_data_false(self):
        """Test has_speed_data returns False when percentiles are missing."""
        percentiles = CellPercentiles(speed_p25=30.0)  # Missing p50
        assert percentiles.has_speed_data == False

    def test_percentiles_is_calibrated_true(self):
        """Test is_calibrated returns True when enough samples."""
        percentiles = CellPercentiles(sample_count=MIN_SAMPLES_FOR_PERCENTILES)
        assert percentiles.is_calibrated == True

    def test_percentiles_is_calibrated_false(self):
        """Test is_calibrated returns False when not enough samples."""
        percentiles = CellPercentiles(sample_count=MIN_SAMPLES_FOR_PERCENTILES - 1)
        assert percentiles.is_calibrated == False


@pytest.mark.unit
class TestCongestionLevelFallback:
    """Test congestion level calculation in fallback mode (no history)."""

    def test_fallback_low_count_no_speed(self):
        """Test LOW congestion with low count and no speed data."""
        percentiles = CellPercentiles(sample_count=0)
        level, _ = calculate_congestion_level(5, None, percentiles)
        assert level == "LOW"

    def test_fallback_moderate_count_no_speed(self):
        """Test MODERATE congestion with moderate count and no speed data."""
        percentiles = CellPercentiles(sample_count=0)
        level, _ = calculate_congestion_level(FALLBACK_COUNT_MODERATE, None, percentiles)
        assert level == "MODERATE"

    def test_fallback_high_count_no_speed(self):
        """Test HIGH congestion with high count and no speed data."""
        percentiles = CellPercentiles(sample_count=0)
        level, _ = calculate_congestion_level(FALLBACK_COUNT_HIGH, None, percentiles)
        assert level == "HIGH"

    def test_fallback_high_speed_means_low(self):
        """Test that good speed results in LOW even with low count."""
        percentiles = CellPercentiles(sample_count=0)
        level, _ = calculate_congestion_level(5, 60.0, percentiles)  # Good speed
        assert level == "LOW"

    def test_fallback_low_speed_means_high(self):
        """Test that very low speed means HIGH congestion."""
        percentiles = CellPercentiles(sample_count=0)
        level, _ = calculate_congestion_level(5, 10.0, percentiles)  # Very slow
        assert level == "HIGH"

    def test_fallback_moderate_speed(self):
        """Test MODERATE speed threshold."""
        percentiles = CellPercentiles(sample_count=0)
        level, _ = calculate_congestion_level(5, 30.0, percentiles)  # Moderate speed
        assert level == "MODERATE"

    def test_fallback_debug_info_shows_method(self):
        """Test debug info indicates fallback mode."""
        percentiles = CellPercentiles(sample_count=10)
        _, debug = calculate_congestion_level(5, None, percentiles)
        assert debug["method"] == "fallback"


@pytest.mark.unit
class TestCongestionLevelCalibrated:
    """Test congestion level calculation in calibrated mode (with percentiles)."""

    def test_calibrated_low_when_above_median(self):
        """Test LOW when current speed is above median (p50)."""
        percentiles = CellPercentiles(
            speed_p25=30.0,  # 25th percentile
            speed_p50=45.0,  # median
            count_p75=25.0,  # 75th percentile count
            sample_count=MIN_SAMPLES_FOR_PERCENTILES
        )
        # Current speed 50 is above median 45
        level, debug = calculate_congestion_level(15, 50.0, percentiles)
        assert level == "LOW"
        assert debug["method"] == "percentile"

    def test_calibrated_high_when_below_p25(self):
        """Test HIGH when current speed is below 25th percentile."""
        percentiles = CellPercentiles(
            speed_p25=30.0,
            speed_p50=45.0,
            count_p75=25.0,
            sample_count=MIN_SAMPLES_FOR_PERCENTILES
        )
        # Current speed 20 is below p25 of 30
        level, debug = calculate_congestion_level(15, 20.0, percentiles)
        assert level == "HIGH"
        assert debug["level_reason"] == "speed_percentile"

    def test_calibrated_moderate_between_p25_and_p50(self):
        """Test MODERATE when current speed is between p25 and p50."""
        percentiles = CellPercentiles(
            speed_p25=30.0,
            speed_p50=45.0,
            count_p75=25.0,
            sample_count=MIN_SAMPLES_FOR_PERCENTILES
        )
        # Current speed 35 is between p25 (30) and p50 (45)
        level, debug = calculate_congestion_level(15, 35.0, percentiles)
        assert level == "MODERATE"

    def test_calibrated_moderate_high_count_good_speed(self):
        """Test MODERATE when speed is good but count is above p75."""
        percentiles = CellPercentiles(
            speed_p25=30.0,
            speed_p50=45.0,
            count_p75=25.0,
            sample_count=MIN_SAMPLES_FOR_PERCENTILES
        )
        # Speed 50 is good (above p50), but count 30 is above p75 (25)
        level, debug = calculate_congestion_level(30, 50.0, percentiles)
        assert level == "MODERATE"
        assert debug["level_reason"] == "high_count_despite_good_speed"

    def test_calibrated_count_only_when_no_speed(self):
        """Test using count percentiles when no current speed data."""
        percentiles = CellPercentiles(
            speed_p25=30.0,
            speed_p50=45.0,
            count_p75=20.0,
            sample_count=MIN_SAMPLES_FOR_PERCENTILES
        )
        # No current speed, count 35 is way above p75 (20)
        level, debug = calculate_congestion_level(35, None, percentiles)
        assert debug["level_reason"] == "count_only"
        assert level == "HIGH"  # 35 > 20 * 1.5

    def test_calibrated_count_only_moderate(self):
        """Test MODERATE with count above p75 but not way above."""
        percentiles = CellPercentiles(
            speed_p25=30.0,
            speed_p50=45.0,
            count_p75=20.0,
            sample_count=MIN_SAMPLES_FOR_PERCENTILES
        )
        # No current speed, count 25 is above p75 but not > p75 * 1.5
        level, debug = calculate_congestion_level(25, None, percentiles)
        assert level == "MODERATE"


@pytest.mark.unit
class TestPercentileRetrieval:
    """Test percentile retrieval from Supabase."""

    def test_get_percentiles_no_database(self):
        """Test getting percentiles when database is not configured."""
        with patch("src.api.congestion.is_database_configured", return_value=False):
            percentiles = get_cell_percentiles("test_cell")

        assert percentiles.sample_count == 0
        assert percentiles.speed_p25 is None

    def test_get_percentiles_empty_result(self):
        """Test getting percentiles when no history exists."""
        mock_session = MagicMock()
        mock_result = MagicMock()
        mock_result.sample_count = 0
        mock_session.execute.return_value.fetchone.return_value = mock_result

        with patch("src.api.congestion.is_database_configured", return_value=True):
            with patch("src.api.congestion.get_db_session", return_value=mock_session):
                percentiles = get_cell_percentiles("test_cell")

        assert percentiles.sample_count == 0
        mock_session.close.assert_called_once()

    def test_get_percentiles_with_data(self):
        """Test getting percentiles with existing history."""
        mock_session = MagicMock()
        mock_result = MagicMock()
        mock_result.speed_p25 = 30.0
        mock_result.speed_p50 = 45.0
        mock_result.count_p75 = 25.0
        mock_result.sample_count = 100
        mock_session.execute.return_value.fetchone.return_value = mock_result

        with patch("src.api.congestion.is_database_configured", return_value=True):
            with patch("src.api.congestion.get_db_session", return_value=mock_session):
                percentiles = get_cell_percentiles("test_cell")

        assert percentiles.speed_p25 == 30.0
        assert percentiles.speed_p50 == 45.0
        assert percentiles.count_p75 == 25.0
        assert percentiles.sample_count == 100
        mock_session.close.assert_called_once()


@pytest.mark.unit
class TestBucketHistorySave:
    """Test saving bucket data to history table."""

    def test_save_bucket_no_database(self):
        """Test saving bucket when database is not configured."""
        with patch("src.api.congestion.is_database_configured", return_value=False):
            result = save_bucket_to_history(
                "test_cell",
                datetime.now(timezone.utc),
                15,
                50.0
            )

        assert result == False

    def test_save_bucket_success(self):
        """Test saving bucket data successfully."""
        mock_session = MagicMock()

        with patch("src.api.congestion.is_database_configured", return_value=True):
            with patch("src.api.congestion.get_db_session", return_value=mock_session):
                result = save_bucket_to_history(
                    "test_cell",
                    datetime(2024, 1, 15, 8, 30, tzinfo=timezone.utc),
                    15,
                    50.0
                )

        assert result == True
        mock_session.add.assert_called_once()
        mock_session.commit.assert_called_once()
        mock_session.close.assert_called_once()

        # Verify the record has correct hour_of_day and day_of_week
        saved_record = mock_session.add.call_args[0][0]
        assert saved_record.hour_of_day == 8  # 8 AM
        assert saved_record.day_of_week == 0  # Monday (Jan 15, 2024)

    def test_save_bucket_with_null_speed(self):
        """Test saving bucket with no speed data."""
        mock_session = MagicMock()

        with patch("src.api.congestion.is_database_configured", return_value=True):
            with patch("src.api.congestion.get_db_session", return_value=mock_session):
                result = save_bucket_to_history(
                    "test_cell",
                    datetime.now(timezone.utc),
                    15,
                    None  # No speed data
                )

        assert result == True
        saved_record = mock_session.add.call_args[0][0]
        assert saved_record.avg_speed is None


@pytest.mark.unit
class TestFallbackFunction:
    """Test the internal fallback calculation function."""

    def test_fallback_speed_high(self):
        """Test HIGH when speed is very low."""
        level = _calculate_congestion_fallback(5, 10.0)
        assert level == "HIGH"

    def test_fallback_speed_moderate(self):
        """Test MODERATE when speed is moderately low."""
        level = _calculate_congestion_fallback(5, 30.0)
        assert level == "MODERATE"

    def test_fallback_speed_low(self):
        """Test LOW when speed is good."""
        level = _calculate_congestion_fallback(5, 60.0)
        assert level == "LOW"

    def test_fallback_count_high(self):
        """Test HIGH count when no speed data."""
        level = _calculate_congestion_fallback(FALLBACK_COUNT_HIGH, None)
        assert level == "HIGH"

    def test_fallback_count_moderate(self):
        """Test MODERATE count when no speed data."""
        level = _calculate_congestion_fallback(FALLBACK_COUNT_MODERATE, None)
        assert level == "MODERATE"

    def test_fallback_count_low(self):
        """Test LOW count when no speed data."""
        level = _calculate_congestion_fallback(5, None)
        assert level == "LOW"

    def test_fallback_good_speed_high_count(self):
        """Test that good speed with high count gives MODERATE."""
        level = _calculate_congestion_fallback(FALLBACK_COUNT_HIGH, 60.0)
        assert level == "MODERATE"  # High count but good speed
