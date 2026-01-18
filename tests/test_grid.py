"""
Unit tests for grid module (H3 hexagonal grid system).
"""
import pytest
import h3
from src.api.grid import (
    latlon_to_cell,
    get_neighbor_cells,
    cell_to_latlon,
    H3_RESOLUTION
)


@pytest.mark.unit
class TestLatLonToCell:
    """Test suite for latlon_to_cell function."""

    def test_latlon_to_cell_valid_coordinates(self):
        """Test conversion of valid lat/lon to H3 cell ID."""
        lat, lon = 40.7128, -74.0060  # New York City
        cell_id = latlon_to_cell(lat, lon)

        assert isinstance(cell_id, str)
        assert len(cell_id) == 15  # H3 cell ID length
        assert h3.is_valid_cell(cell_id)

    def test_latlon_to_cell_resolution(self):
        """Test that cell ID uses correct resolution."""
        lat, lon = 51.5074, -0.1278  # London
        cell_id = latlon_to_cell(lat, lon)

        resolution = h3.get_resolution(cell_id)
        assert resolution == H3_RESOLUTION
        assert resolution == 8

    def test_latlon_to_cell_same_location_same_cell(self):
        """Test that same coordinates return same cell ID."""
        lat, lon = 35.6762, 139.6503  # Tokyo

        cell_id1 = latlon_to_cell(lat, lon)
        cell_id2 = latlon_to_cell(lat, lon)

        assert cell_id1 == cell_id2

    def test_latlon_to_cell_nearby_locations_different_cells(self):
        """Test that nearby but different locations may return different cells."""
        # Two points about 500m apart (should be in different cells at resolution 8)
        lat1, lon1 = 40.7128, -74.0060
        lat2, lon2 = 40.7178, -74.0060  # ~500m north

        cell_id1 = latlon_to_cell(lat1, lon1)
        cell_id2 = latlon_to_cell(lat2, lon2)

        # May be different cells, but both should be valid
        assert h3.is_valid_cell(cell_id1)
        assert h3.is_valid_cell(cell_id2)

    def test_latlon_to_cell_equator(self):
        """Test conversion at equator."""
        lat, lon = 0.0, 0.0
        cell_id = latlon_to_cell(lat, lon)

        assert isinstance(cell_id, str)
        assert h3.is_valid_cell(cell_id)

    def test_latlon_to_cell_extreme_latitudes(self):
        """Test conversion at extreme latitudes."""
        # Near North Pole
        cell_id_north = latlon_to_cell(85.0, 0.0)
        assert h3.is_valid_cell(cell_id_north)

        # Near South Pole
        cell_id_south = latlon_to_cell(-85.0, 0.0)
        assert h3.is_valid_cell(cell_id_south)


@pytest.mark.unit
class TestGetNeighborCells:
    """Test suite for get_neighbor_cells function."""

    def test_get_neighbor_cells_k0(self):
        """Test k=0 returns only center cell."""
        cell_id = latlon_to_cell(40.7128, -74.0060)
        neighbors = get_neighbor_cells(cell_id, k=0)

        assert len(neighbors) == 1
        assert cell_id in neighbors

    def test_get_neighbor_cells_k1(self):
        """Test k=1 returns center + 6 neighbors = 7 cells."""
        cell_id = latlon_to_cell(40.7128, -74.0060)
        neighbors = get_neighbor_cells(cell_id, k=1)

        assert len(neighbors) == 7
        assert cell_id in neighbors

    def test_get_neighbor_cells_k2(self):
        """Test k=2 returns center + 2-ring = 19 cells."""
        cell_id = latlon_to_cell(40.7128, -74.0060)
        neighbors = get_neighbor_cells(cell_id, k=2)

        assert len(neighbors) == 19
        assert cell_id in neighbors

    def test_get_neighbor_cells_k3(self):
        """Test k=3 returns center + 3-ring = 37 cells."""
        cell_id = latlon_to_cell(40.7128, -74.0060)
        neighbors = get_neighbor_cells(cell_id, k=3)

        assert len(neighbors) == 37
        assert cell_id in neighbors

    def test_get_neighbor_cells_default_k(self):
        """Test default k=1 parameter."""
        cell_id = latlon_to_cell(40.7128, -74.0060)
        neighbors = get_neighbor_cells(cell_id)

        assert len(neighbors) == 7

    def test_get_neighbor_cells_all_valid(self):
        """Test that all returned cells are valid H3 cells."""
        cell_id = latlon_to_cell(40.7128, -74.0060)
        neighbors = get_neighbor_cells(cell_id, k=2)

        for neighbor in neighbors:
            assert h3.is_valid_cell(neighbor)
            assert h3.get_resolution(neighbor) == H3_RESOLUTION

    def test_get_neighbor_cells_returns_list(self):
        """Test that function returns a list."""
        cell_id = latlon_to_cell(40.7128, -74.0060)
        neighbors = get_neighbor_cells(cell_id, k=1)

        assert isinstance(neighbors, list)


@pytest.mark.unit
class TestCellToLatLon:
    """Test suite for cell_to_latlon function."""

    def test_cell_to_latlon_returns_tuple(self):
        """Test that function returns a tuple of two floats."""
        cell_id = latlon_to_cell(40.7128, -74.0060)
        result = cell_to_latlon(cell_id)

        assert isinstance(result, tuple)
        assert len(result) == 2
        assert isinstance(result[0], float)
        assert isinstance(result[1], float)

    def test_cell_to_latlon_approximate_roundtrip(self):
        """Test that converting lat/lon -> cell -> lat/lon returns approximately same coords."""
        original_lat, original_lon = 40.7128, -74.0060

        cell_id = latlon_to_cell(original_lat, original_lon)
        converted_lat, converted_lon = cell_to_latlon(cell_id)

        # Should be within ~460m (resolution 8 hexagon size)
        # Using 0.01 degrees (~1.1km) as tolerance
        assert abs(converted_lat - original_lat) < 0.01
        assert abs(converted_lon - original_lon) < 0.01

    def test_cell_to_latlon_valid_coordinates(self):
        """Test that returned coordinates are valid lat/lon values."""
        cell_id = latlon_to_cell(40.7128, -74.0060)
        lat, lon = cell_to_latlon(cell_id)

        # Valid latitude range: -90 to 90
        assert -90 <= lat <= 90
        # Valid longitude range: -180 to 180
        assert -180 <= lon <= 180

    def test_cell_to_latlon_multiple_cells(self):
        """Test conversion for multiple different cells."""
        locations = [
            (40.7128, -74.0060),  # New York
            (51.5074, -0.1278),   # London
            (35.6762, 139.6503),  # Tokyo
        ]

        for lat, lon in locations:
            cell_id = latlon_to_cell(lat, lon)
            converted_lat, converted_lon = cell_to_latlon(cell_id)

            assert isinstance(converted_lat, float)
            assert isinstance(converted_lon, float)
            assert -90 <= converted_lat <= 90
            assert -180 <= converted_lon <= 180


@pytest.mark.unit
class TestH3Resolution:
    """Test suite for H3_RESOLUTION constant."""

    def test_h3_resolution_value(self):
        """Test that H3_RESOLUTION is set to 8."""
        assert H3_RESOLUTION == 8

    def test_h3_resolution_is_valid(self):
        """Test that H3_RESOLUTION is a valid H3 resolution level."""
        # H3 supports resolutions 0-15
        assert 0 <= H3_RESOLUTION <= 15
