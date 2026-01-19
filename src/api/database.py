"""
Database connection for Supabase PostgreSQL.

We use Supabase to store historical baselines because:
- Baselines are long-lived data that should survive restarts
- Redis is better for ephemeral real-time data (buckets, speeds)
- PostgreSQL gives us durability and easy querying

The connection URL is loaded from .env file (not committed to git).
"""
import os
from datetime import datetime, timezone
from sqlalchemy import create_engine, Column, String, Float, Integer, DateTime
from sqlalchemy.orm import declarative_base, sessionmaker
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Get database URL from environment
DATABASE_URL = os.getenv("DATABASE_URL")

# Create engine and session factory
# We only create these if DATABASE_URL is set (allows tests to run without DB)
engine = None
SessionLocal = None

if DATABASE_URL:
    engine = create_engine(DATABASE_URL)
    SessionLocal = sessionmaker(bind=engine)

# Base class for our models
Base = declarative_base()


class HexBaseline(Base):
    """
    Stores historical traffic baselines for each hexagon cell.

    Each cell learns what "normal" looks like over time:
    - avg_speed: typical speed in this cell
    - avg_count: typical vehicle count
    - variance values let us calculate standard deviation for Z-scores
    - sample_count tracks how much data we have
    """
    __tablename__ = "hex_baselines"

    cell_id = Column(String(20), primary_key=True)
    avg_speed = Column(Float, default=0)
    avg_count = Column(Float, default=0)
    speed_variance = Column(Float, default=0)
    count_variance = Column(Float, default=0)
    sample_count = Column(Integer, default=0)
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


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
