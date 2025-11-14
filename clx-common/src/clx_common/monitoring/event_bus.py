"""Monitoring event bus for asynchronous event recording.

This module provides an asynchronous event bus that records monitoring events
without blocking the main system. Events are queued and processed in the
background, ensuring monitoring never impacts job processing performance.
"""

import asyncio
import logging
import os
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Optional

from clx_common.monitoring.monitor_db import MonitoringDB

logger = logging.getLogger(__name__)


class EventCategory(str, Enum):
    """Event category enum."""
    WORKER = "worker"
    JOB = "job"
    SYSTEM = "system"
    HEALTH = "health"


class EventSeverity(str, Enum):
    """Event severity enum."""
    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


@dataclass
class MonitoringEvent:
    """Represents a monitoring event.

    This class encapsulates all information about a monitoring event,
    including its type, context, and associated data.
    """

    event_type: str
    category: EventCategory
    severity: EventSeverity = EventSeverity.INFO
    worker_id: Optional[int] = None
    job_id: Optional[int] = None
    correlation_id: Optional[str] = None
    message: Optional[str] = None
    event_data: Optional[Dict[str, Any]] = None
    event_time: Optional[datetime] = None

    def __post_init__(self):
        """Set event time if not provided."""
        if self.event_time is None:
            self.event_time = datetime.now()

        # Ensure event_data is a dict
        if self.event_data is None:
            self.event_data = {}


class MonitoringEventBus:
    """Asynchronous event bus for monitoring events.

    This class provides a non-blocking event recording system that:
    - Queues events asynchronously
    - Processes events in background task
    - Never blocks the caller
    - Catches and logs all errors without propagating
    - Can be enabled/disabled via configuration

    Design Principles:
    - Fail-safe: Errors never propagate to caller
    - Non-blocking: Uses asyncio.Queue for buffering
    - Lightweight: Minimal overhead when disabled
    """

    def __init__(self, db_path: Path, enabled: bool = True, queue_size: int = 1000):
        """Initialize monitoring event bus.

        Args:
            db_path: Path to monitoring database
            enabled: Whether monitoring is enabled
            queue_size: Maximum event queue size
        """
        self.db_path = db_path
        self.enabled = enabled
        self.queue_size = queue_size
        self._event_queue: Optional[asyncio.Queue] = None
        self._worker_task: Optional[asyncio.Task] = None
        self._db: Optional[MonitoringDB] = None
        self._running = False

    async def start(self):
        """Start the event processing worker."""
        if not self.enabled:
            logger.debug("Monitoring event bus is disabled")
            return

        if self._running:
            logger.warning("Event bus already running")
            return

        self._running = True
        self._event_queue = asyncio.Queue(maxsize=self.queue_size)
        self._db = MonitoringDB(self.db_path)

        # Start background worker task
        self._worker_task = asyncio.create_task(
            self._process_events(),
            name="monitoring_event_bus_worker"
        )

        logger.info("Monitoring event bus started")

    async def stop(self, timeout: float = 5.0):
        """Stop the event processing worker.

        Args:
            timeout: Maximum time to wait for queue to drain (seconds)
        """
        if not self._running:
            return

        logger.info("Stopping monitoring event bus...")
        self._running = False

        if self._event_queue is not None:
            # Send sentinel to wake up worker
            try:
                await asyncio.wait_for(
                    self._event_queue.put(None),
                    timeout=1.0
                )
            except asyncio.TimeoutError:
                logger.warning("Timeout sending stop sentinel to event queue")

        if self._worker_task is not None:
            try:
                await asyncio.wait_for(self._worker_task, timeout=timeout)
            except asyncio.TimeoutError:
                logger.warning(f"Worker task did not complete within {timeout}s")
                self._worker_task.cancel()
                try:
                    await self._worker_task
                except asyncio.CancelledError:
                    pass

        if self._db is not None:
            self._db.close()
            self._db = None

        logger.info("Monitoring event bus stopped")

    def record_event(self, event: MonitoringEvent):
        """Record a monitoring event (non-blocking).

        This method attempts to queue the event asynchronously. If the queue
        is full or any error occurs, the error is logged but not propagated.

        Args:
            event: Event to record
        """
        if not self.enabled or not self._running:
            return

        if self._event_queue is None:
            logger.debug("Event queue not initialized, ignoring event")
            return

        try:
            # Non-blocking put
            # If queue is full, we drop the event and log a warning
            try:
                self._event_queue.put_nowait(event)
            except asyncio.QueueFull:
                logger.warning(
                    f"Monitoring event queue full, dropping event: {event.event_type}"
                )
        except Exception as e:
            # Never propagate exceptions from monitoring
            logger.error(f"Failed to queue monitoring event: {e}")

    async def _process_events(self):
        """Background worker that writes events to database.

        This task runs continuously, pulling events from the queue and
        writing them to the database. It handles errors gracefully and
        never raises exceptions.
        """
        logger.debug("Event processing worker started")

        while self._running:
            try:
                # Wait for event (with timeout to check _running periodically)
                try:
                    event = await asyncio.wait_for(
                        self._event_queue.get(),
                        timeout=1.0
                    )
                except asyncio.TimeoutError:
                    # No event available, continue loop to check _running
                    continue

                # Check for sentinel (None = stop signal)
                if event is None:
                    logger.debug("Received stop sentinel")
                    break

                # Write event to database
                await self._write_event(event)

                # Mark task as done
                self._event_queue.task_done()

            except Exception as e:
                # Catch-all to ensure worker never dies
                logger.error(f"Error in event processing worker: {e}", exc_info=True)

        logger.debug("Event processing worker stopped")

    async def _write_event(self, event: MonitoringEvent):
        """Write event to database.

        This method writes the event and may also update related state tables
        based on the event type.

        Args:
            event: Event to write
        """
        try:
            if self._db is None:
                logger.error("Database not initialized")
                return

            # Run database operation in executor to avoid blocking
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None,
                self._write_event_sync,
                event
            )

        except Exception as e:
            # Never propagate exceptions
            logger.error(
                f"Failed to write event {event.event_type} to database: {e}",
                exc_info=True
            )

    def _write_event_sync(self, event: MonitoringEvent):
        """Synchronous method to write event to database.

        This is called from _write_event via run_in_executor.

        Args:
            event: Event to write
        """
        # Insert event into events table
        self._db.insert_event(
            event_type=event.event_type,
            event_category=event.category.value,
            severity=event.severity.value,
            worker_id=event.worker_id,
            job_id=event.job_id,
            correlation_id=event.correlation_id,
            message=event.message,
            event_data=event.event_data
        )

        # TODO: In Phase 3, we can add automatic state table updates
        # based on event type. For now, state updates will be done
        # explicitly via MonitoringDB methods.

    def get_queue_size(self) -> int:
        """Get current number of events in queue.

        Returns:
            Number of events waiting to be processed
        """
        if self._event_queue is None:
            return 0
        return self._event_queue.qsize()

    def is_running(self) -> bool:
        """Check if event bus is running.

        Returns:
            True if event bus is running
        """
        return self._running


# Global event bus instance
_event_bus: Optional[MonitoringEventBus] = None
_event_bus_lock = asyncio.Lock()


async def init_monitoring(
    db_path: Path,
    enabled: Optional[bool] = None,
    queue_size: int = 1000
):
    """Initialize global monitoring event bus.

    Args:
        db_path: Path to monitoring database
        enabled: Whether to enable monitoring (default: from env var)
        queue_size: Maximum event queue size
    """
    global _event_bus

    # Determine if monitoring is enabled
    if enabled is None:
        enabled = os.getenv('CLX_MONITORING_ENABLED', 'true').lower() in ('true', '1', 'yes')

    async with _event_bus_lock:
        if _event_bus is not None:
            logger.warning("Monitoring already initialized")
            return

        _event_bus = MonitoringEventBus(db_path, enabled=enabled, queue_size=queue_size)
        await _event_bus.start()


async def shutdown_monitoring(timeout: float = 5.0):
    """Shutdown global monitoring event bus.

    Args:
        timeout: Maximum time to wait for shutdown
    """
    global _event_bus

    async with _event_bus_lock:
        if _event_bus is not None:
            await _event_bus.stop(timeout=timeout)
            _event_bus = None


def record_event(event: MonitoringEvent):
    """Record a monitoring event using the global event bus.

    This is a convenience function that uses the global event bus instance.
    If monitoring is not initialized or disabled, this is a no-op.

    Args:
        event: Event to record
    """
    if _event_bus is not None:
        _event_bus.record_event(event)


def is_monitoring_enabled() -> bool:
    """Check if monitoring is enabled and running.

    Returns:
        True if monitoring is enabled
    """
    return _event_bus is not None and _event_bus.is_running()


# Convenience functions for common event types

def record_worker_event(
    event_type: str,
    worker_id: int,
    worker_type: str,
    status: Optional[str] = None,
    job_id: Optional[int] = None,
    severity: EventSeverity = EventSeverity.INFO,
    message: Optional[str] = None,
    **kwargs
):
    """Record a worker-related event.

    Args:
        event_type: Event type (e.g., 'worker_started', 'worker_stopped')
        worker_id: Worker ID
        worker_type: Worker type ('notebook', 'drawio', 'plantuml')
        status: Worker status ('idle', 'busy', 'hung', 'dead')
        job_id: Optional job ID if processing a job
        severity: Event severity
        message: Optional message
        **kwargs: Additional event data
    """
    event_data = {
        'worker_type': worker_type,
        **(kwargs if kwargs else {})
    }
    if status is not None:
        event_data['status'] = status

    record_event(MonitoringEvent(
        event_type=event_type,
        category=EventCategory.WORKER,
        severity=severity,
        worker_id=worker_id,
        job_id=job_id,
        message=message,
        event_data=event_data
    ))


def record_job_event(
    event_type: str,
    job_id: int,
    job_type: str,
    status: str,
    input_file: str,
    worker_id: Optional[int] = None,
    correlation_id: Optional[str] = None,
    severity: EventSeverity = EventSeverity.INFO,
    message: Optional[str] = None,
    error: Optional[str] = None,
    **kwargs
):
    """Record a job-related event.

    Args:
        event_type: Event type (e.g., 'job_submitted', 'job_completed')
        job_id: Job ID
        job_type: Job type ('notebook', 'drawio', 'plantuml')
        status: Job status ('pending', 'processing', 'completed', 'failed')
        input_file: Input file path
        worker_id: Optional worker ID processing the job
        correlation_id: Optional correlation ID for tracing
        severity: Event severity
        message: Optional message
        error: Optional error message
        **kwargs: Additional event data
    """
    event_data = {
        'job_type': job_type,
        'status': status,
        'input_file': input_file,
        **(kwargs if kwargs else {})
    }
    if error is not None:
        event_data['error'] = error

    record_event(MonitoringEvent(
        event_type=event_type,
        category=EventCategory.JOB,
        severity=severity,
        job_id=job_id,
        worker_id=worker_id,
        correlation_id=correlation_id,
        message=message,
        event_data=event_data
    ))


def record_system_event(
    event_type: str,
    severity: EventSeverity = EventSeverity.INFO,
    message: Optional[str] = None,
    **kwargs
):
    """Record a system-level event.

    Args:
        event_type: Event type (e.g., 'system_started', 'pool_started')
        severity: Event severity
        message: Optional message
        **kwargs: Additional event data
    """
    record_event(MonitoringEvent(
        event_type=event_type,
        category=EventCategory.SYSTEM,
        severity=severity,
        message=message,
        event_data=kwargs if kwargs else None
    ))


def record_health_event(
    event_type: str,
    overall_status: str,
    severity: EventSeverity = EventSeverity.INFO,
    message: Optional[str] = None,
    **kwargs
):
    """Record a health check event.

    Args:
        event_type: Event type (e.g., 'health_check_ok', 'health_check_warning')
        overall_status: Overall system status ('healthy', 'degraded', 'unhealthy')
        severity: Event severity
        message: Optional message
        **kwargs: Additional health data (worker counts, job counts, etc.)
    """
    event_data = {
        'overall_status': overall_status,
        **(kwargs if kwargs else {})
    }

    record_event(MonitoringEvent(
        event_type=event_type,
        category=EventCategory.HEALTH,
        severity=severity,
        message=message,
        event_data=event_data
    ))
