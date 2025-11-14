"""Helper functions for integrating monitoring into existing components.

This module provides safe wrapper functions that integrate monitoring
without risking the stability of the main system. All monitoring calls
are wrapped in try-except blocks and failures are logged but never propagated.
"""

import logging
import os
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Track whether monitoring is initialized
_monitoring_initialized = False
_monitoring_enabled = False


def is_monitoring_available() -> bool:
    """Check if monitoring is available and initialized.

    Returns:
        True if monitoring is available
    """
    global _monitoring_initialized, _monitoring_enabled

    if not _monitoring_enabled:
        return False

    if not _monitoring_initialized:
        # Try to initialize monitoring
        try:
            from clx_common.monitoring.event_bus import is_monitoring_enabled
            _monitoring_initialized = is_monitoring_enabled()
        except Exception as e:
            logger.debug(f"Monitoring not available: {e}")
            _monitoring_initialized = False

    return _monitoring_initialized


def enable_monitoring():
    """Enable monitoring integration.

    This should be called during system initialization if monitoring is desired.
    """
    global _monitoring_enabled
    _monitoring_enabled = True


def disable_monitoring():
    """Disable monitoring integration."""
    global _monitoring_enabled, _monitoring_initialized
    _monitoring_enabled = False
    _monitoring_initialized = False


def safe_record_worker_event(
    event_type: str,
    worker_id: int,
    worker_type: str,
    status: Optional[str] = None,
    job_id: Optional[int] = None,
    message: Optional[str] = None,
    **kwargs
):
    """Safely record a worker event.

    This function wraps event recording with error handling to ensure
    monitoring failures never impact worker operation.

    Args:
        event_type: Event type (e.g., 'worker_started', 'worker_stopped')
        worker_id: Worker ID
        worker_type: Worker type
        status: Optional worker status
        job_id: Optional job ID
        message: Optional message
        **kwargs: Additional event data
    """
    if not is_monitoring_available():
        return

    try:
        from clx_common.monitoring.event_bus import record_worker_event, EventSeverity

        # Determine severity based on event type
        severity = EventSeverity.INFO
        if 'error' in event_type or 'failed' in event_type:
            severity = EventSeverity.ERROR
        elif 'stopped' in event_type or 'dead' in event_type:
            severity = EventSeverity.WARNING

        record_worker_event(
            event_type=event_type,
            worker_id=worker_id,
            worker_type=worker_type,
            status=status,
            job_id=job_id,
            severity=severity,
            message=message,
            **kwargs
        )
    except Exception as e:
        # Never propagate monitoring errors
        logger.debug(f"Failed to record worker event: {e}")


def safe_record_job_event(
    event_type: str,
    job_id: int,
    job_type: str,
    status: str,
    input_file: str,
    worker_id: Optional[int] = None,
    correlation_id: Optional[str] = None,
    message: Optional[str] = None,
    error: Optional[str] = None,
    **kwargs
):
    """Safely record a job event.

    Args:
        event_type: Event type (e.g., 'job_submitted', 'job_completed')
        job_id: Job ID
        job_type: Job type
        status: Job status
        input_file: Input file path
        worker_id: Optional worker ID
        correlation_id: Optional correlation ID
        message: Optional message
        error: Optional error message
        **kwargs: Additional event data
    """
    if not is_monitoring_available():
        return

    try:
        from clx_common.monitoring.event_bus import record_job_event, EventSeverity

        # Determine severity
        severity = EventSeverity.INFO
        if status == 'failed' or error:
            severity = EventSeverity.ERROR
        elif status == 'completed':
            severity = EventSeverity.INFO

        record_job_event(
            event_type=event_type,
            job_id=job_id,
            job_type=job_type,
            status=status,
            input_file=input_file,
            worker_id=worker_id,
            correlation_id=correlation_id,
            severity=severity,
            message=message,
            error=error,
            **kwargs
        )
    except Exception as e:
        logger.debug(f"Failed to record job event: {e}")


def safe_record_system_event(
    event_type: str,
    message: Optional[str] = None,
    **kwargs
):
    """Safely record a system event.

    Args:
        event_type: Event type (e.g., 'pool_started', 'pool_stopped')
        message: Optional message
        **kwargs: Additional event data
    """
    if not is_monitoring_available():
        return

    try:
        from clx_common.monitoring.event_bus import record_system_event, EventSeverity

        severity = EventSeverity.INFO
        if 'error' in event_type or 'failed' in event_type:
            severity = EventSeverity.ERROR

        record_system_event(
            event_type=event_type,
            severity=severity,
            message=message,
            **kwargs
        )
    except Exception as e:
        logger.debug(f"Failed to record system event: {e}")


def safe_update_worker_state(
    db_path: Path,
    worker_id: int,
    worker_type: str,
    container_id: str,
    status: str,
    current_job_id: Optional[int] = None,
    current_job_input_file: Optional[str] = None,
    execution_mode: Optional[str] = None
):
    """Safely update worker state in monitoring database.

    Args:
        db_path: Path to monitoring database
        worker_id: Worker ID
        worker_type: Worker type
        container_id: Container ID
        status: Worker status
        current_job_id: Currently processing job ID
        current_job_input_file: Currently processing file
        execution_mode: Execution mode
    """
    if not is_monitoring_available():
        return

    try:
        from clx_common.monitoring.monitor_db import MonitoringDB
        from clx_common.monitoring.schema import get_monitoring_db_path

        monitoring_db_path = get_monitoring_db_path(db_path.parent)
        if not monitoring_db_path.exists():
            return

        db = MonitoringDB(monitoring_db_path)
        db.update_worker_state(
            worker_id=worker_id,
            worker_type=worker_type,
            container_id=container_id,
            status=status,
            current_job_id=current_job_id,
            current_job_input_file=current_job_input_file,
            execution_mode=execution_mode
        )
        db.close()
    except Exception as e:
        logger.debug(f"Failed to update worker state: {e}")


def safe_update_worker_stats(
    db_path: Path,
    worker_id: int,
    jobs_processed_delta: int = 0,
    jobs_failed_delta: int = 0,
    processing_time_seconds: Optional[float] = None
):
    """Safely update worker statistics.

    Args:
        db_path: Path to job queue database
        worker_id: Worker ID
        jobs_processed_delta: Number of jobs processed
        jobs_failed_delta: Number of jobs failed
        processing_time_seconds: Processing time
    """
    if not is_monitoring_available():
        return

    try:
        from clx_common.monitoring.monitor_db import MonitoringDB
        from clx_common.monitoring.schema import get_monitoring_db_path

        monitoring_db_path = get_monitoring_db_path(db_path.parent)
        if not monitoring_db_path.exists():
            return

        db = MonitoringDB(monitoring_db_path)
        db.update_worker_stats(
            worker_id=worker_id,
            jobs_processed_delta=jobs_processed_delta,
            jobs_failed_delta=jobs_failed_delta,
            processing_time_seconds=processing_time_seconds
        )
        db.close()
    except Exception as e:
        logger.debug(f"Failed to update worker stats: {e}")


def safe_update_job_state(
    db_path: Path,
    job_id: int,
    job_type: str,
    status: str,
    input_file: str,
    output_file: str,
    worker_id: Optional[int] = None,
    content_hash: Optional[str] = None,
    correlation_id: Optional[str] = None,
    priority: int = 0,
    error_message: Optional[str] = None,
    error_traceback: Optional[str] = None
):
    """Safely update job state in monitoring database.

    Args:
        db_path: Path to job queue database
        job_id: Job ID
        job_type: Job type
        status: Job status
        input_file: Input file path
        output_file: Output file path
        worker_id: Worker ID
        content_hash: Content hash
        correlation_id: Correlation ID
        priority: Job priority
        error_message: Error message if failed
        error_traceback: Error traceback if failed
    """
    if not is_monitoring_available():
        return

    try:
        from clx_common.monitoring.monitor_db import MonitoringDB
        from clx_common.monitoring.schema import get_monitoring_db_path

        monitoring_db_path = get_monitoring_db_path(db_path.parent)
        if not monitoring_db_path.exists():
            return

        db = MonitoringDB(monitoring_db_path)
        db.update_job_state(
            job_id=job_id,
            job_type=job_type,
            status=status,
            input_file=input_file,
            output_file=output_file,
            worker_id=worker_id,
            content_hash=content_hash,
            correlation_id=correlation_id,
            priority=priority,
            error_message=error_message,
            error_traceback=error_traceback
        )
        db.close()
    except Exception as e:
        logger.debug(f"Failed to update job state: {e}")
