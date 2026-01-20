from pydantic import BaseModel, Field
from datetime import datetime
from typing import Optional, List


class Ping(BaseModel):
    """Single device location ping."""
    device_id: str = Field(..., min_length=1)
    timestamp: Optional[datetime] = Field(default=None)
    lat: float
    lon: float
    speed_kmh: Optional[float] = Field(default=None, ge=0, description="Speed in km/h from GPS")


class BatchPingRequest(BaseModel):
    """Batch of pings for high-volume ingestion."""
    pings: List[Ping] = Field(..., min_length=1, max_length=1000, description="List of pings (max 1000)")
