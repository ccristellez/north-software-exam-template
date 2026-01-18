from pydantic import BaseModel, Field
from datetime import datetime
from typing import Optional

class Ping(BaseModel):
    device_id: str = Field(..., min_length=1)
    timestamp: Optional[datetime] = Field(default=None)
    lat: float
    lon: float
