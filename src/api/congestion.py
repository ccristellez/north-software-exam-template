"""
Congestion calculation module using historical baselines and Z-scores.

This module implements a self-calibrating congestion detection system where each
hexagon cell learns its own "normal" traffic patterns over time. Congestion levels
are determined by comparing current conditions to the cell's historical baseline
using Z-scores (standard deviations from the mean).

Key concepts:
- Each cell builds a baseline: avg_speed, avg_count, and their standard deviations
- Z-score measures how far current values deviate from the baseline
- Speed below normal = bad (positive Z), Count above normal = bad (positive Z)
- Combined Z-score determines congestion level

Storage:
- Historical baselines are stored in Supabase (PostgreSQL) for durability
- Real-time speed data stays in Redis (ephemeral, expires with buckets)
"""
from typing import Optional, Tuple
from dataclasses import dataclass
from datetime import datetime, timezone
from redis import Redis

# Import our database stuff
from .database import get_db_session, HexBaseline, is_database_configured

# Minimum samples before using historical calibration
# Below this threshold, fall back to absolute thresholds
MIN_SAMPLES_FOR_CALIBRATION = 50

# Fallback thresholds when no historical data exists
FALLBACK_SPEED_HIGH = 15      # Below this km/h = HIGH congestion
FALLBACK_SPEED_MODERATE = 40  # Below this km/h = MODERATE congestion
FALLBACK_COUNT_HIGH = 30      # Above this count = HIGH congestion
FALLBACK_COUNT_MODERATE = 10  # Above this count = MODERATE congestion

# Z-score thresholds for congestion levels
Z_THRESHOLD_HIGH = 1.5      # Z > 1.5 = HIGH congestion
Z_THRESHOLD_MODERATE = 0.5  # Z > 0.5 = MODERATE congestion


@dataclass
class CellBaseline:
    """Historical baseline statistics for a single hexagon cell."""
    avg_speed: float = 0.0
    avg_count: float = 0.0
    speed_variance: float = 0.0  # We store variance, calculate std when needed
    count_variance: float = 0.0
    sample_count: int = 0

    @property
    def speed_std(self) -> float:
        """Standard deviation of speed."""
        return self.speed_variance ** 0.5 if self.speed_variance > 0 else 1.0

    @property
    def count_std(self) -> float:
        """Standard deviation of count."""
        return self.count_variance ** 0.5 if self.count_variance > 0 else 1.0

    @property
    def is_calibrated(self) -> bool:
        """Whether we have enough samples to use historical calibration."""
        return self.sample_count >= MIN_SAMPLES_FOR_CALIBRATION


def get_speed_key(cell_id: str, bucket: int) -> str:
    """Get Redis key for storing speeds in a bucket."""
    return f"cell:{cell_id}:bucket:{bucket}:speeds"


def get_baseline(cell_id: str) -> CellBaseline:
    """
    Get historical baseline for a cell from Supabase.

    Returns an empty baseline if:
    - Database isn't configured (allows tests to run)
    - No data exists for this cell yet
    - Database connection fails (graceful degradation)
    """
    # If no database, return empty baseline (useful for tests)
    if not is_database_configured():
        return CellBaseline()

    session = get_db_session()
    if session is None:
        return CellBaseline()

    try:
        # Look up the baseline in Supabase
        row = session.query(HexBaseline).filter(HexBaseline.cell_id == cell_id).first()

        if row is None:
            return CellBaseline()

        # Convert database row to our dataclass
        return CellBaseline(
            avg_speed=row.avg_speed or 0.0,
            avg_count=row.avg_count or 0.0,
            speed_variance=row.speed_variance or 0.0,
            count_variance=row.count_variance or 0.0,
            sample_count=row.sample_count or 0
        )
    except Exception:
        # Connection error - return empty baseline (graceful degradation)
        return CellBaseline()
    finally:
        session.close()


def save_baseline(cell_id: str, baseline: CellBaseline) -> bool:
    """
    Save baseline data to Supabase.

    Uses upsert logic - creates new row or updates existing one.

    Returns:
        True if saved successfully, False otherwise
    """
    if not is_database_configured():
        return False  # No database configured

    session = get_db_session()
    if session is None:
        return False

    try:
        # Check if row exists
        row = session.query(HexBaseline).filter(HexBaseline.cell_id == cell_id).first()

        if row is None:
            # Create new row
            row = HexBaseline(
                cell_id=cell_id,
                avg_speed=baseline.avg_speed,
                avg_count=baseline.avg_count,
                speed_variance=baseline.speed_variance,
                count_variance=baseline.count_variance,
                sample_count=baseline.sample_count,
                updated_at=datetime.now(timezone.utc)
            )
            session.add(row)
        else:
            # Update existing row
            row.avg_speed = baseline.avg_speed
            row.avg_count = baseline.avg_count
            row.speed_variance = baseline.speed_variance
            row.count_variance = baseline.count_variance
            row.sample_count = baseline.sample_count
            row.updated_at = datetime.now(timezone.utc)

        session.commit()
        return True
    except Exception:
        # Connection error - graceful degradation
        session.rollback()
        return False
    finally:
        session.close()


def record_speed(r: Redis, cell_id: str, bucket: int, speed_kmh: float) -> None:
    """
    Record a speed reading for a cell+bucket.

    Args:
        r: Redis client
        cell_id: H3 cell ID
        bucket: Time bucket
        speed_kmh: Speed in km/h
    """
    key = get_speed_key(cell_id, bucket)
    r.rpush(key, speed_kmh)
    r.expire(key, 300)  # Same TTL as the count bucket


def get_bucket_speeds(r: Redis, cell_id: str, bucket: int) -> list[float]:
    """
    Get all speed readings for a cell+bucket.

    Args:
        r: Redis client
        cell_id: H3 cell ID
        bucket: Time bucket

    Returns:
        List of speed readings in km/h
    """
    key = get_speed_key(cell_id, bucket)
    speeds = r.lrange(key, 0, -1)
    return [float(s) for s in speeds] if speeds else []


def calculate_z_score(value: float, mean: float, std: float, invert: bool = False) -> float:
    """
    Calculate Z-score (how many standard deviations from mean).

    Args:
        value: Current value
        mean: Historical mean
        std: Historical standard deviation
        invert: If True, flip the sign (for speed where lower = worse)

    Returns:
        Z-score (positive = worse than normal for congestion)
    """
    if std <= 0:
        std = 1.0  # Prevent division by zero

    z = (mean - value) / std if invert else (value - mean) / std
    return z


def calculate_congestion_level(
    current_count: int,
    current_avg_speed: Optional[float],
    baseline: CellBaseline
) -> Tuple[str, dict]:
    """
    Calculate congestion level using Z-scores against historical baseline.

    The algorithm:
    1. If baseline is calibrated (enough samples), use Z-scores
    2. Otherwise, fall back to absolute thresholds
    3. Combine speed Z-score and count Z-score for final determination

    Args:
        current_count: Number of unique devices in current bucket
        current_avg_speed: Average speed in current bucket (None if no speed data)
        baseline: Historical baseline for this cell

    Returns:
        Tuple of (congestion_level, debug_info)
        - congestion_level: "LOW", "MODERATE", or "HIGH"
        - debug_info: Dictionary with calculation details
    """
    debug_info = {
        "method": "calibrated" if baseline.is_calibrated else "fallback",
        "sample_count": baseline.sample_count,
        "baseline_avg_speed": baseline.avg_speed,
        "baseline_avg_count": baseline.avg_count,
        "current_count": current_count,
        "current_avg_speed": current_avg_speed,
    }

    # Not enough historical data - use fallback thresholds
    if not baseline.is_calibrated:
        level = _fallback_congestion(current_count, current_avg_speed)
        debug_info["level_reason"] = "insufficient_history"
        return level, debug_info

    # Calculate Z-scores
    # For count: higher than normal = worse (positive Z)
    count_z = calculate_z_score(current_count, baseline.avg_count, baseline.count_std)
    debug_info["count_z"] = round(count_z, 2)

    # For speed: lower than normal = worse (we invert so positive Z = worse)
    if current_avg_speed is not None and baseline.avg_speed > 0:
        speed_z = calculate_z_score(current_avg_speed, baseline.avg_speed, baseline.speed_std, invert=True)
        debug_info["speed_z"] = round(speed_z, 2)

        # Combined Z-score: average of both signals
        combined_z = (count_z + speed_z) / 2
        debug_info["combined_z"] = round(combined_z, 2)
        debug_info["level_reason"] = "speed_and_count"
    else:
        # No speed data - use count Z-score only
        combined_z = count_z
        debug_info["combined_z"] = round(combined_z, 2)
        debug_info["level_reason"] = "count_only"

    # Determine level from combined Z-score
    if combined_z >= Z_THRESHOLD_HIGH:
        level = "HIGH"
    elif combined_z >= Z_THRESHOLD_MODERATE:
        level = "MODERATE"
    else:
        level = "LOW"

    return level, debug_info


def _fallback_congestion(count: int, avg_speed: Optional[float]) -> str:
    """
    Fallback congestion calculation using absolute thresholds.
    Used when historical baseline is not yet calibrated.

    Args:
        count: Number of devices
        avg_speed: Average speed in km/h (or None)

    Returns:
        Congestion level: "LOW", "MODERATE", or "HIGH"
    """
    # If we have speed data, prioritize it
    if avg_speed is not None:
        if avg_speed < FALLBACK_SPEED_HIGH:
            return "HIGH"
        elif avg_speed < FALLBACK_SPEED_MODERATE:
            return "MODERATE"
        # Speed is good, but check count too
        if count >= FALLBACK_COUNT_HIGH:
            return "MODERATE"  # High count but good speed = moderate
        return "LOW"

    # No speed data - use count only (original behavior)
    if count >= FALLBACK_COUNT_HIGH:
        return "HIGH"
    elif count >= FALLBACK_COUNT_MODERATE:
        return "MODERATE"
    return "LOW"


def update_baseline_with_bucket(
    cell_id: str,
    bucket_count: int,
    bucket_avg_speed: Optional[float],
    alpha: float = 0.1
) -> CellBaseline:
    """
    Update a cell's baseline with data from a completed bucket.

    Uses exponential moving average (EMA) to weight recent data more heavily
    while still maintaining historical context. Also updates variance using
    Welford's online algorithm adaptation.

    The baseline is stored in Supabase so it survives server restarts.

    Args:
        cell_id: H3 cell ID
        bucket_count: Device count from the completed bucket
        bucket_avg_speed: Average speed from the completed bucket (or None)
        alpha: Smoothing factor (0.1 = 10% weight to new data)

    Returns:
        Updated CellBaseline
    """
    # Get current baseline from Supabase
    baseline = get_baseline(cell_id)

    # First sample - initialize directly
    if baseline.sample_count == 0:
        baseline.avg_count = float(bucket_count)
        if bucket_avg_speed is not None:
            baseline.avg_speed = bucket_avg_speed
        baseline.sample_count = 1
        save_baseline(cell_id, baseline)
        return baseline

    # Update count statistics using EMA
    old_avg_count = baseline.avg_count
    baseline.avg_count = (1 - alpha) * baseline.avg_count + alpha * bucket_count

    # Update count variance (adapted Welford's algorithm with EMA)
    count_diff = bucket_count - old_avg_count
    baseline.count_variance = (1 - alpha) * baseline.count_variance + alpha * (count_diff ** 2)

    # Update speed statistics if we have speed data
    if bucket_avg_speed is not None:
        if baseline.avg_speed > 0:
            old_avg_speed = baseline.avg_speed
            baseline.avg_speed = (1 - alpha) * baseline.avg_speed + alpha * bucket_avg_speed
            speed_diff = bucket_avg_speed - old_avg_speed
            baseline.speed_variance = (1 - alpha) * baseline.speed_variance + alpha * (speed_diff ** 2)
        else:
            # First speed reading
            baseline.avg_speed = bucket_avg_speed

    baseline.sample_count += 1
    save_baseline(cell_id, baseline)
    return baseline
