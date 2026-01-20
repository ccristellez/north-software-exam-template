"""
Congestion calculation module using percentile-based detection.

This module implements congestion detection by comparing current conditions
to historical percentiles. The approach:
1. Store each completed bucket's data in PostgreSQL (bucket_history table)
2. Query historical percentiles using SQL PERCENTILE_CONT
3. Compare current speed/count to historical 25th/50th percentiles
4. Fall back to absolute thresholds for cells with insufficient history

Why percentiles instead of Z-scores?
- Easier to understand: "below 25th percentile" vs "1.5 standard deviations"
- Easier to explain in interviews
- More robust to outliers than mean/std
- Simple SQL queries instead of Welford's algorithm
"""
from typing import Optional, Tuple
from dataclasses import dataclass
from datetime import datetime, timezone
from redis import Redis
from sqlalchemy import text

from .database import get_db_session, BucketHistory, is_database_configured

# Minimum bucket history records before using percentile-based detection
MIN_SAMPLES_FOR_PERCENTILES = 20

# Fallback thresholds when insufficient history exists
FALLBACK_SPEED_HIGH = 15      # Below this km/h = HIGH congestion
FALLBACK_SPEED_MODERATE = 40  # Below this km/h = MODERATE congestion
FALLBACK_COUNT_HIGH = 30      # Above this count = HIGH congestion
FALLBACK_COUNT_MODERATE = 10  # Above this count = MODERATE congestion


@dataclass
class CellPercentiles:
    """Historical percentile statistics for a cell."""
    speed_p25: Optional[float] = None  # 25th percentile speed
    speed_p50: Optional[float] = None  # 50th percentile (median) speed
    count_p75: Optional[float] = None  # 75th percentile count
    sample_count: int = 0

    @property
    def has_speed_data(self) -> bool:
        """Whether we have enough speed history."""
        return self.speed_p25 is not None and self.speed_p50 is not None

    @property
    def is_calibrated(self) -> bool:
        """Whether we have enough history to use percentile-based detection."""
        return self.sample_count >= MIN_SAMPLES_FOR_PERCENTILES


def get_speed_key(cell_id: str, bucket: int) -> str:
    """Get Redis key for storing speeds in a bucket."""
    return f"cell:{cell_id}:bucket:{bucket}:speeds"


def get_cell_percentiles(cell_id: str, hours_back: int = 168) -> CellPercentiles:
    """
    Get historical percentiles for a cell from the database.

    Uses PostgreSQL's PERCENTILE_CONT for accurate percentile calculation.
    Default looks back 7 days (168 hours).

    Args:
        cell_id: H3 cell ID
        hours_back: How many hours of history to consider (default 7 days)

    Returns:
        CellPercentiles with speed_p25, speed_p50, count_p75, and sample_count
    """
    if not is_database_configured():
        return CellPercentiles()

    session = get_db_session()
    if session is None:
        return CellPercentiles()

    try:
        # Query percentiles using PostgreSQL's PERCENTILE_CONT
        result = session.execute(
            text("""
                SELECT
                    PERCENTILE_CONT(0.25) WITHIN GROUP (ORDER BY avg_speed) as speed_p25,
                    PERCENTILE_CONT(0.50) WITHIN GROUP (ORDER BY avg_speed) as speed_p50,
                    PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY vehicle_count) as count_p75,
                    COUNT(*) as sample_count
                FROM bucket_history
                WHERE cell_id = :cell_id
                  AND bucket_time > NOW() - INTERVAL ':hours hours'
            """.replace(":hours", str(hours_back))),
            {"cell_id": cell_id}
        ).fetchone()

        if result is None or result.sample_count == 0:
            return CellPercentiles()

        return CellPercentiles(
            speed_p25=result.speed_p25,
            speed_p50=result.speed_p50,
            count_p75=result.count_p75,
            sample_count=result.sample_count
        )
    except Exception:
        return CellPercentiles()
    finally:
        session.close()


def save_bucket_to_history(
    cell_id: str,
    bucket_time: datetime,
    vehicle_count: int,
    avg_speed: Optional[float]
) -> bool:
    """
    Save a completed bucket's data to the history table.

    Args:
        cell_id: H3 cell ID
        bucket_time: When the bucket started (UTC)
        vehicle_count: Number of unique devices in the bucket
        avg_speed: Average speed in km/h (or None if no speed data)

    Returns:
        True if saved successfully, False otherwise
    """
    if not is_database_configured():
        return False

    session = get_db_session()
    if session is None:
        return False

    try:
        # Extract time components for time-aware queries
        hour_of_day = bucket_time.hour
        day_of_week = bucket_time.weekday()  # 0=Monday, 6=Sunday

        record = BucketHistory(
            cell_id=cell_id,
            bucket_time=bucket_time,
            vehicle_count=vehicle_count,
            avg_speed=avg_speed,
            hour_of_day=hour_of_day,
            day_of_week=day_of_week
        )
        session.add(record)
        session.commit()
        return True
    except Exception:
        session.rollback()
        return False
    finally:
        session.close()


def record_speed(r: Redis, cell_id: str, bucket: int, speed_kmh: float) -> None:
    """
    Record a speed reading for a cell+bucket in Redis.

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
    Get all speed readings for a cell+bucket from Redis.

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


def calculate_congestion_level(
    current_count: int,
    current_avg_speed: Optional[float],
    percentiles: CellPercentiles
) -> Tuple[str, dict]:
    """
    Calculate congestion level using percentile comparison.

    Algorithm:
    1. If we have enough history, compare current values to percentiles
    2. Speed below 25th percentile = HIGH congestion
    3. Speed below 50th percentile = MODERATE congestion
    4. Otherwise = LOW congestion
    5. Fall back to absolute thresholds if insufficient history

    Args:
        current_count: Number of unique devices in current bucket
        current_avg_speed: Average speed in current bucket (None if no speed data)
        percentiles: Historical percentiles for this cell

    Returns:
        Tuple of (congestion_level, debug_info)
        - congestion_level: "LOW", "MODERATE", or "HIGH"
        - debug_info: Dictionary with calculation details
    """
    debug_info = {
        "method": "percentile" if percentiles.is_calibrated else "fallback",
        "sample_count": percentiles.sample_count,
        "current_count": current_count,
        "current_avg_speed": current_avg_speed,
    }

    # Not enough history - use absolute thresholds
    if not percentiles.is_calibrated:
        level = _calculate_congestion_fallback(current_count, current_avg_speed)
        debug_info["level_reason"] = "insufficient_history"
        return level, debug_info

    # Add percentile values to debug info
    debug_info["speed_p25"] = percentiles.speed_p25
    debug_info["speed_p50"] = percentiles.speed_p50
    debug_info["count_p75"] = percentiles.count_p75

    # Use speed as primary signal (if available)
    if current_avg_speed is not None and percentiles.has_speed_data:
        debug_info["level_reason"] = "speed_percentile"

        if current_avg_speed < percentiles.speed_p25:
            # Below 25th percentile = worst 25% of historical speeds
            return "HIGH", debug_info
        elif current_avg_speed < percentiles.speed_p50:
            # Below median = worse than typical
            return "MODERATE", debug_info
        else:
            # At or above median = normal or better
            # But check if count is unusually high
            if percentiles.count_p75 and current_count > percentiles.count_p75:
                debug_info["level_reason"] = "high_count_despite_good_speed"
                return "MODERATE", debug_info
            return "LOW", debug_info

    # No speed data - use count percentiles only
    debug_info["level_reason"] = "count_only"

    if percentiles.count_p75 and current_count > percentiles.count_p75 * 1.5:
        # Way above 75th percentile
        return "HIGH", debug_info
    elif percentiles.count_p75 and current_count > percentiles.count_p75:
        # Above 75th percentile
        return "MODERATE", debug_info
    else:
        return "LOW", debug_info


def _calculate_congestion_fallback(count: int, avg_speed: Optional[float]) -> str:
    """
    Calculate congestion using absolute thresholds (fallback mode).

    Used when a cell has insufficient historical data for percentile comparison.

    Args:
        count: Number of devices
        avg_speed: Average speed in km/h (or None)

    Returns:
        Congestion level: "LOW", "MODERATE", or "HIGH"
    """
    # Speed is the primary signal (if available)
    if avg_speed is not None:
        if avg_speed < FALLBACK_SPEED_HIGH:
            return "HIGH"
        elif avg_speed < FALLBACK_SPEED_MODERATE:
            return "MODERATE"
        # Good speed - check count as secondary signal
        if count >= FALLBACK_COUNT_HIGH:
            return "MODERATE"  # High count but good speed
        return "LOW"

    # No speed data - use count only
    if count >= FALLBACK_COUNT_HIGH:
        return "HIGH"
    elif count >= FALLBACK_COUNT_MODERATE:
        return "MODERATE"
    return "LOW"


# =============================================================================
# Convenience function for backward compatibility
# =============================================================================

def get_baseline(cell_id: str) -> CellPercentiles:
    """
    Get historical data for a cell. Alias for get_cell_percentiles.

    Kept for backward compatibility with existing code.
    """
    return get_cell_percentiles(cell_id)
