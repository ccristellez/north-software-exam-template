from datetime import datetime, timezone

WINDOW_SECONDS = 300  # 5 minutes

def current_bucket(ts: datetime) -> int:
    """
    Returns a Unix timestamp bucket for the given time.
    """
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    epoch_seconds = int(ts.timestamp())
    return epoch_seconds // WINDOW_SECONDS
