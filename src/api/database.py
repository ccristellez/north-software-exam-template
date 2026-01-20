"""
Database connection for Supabase PostgreSQL.

We use Supabase to store bucket history because:
- Historical data should survive restarts
- Redis is better for ephemeral real-time data (current bucket counts/speeds)
- PostgreSQL gives us durability and powerful percentile queries
"""
import os
from datetime import datetime, timezone

from dotenv import load_dotenv
from sqlalchemy import create_engine, Column, String, Float, Integer, DateTime
from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy.dialects.postgresql import TIMESTAMP

# Load environment variables from .env file
load_dotenv()

# Database URL loaded from environment variable
# Set SUPABASE_DATABASE_URL in your .env file or environment
DATABASE_URL = os.getenv("SUPABASE_DATABASE_URL")

# Create engine and session factory
# We only create these if DATABASE_URL is set (allows tests to run without DB)
engine = None
SessionLocal = None

if DATABASE_URL:
    engine = create_engine(DATABASE_URL)
    SessionLocal = sessionmaker(bind=engine)

# Base class for our models
Base = declarative_base()


class BucketHistory(Base):
    """
    Stores completed 5-minute bucket data for percentile-based congestion detection.

    Instead of computing running statistics (mean/variance), we store raw bucket data
    and use SQL percentile queries. This approach is:
    - Easier to understand and explain
    - Debuggable (can see exact historical data)
    - Supports time-of-day filtering (rush hour vs. midnight)

    See docs/schema.sql for full DDL and example queries.
    """
    __tablename__ = "bucket_history"

    id = Column(Integer, primary_key=True, autoincrement=True)
    cell_id = Column(String(20), nullable=False)
    bucket_time = Column(DateTime(timezone=True), nullable=False)
    vehicle_count = Column(Integer, nullable=False)
    avg_speed = Column(Float, nullable=True)  # NULL if no speed data
    hour_of_day = Column(Integer, nullable=False)  # 0-23
    day_of_week = Column(Integer, nullable=False)  # 0=Monday, 6=Sunday
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


def get_db_session():
    """
    Get a database session.

    Usage:
        session = get_db_session()
        if session:
            # do database stuff
            session.close()

    Returns None if database is not configured (useful for tests).
    """
    if SessionLocal is None:
        return None
    return SessionLocal()


def is_database_configured():
    """Check if database connection is configured."""
    return DATABASE_URL is not None and engine is not None
