def latlon_to_cell(lat: float, lon: float, precision: int = 2) -> str:
    """
    Convert lat/lon to a grid cell id by rounding to a fixed precision.
    Example: 40.743, -73.989 -> "40.74_-73.99"
    """
    return f"{round(lat, precision):.2f}_{round(lon, precision):.2f}"
