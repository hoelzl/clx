"""CLX Monitoring System.

This package provides just-in-time monitoring capabilities for the CLX system,
replacing extensive logging with structured, queryable state tracking.

Key Components:
- schema: Database schema definition and initialization
- monitor_db: Database operations for monitoring data
- event_bus: Asynchronous event recording
- integration: Safe integration helpers for existing components
- queries: Pre-defined queries for common use cases
"""

__version__ = "0.3.0"

# Export key integration functions for easier importing
from clx_common.monitoring.integration import (
    enable_monitoring,
    disable_monitoring,
    is_monitoring_available,
)

__all__ = [
    'enable_monitoring',
    'disable_monitoring',
    'is_monitoring_available',
]
