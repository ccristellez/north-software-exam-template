# Tests

67 tests covering time utilities, H3 grid, models, and API endpoints.

## Run tests

```bash
pytest              # All tests
pytest -v           # Verbose
pytest -m unit      # Unit tests only (no Redis)
pytest tests/test_api.py::TestCongestionEndpoint  # Specific class
```

## Test structure

```
tests/
├── test_time_utils.py    # Time bucketing (7 tests)
├── test_grid.py          # H3 grid functions (19 tests)
├── test_models.py        # Pydantic validation (17 tests)
└── test_api.py           # API endpoints (24 tests)
```

## Coverage

- Time bucketing and timezone handling
- H3 cell conversion and neighbor finding
- Model validation and edge cases
- All API endpoints with mocked Redis
- Duplicate ping handling
- Threshold boundaries
