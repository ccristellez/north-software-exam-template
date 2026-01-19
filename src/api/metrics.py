"""
Prometheus metrics for monitoring API performance and behavior.
"""
from prometheus_client import Counter, Histogram, Gauge

# Request metrics
ping_requests_total = Counter(
    'ping_requests_total',
    'Total number of ping requests received',
    ['status']
)

congestion_requests_total = Counter(
    'congestion_requests_total',
    'Total number of congestion query requests',
    ['endpoint', 'status']
)

# Latency metrics
request_duration_seconds = Histogram(
    'request_duration_seconds',
    'Request latency in seconds',
    ['endpoint']
)

# Business metrics
unique_devices_per_bucket = Gauge(
    'unique_devices_per_bucket',
    'Number of unique devices in current time bucket',
    ['cell_id']
)

congestion_level_count = Counter(
    'congestion_level_count',
    'Count of congestion level classifications',
    ['level']
)

# Redis metrics
redis_operations_total = Counter(
    'redis_operations_total',
    'Total Redis operations',
    ['operation', 'status']
)
