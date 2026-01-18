"""
Spatial indexing using H3 hexagonal grid system.
Resolution 8 = ~460m hexagon edge length (~0.74 km² area)
"""
import h3

# H3 resolution level
# 7 = ~1.2km edge (~5km² area)
# 8 = ~460m edge (~0.74km² area) ← RECOMMENDED for city traffic
# 9 = ~174m edge (~0.10km² area)
H3_RESOLUTION = 8


def latlon_to_cell(lat: float, lon: float) -> str:
    """
    Convert lat/lon to H3 hexagon cell ID.
    
    Args:
        lat: Latitude
        lon: Longitude
    
    Returns:
        H3 cell ID (e.g., "882a100897fffff")
    """
    # H3 v4+ uses latlng_to_cell instead of geo_to_h3
    return h3.latlng_to_cell(lat, lon, H3_RESOLUTION)


def get_neighbor_cells(cell_id: str, k: int = 1) -> list[str]:
    """
    Get all hexagons within k hops of the given cell.
    
    Args:
        cell_id: H3 cell ID
        k: Number of hops (1 = immediate neighbors, 2 = 2-ring, etc.)
    
    Returns:
        List of H3 cell IDs including the center cell
        
    Examples:
        k=0: 1 cell (just the center)
        k=1: 7 cells (center + 6 neighbors)
        k=2: 19 cells (center + 2-ring)
        k=3: 37 cells (center + 3-ring)
    """
    # H3 v4+ uses grid_disk instead of k_ring
    return list(h3.grid_disk(cell_id, k))


def cell_to_latlon(cell_id: str) -> tuple[float, float]:
    """
    Convert H3 cell ID back to lat/lon (center of hexagon).
    
    Args:
        cell_id: H3 cell ID
    
    Returns:
        Tuple of (lat, lon)
    """
    # H3 v4+ uses cell_to_latlng instead of h3_to_geo
    lat, lon = h3.cell_to_latlng(cell_id)
    return lat, lon