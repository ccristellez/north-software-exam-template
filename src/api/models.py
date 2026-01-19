from pydantic import BaseModel, Field
from datetime import datetime
from typing import Optional

class Ping(BaseModel):
    device_id: str = Field(..., min_length=1)
    timestamp: Optional[datetime] = Field(default=None)
    lat: float
    lon: float
    speed_kmh: Optional[float] = Field(default=None, ge=0, description="Speed in km/h from GPS")
