-- ============================================================================
-- Congestion Monitor Database Schema
-- ============================================================================
-- Database: Supabase PostgreSQL
-- Purpose: Store historical bucket data for percentile-based congestion detection
-- ============================================================================

-- ----------------------------------------------------------------------------
-- Table: bucket_history
-- ----------------------------------------------------------------------------
-- Stores completed 5-minute bucket data for each cell. This raw data is used
-- to calculate percentiles for congestion detection.
--
-- Design rationale:
--   - Store raw bucket data instead of computed statistics (mean/variance)
--   - Enables simple SQL percentile queries (PERCENTILE_CONT)
--   - Easier to debug and explain than Welford's algorithm
--   - Supports time-of-day filtering (rush hour vs. midnight)
--
-- Example query to get congestion thresholds:
--   SELECT
--     PERCENTILE_CONT(0.25) WITHIN GROUP (ORDER BY avg_speed) as speed_p25,
--     PERCENTILE_CONT(0.50) WITHIN GROUP (ORDER BY avg_speed) as speed_p50
--   FROM bucket_history
--   WHERE cell_id = '882a100d63fffff'
--     AND bucket_time > NOW() - INTERVAL '7 days';
-- ----------------------------------------------------------------------------

CREATE TABLE bucket_history (
    id SERIAL PRIMARY KEY,

    -- H3 hexagon cell identifier (resolution 8, ~460m)
    cell_id VARCHAR(20) NOT NULL,

    -- When this 5-minute bucket started (UTC)
    bucket_time TIMESTAMPTZ NOT NULL,

    -- Number of unique devices seen in this bucket
    vehicle_count INTEGER NOT NULL,

    -- Average speed of vehicles in km/h (NULL if no speed data)
    avg_speed FLOAT,

    -- Extracted time components for time-aware queries
    -- hour_of_day: 0-23 (allows comparing "is this slow for 8 AM?")
    hour_of_day INTEGER NOT NULL,

    -- day_of_week: 0=Monday, 6=Sunday (allows weekday vs weekend patterns)
    day_of_week INTEGER NOT NULL,

    -- When this record was inserted
    created_at TIMESTAMPTZ DEFAULT NOW(),

    -- Ensure one record per cell per bucket
    UNIQUE(cell_id, bucket_time)
);

-- ----------------------------------------------------------------------------
-- Indexes
-- ----------------------------------------------------------------------------

-- Primary query pattern: get recent history for a specific cell
-- Used by: congestion calculation queries
CREATE INDEX idx_bucket_history_cell_time
ON bucket_history(cell_id, bucket_time DESC);

-- Time-of-day queries: compare current conditions to same hour historically
-- Used by: "is this slow for 8 AM?" comparisons
CREATE INDEX idx_bucket_history_cell_hour
ON bucket_history(cell_id, hour_of_day);

-- ----------------------------------------------------------------------------
-- Example Queries
-- ----------------------------------------------------------------------------

-- Get speed percentiles for a cell (last 7 days)
-- SELECT
--     PERCENTILE_CONT(0.25) WITHIN GROUP (ORDER BY avg_speed) as speed_p25,
--     PERCENTILE_CONT(0.50) WITHIN GROUP (ORDER BY avg_speed) as speed_p50
-- FROM bucket_history
-- WHERE cell_id = '882a100d63fffff'
--   AND avg_speed IS NOT NULL
--   AND bucket_time > NOW() - INTERVAL '7 days';

-- Get speed percentiles for same hour of day (last 30 days)
-- SELECT
--     PERCENTILE_CONT(0.25) WITHIN GROUP (ORDER BY avg_speed) as speed_p25
-- FROM bucket_history
-- WHERE cell_id = '882a100d63fffff'
--   AND hour_of_day = 8  -- 8 AM
--   AND avg_speed IS NOT NULL
--   AND bucket_time > NOW() - INTERVAL '30 days';

-- ----------------------------------------------------------------------------
-- Data Retention (Optional)
-- ----------------------------------------------------------------------------
-- To prevent unbounded growth, consider adding a scheduled job to delete
-- old records. Example: keep 90 days of history.
--
-- DELETE FROM bucket_history WHERE bucket_time < NOW() - INTERVAL '90 days';
