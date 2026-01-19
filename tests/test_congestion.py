"""
Unit tests for the congestion calculation module.
Tests Z-score based congestion detection with historical baselines.

Note: get_baseline, save_baseline, update_baseline_with_bucket now use Supabase.
These tests mock the database module to avoid needing a real database connection.
"""
import pytest
from unittest.mock import Mock, patch, MagicMock
from src.api.congestion import (
    CellBaseline,
    calculate_z_score,
    calculate_congestion_level,
    get_baseline,
    save_baseline,
    update_baseline_with_bucket,
    MIN_SAMPLES_FOR_CALIBRATION,
    FALLBACK_SPEED_HIGH,
    FALLBACK_SPEED_MODERATE,
    FALLBACK_COUNT_HIGH,
    FALLBACK_COUNT_MODERATE,
)


@pytest.mark.unit
class TestCellBaseline:
    """Test suite for CellBaseline dataclass."""

    def test_baseline_defaults(self):
        """Test default values for a new baseline."""
        baseline = CellBaseline()
        assert baseline.avg_speed == 0.0
        assert baseline.avg_count == 0.0
        assert baseline.speed_variance == 0.0
        assert baseline.count_variance == 0.0
        assert baseline.sample_count == 0

    def test_baseline_speed_std(self):
        """Test speed standard deviation calculation."""
        baseline = CellBaseline(speed_variance=100.0)
        assert baseline.speed_std == 10.0  # sqrt(100)

    def test_baseline_count_std(self):
        """Test count standard deviation calculation."""
        baseline = CellBaseline(count_variance=25.0)
        assert baseline.count_std == 5.0  # sqrt(25)

    def test_baseline_std_zero_variance(self):
        """Test std returns 1.0 when variance is 0 (prevent div by zero)."""
        baseline = CellBaseline(speed_variance=0.0, count_variance=0.0)
        assert baseline.speed_std == 1.0
        assert baseline.count_std == 1.0

    def test_baseline_is_calibrated_true(self):
        """Test is_calibrated returns True when enough samples."""
        baseline = CellBaseline(sample_count=MIN_SAMPLES_FOR_CALIBRATION)
        assert baseline.is_calibrated == True

    def test_baseline_is_calibrated_false(self):
        """Test is_calibrated returns False when not enough samples."""
        baseline = CellBaseline(sample_count=MIN_SAMPLES_FOR_CALIBRATION - 1)
        assert baseline.is_calibrated == False


@pytest.mark.unit
class TestZScoreCalculation:
    """Test suite for Z-score calculation."""

    def test_z_score_at_mean(self):
        """Test Z-score is 0 when value equals mean."""
        z = calculate_z_score(value=50, mean=50, std=10)
        assert z == 0.0

    def test_z_score_above_mean(self):
        """Test Z-score is positive when value is above mean."""
        z = calculate_z_score(value=60, mean=50, std=10)
        assert z == 1.0  # (60-50)/10 = 1

    def test_z_score_below_mean(self):
        """Test Z-score is negative when value is below mean."""
        z = calculate_z_score(value=40, mean=50, std=10)
        assert z == -1.0  # (40-50)/10 = -1

    def test_z_score_inverted(self):
        """Test inverted Z-score (for speed where lower = worse)."""
        # Speed: 40 km/h, mean: 60 km/h, std: 10
        # Without invert: (40-60)/10 = -2
        # With invert: (60-40)/10 = 2 (positive = worse)
        z = calculate_z_score(value=40, mean=60, std=10, invert=True)
        assert z == 2.0

    def test_z_score_zero_std_uses_one(self):
        """Test that zero std is replaced with 1 to prevent div by zero."""
        z = calculate_z_score(value=55, mean=50, std=0)
        assert z == 5.0  # (55-50)/1 = 5


@pytest.mark.unit
class TestCongestionLevelFallback:
    """Test congestion level calculation in fallback mode (no baseline)."""

    def test_fallback_low_count_no_speed(self):
        """Test LOW congestion with low count and no speed data."""
        baseline = CellBaseline(sample_count=0)
        level, _ = calculate_congestion_level(5, None, baseline)
        assert level == "LOW"

    def test_fallback_moderate_count_no_speed(self):
        """Test MODERATE congestion with moderate count and no speed data."""
        baseline = CellBaseline(sample_count=0)
        level, _ = calculate_congestion_level(FALLBACK_COUNT_MODERATE, None, baseline)
        assert level == "MODERATE"

    def test_fallback_high_count_no_speed(self):
        """Test HIGH congestion with high count and no speed data."""
        baseline = CellBaseline(sample_count=0)
        level, _ = calculate_congestion_level(FALLBACK_COUNT_HIGH, None, baseline)
        assert level == "HIGH"

    def test_fallback_high_speed_overrides_low_count(self):
        """Test that good speed results in LOW even with low count."""
        baseline = CellBaseline(sample_count=0)
        level, _ = calculate_congestion_level(5, 60.0, baseline)  # Good speed
        assert level == "LOW"

    def test_fallback_low_speed_means_high(self):
        """Test that very low speed means HIGH congestion."""
        baseline = CellBaseline(sample_count=0)
        level, _ = calculate_congestion_level(5, 10.0, baseline)  # Very slow
        assert level == "HIGH"

    def test_fallback_moderate_speed(self):
        """Test MODERATE speed threshold."""
        baseline = CellBaseline(sample_count=0)
        level, _ = calculate_congestion_level(5, 30.0, baseline)  # Moderate speed
        assert level == "MODERATE"

    def test_fallback_debug_info_shows_method(self):
        """Test debug info indicates fallback mode."""
        baseline = CellBaseline(sample_count=10)
        _, debug = calculate_congestion_level(5, None, baseline)
        assert debug["method"] == "fallback"


@pytest.mark.unit
class TestCongestionLevelCalibrated:
    """Test congestion level calculation in calibrated mode (with baseline)."""

    def test_calibrated_normal_conditions(self):
        """Test LOW when current conditions match baseline."""
        baseline = CellBaseline(
            avg_speed=60.0,
            avg_count=20.0,
            speed_variance=100.0,  # std=10
            count_variance=25.0,   # std=5
            sample_count=MIN_SAMPLES_FOR_CALIBRATION
        )
        # Current: speed=60 (exactly avg), count=20 (exactly avg)
        level, debug = calculate_congestion_level(20, 60.0, baseline)
        assert level == "LOW"
        assert debug["method"] == "calibrated"

    def test_calibrated_high_when_slow_and_crowded(self):
        """Test HIGH when speed is much lower and count is much higher."""
        baseline = CellBaseline(
            avg_speed=60.0,
            avg_count=20.0,
            speed_variance=100.0,  # std=10
            count_variance=25.0,   # std=5
            sample_count=MIN_SAMPLES_FOR_CALIBRATION
        )
        # Current: speed=30 (3 std below), count=35 (3 std above)
        level, debug = calculate_congestion_level(35, 30.0, baseline)
        assert level == "HIGH"
        assert debug["combined_z"] > 1.5

    def test_calibrated_moderate_slightly_worse(self):
        """Test MODERATE when conditions are somewhat worse than normal."""
        baseline = CellBaseline(
            avg_speed=60.0,
            avg_count=20.0,
            speed_variance=100.0,  # std=10
            count_variance=25.0,   # std=5
            sample_count=MIN_SAMPLES_FOR_CALIBRATION
        )
        # Current: speed=50 (1 std below), count=25 (1 std above)
        # Speed Z = 1.0 (inverted), Count Z = 1.0, Combined = 1.0
        level, debug = calculate_congestion_level(25, 50.0, baseline)
        assert level == "MODERATE"

    def test_calibrated_count_only_when_no_speed(self):
        """Test using count Z-score only when no speed data."""
        baseline = CellBaseline(
            avg_speed=60.0,
            avg_count=20.0,
            speed_variance=100.0,
            count_variance=25.0,
            sample_count=MIN_SAMPLES_FOR_CALIBRATION
        )
        # No current speed, count is 2 std above normal
        level, debug = calculate_congestion_level(30, None, baseline)
        assert debug["level_reason"] == "count_only"
        assert level == "HIGH"  # Z > 1.5


@pytest.mark.unit
class TestBaselineStorage:
    """Test baseline storage and retrieval from Supabase."""

    def test_get_baseline_no_database(self):
        """Test getting baseline when database is not configured."""
        # When database isn't set up, should return empty baseline
        with patch("src.api.congestion.is_database_configured", return_value=False):
            baseline = get_baseline("test_cell")

        assert baseline.sample_count == 0
        assert baseline.avg_speed == 0.0

    def test_get_baseline_empty(self):
        """Test getting baseline when none exists in database."""
        mock_session = MagicMock()
        mock_session.query.return_value.filter.return_value.first.return_value = None

        with patch("src.api.congestion.is_database_configured", return_value=True):
            with patch("src.api.congestion.get_db_session", return_value=mock_session):
                baseline = get_baseline("test_cell")

        assert baseline.sample_count == 0
        assert baseline.avg_speed == 0.0
        mock_session.close.assert_called_once()

    def test_get_baseline_with_data(self):
        """Test getting baseline with existing data in database."""
        # Create a mock database row
        mock_row = MagicMock()
        mock_row.avg_speed = 55.5
        mock_row.avg_count = 15.0
        mock_row.speed_variance = 100.0
        mock_row.count_variance = 25.0
        mock_row.sample_count = 100

        mock_session = MagicMock()
        mock_session.query.return_value.filter.return_value.first.return_value = mock_row

        with patch("src.api.congestion.is_database_configured", return_value=True):
            with patch("src.api.congestion.get_db_session", return_value=mock_session):
                baseline = get_baseline("test_cell")

        assert baseline.avg_speed == 55.5
        assert baseline.avg_count == 15.0
        assert baseline.speed_variance == 100.0
        assert baseline.count_variance == 25.0
        assert baseline.sample_count == 100
        mock_session.close.assert_called_once()

    def test_save_baseline_no_database(self):
        """Test saving baseline when database is not configured (should silently skip)."""
        baseline = CellBaseline(avg_speed=60.0, sample_count=50)

        # Should not raise an error
        with patch("src.api.congestion.is_database_configured", return_value=False):
            save_baseline("test_cell", baseline)

    def test_save_baseline_new_row(self):
        """Test saving baseline creates new row when none exists."""
        mock_session = MagicMock()
        mock_session.query.return_value.filter.return_value.first.return_value = None

        baseline = CellBaseline(
            avg_speed=60.0,
            avg_count=20.0,
            speed_variance=100.0,
            count_variance=25.0,
            sample_count=50
        )

        with patch("src.api.congestion.is_database_configured", return_value=True):
            with patch("src.api.congestion.get_db_session", return_value=mock_session):
                save_baseline("test_cell", baseline)

        mock_session.add.assert_called_once()
        mock_session.commit.assert_called_once()
        mock_session.close.assert_called_once()


@pytest.mark.unit
class TestBaselineUpdate:
    """Test baseline update with new bucket data (now uses Supabase)."""

    def test_update_first_sample(self):
        """Test updating baseline with first sample."""
        mock_session = MagicMock()
        mock_session.query.return_value.filter.return_value.first.return_value = None

        with patch("src.api.congestion.is_database_configured", return_value=True):
            with patch("src.api.congestion.get_db_session", return_value=mock_session):
                baseline = update_baseline_with_bucket(
                    "test_cell",
                    bucket_count=15,
                    bucket_avg_speed=50.0
                )

        assert baseline.avg_count == 15.0
        assert baseline.avg_speed == 50.0
        assert baseline.sample_count == 1

    def test_update_with_ema(self):
        """Test that update uses exponential moving average."""
        # Create a mock existing baseline row
        mock_row = MagicMock()
        mock_row.avg_speed = 60.0
        mock_row.avg_count = 20.0
        mock_row.speed_variance = 100.0
        mock_row.count_variance = 25.0
        mock_row.sample_count = 10

        mock_session = MagicMock()
        # First call is get_baseline, second call is save_baseline
        mock_session.query.return_value.filter.return_value.first.side_effect = [
            mock_row,  # get_baseline finds existing row
            mock_row,  # save_baseline finds existing row to update
        ]

        with patch("src.api.congestion.is_database_configured", return_value=True):
            with patch("src.api.congestion.get_db_session", return_value=mock_session):
                baseline = update_baseline_with_bucket(
                    "test_cell",
                    bucket_count=30,  # Higher than avg
                    bucket_avg_speed=40.0,  # Lower than avg
                    alpha=0.1
                )

        # EMA: new_avg = (1-0.1)*60 + 0.1*40 = 54 + 4 = 58
        assert baseline.avg_speed == 58.0
        # EMA: new_avg = (1-0.1)*20 + 0.1*30 = 18 + 3 = 21
        assert baseline.avg_count == 21.0
        assert baseline.sample_count == 11
