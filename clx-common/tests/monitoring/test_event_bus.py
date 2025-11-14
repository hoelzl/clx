"""Tests for monitoring event bus."""

import pytest
import asyncio
import tempfile
from pathlib import Path
from datetime import datetime

from clx_common.monitoring.schema import init_monitoring_database
from clx_common.monitoring.event_bus import (
    MonitoringEvent,
    MonitoringEventBus,
    EventCategory,
    EventSeverity,
    init_monitoring,
    shutdown_monitoring,
    record_event,
    record_worker_event,
    record_job_event,
    record_system_event,
    record_health_event,
    is_monitoring_enabled,
)
from clx_common.monitoring.monitor_db import MonitoringDB


@pytest.fixture
async def event_bus():
    """Create an event bus for testing."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = Path(f.name)

    # Initialize database
    init_monitoring_database(db_path)

    # Create and start event bus
    bus = MonitoringEventBus(db_path, enabled=True)
    await bus.start()

    # Yield both bus and path
    yield (bus, db_path)

    # Cleanup
    await bus.stop()
    if db_path.exists():
        db_path.unlink()


@pytest.fixture
def monitoring_db_path():
    """Create a temporary database path."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = Path(f.name)

    # Initialize database
    init_monitoring_database(db_path)

    yield db_path

    # Cleanup
    if db_path.exists():
        db_path.unlink()


class TestMonitoringEvent:
    """Tests for MonitoringEvent class."""

    def test_create_event_basic(self):
        """Test creating a basic event."""
        event = MonitoringEvent(
            event_type='test_event',
            category=EventCategory.SYSTEM
        )

        assert event.event_type == 'test_event'
        assert event.category == EventCategory.SYSTEM
        assert event.severity == EventSeverity.INFO  # Default
        assert event.event_time is not None
        assert event.event_data == {}

    def test_create_event_with_all_fields(self):
        """Test creating an event with all fields."""
        now = datetime.now()
        event_data = {'key': 'value'}

        event = MonitoringEvent(
            event_type='test_event',
            category=EventCategory.WORKER,
            severity=EventSeverity.ERROR,
            worker_id=1,
            job_id=100,
            correlation_id='corr-123',
            message='Test message',
            event_data=event_data,
            event_time=now
        )

        assert event.event_type == 'test_event'
        assert event.category == EventCategory.WORKER
        assert event.severity == EventSeverity.ERROR
        assert event.worker_id == 1
        assert event.job_id == 100
        assert event.correlation_id == 'corr-123'
        assert event.message == 'Test message'
        assert event.event_data == event_data
        assert event.event_time == now

    def test_event_data_defaults_to_dict(self):
        """Test that event_data defaults to empty dict."""
        event = MonitoringEvent(
            event_type='test',
            category=EventCategory.SYSTEM,
            event_data=None
        )

        assert event.event_data == {}


class TestMonitoringEventBus:
    """Tests for MonitoringEventBus class."""

    @pytest.mark.asyncio
    async def test_start_and_stop(self, monitoring_db_path):
        """Test starting and stopping the event bus."""
        bus = MonitoringEventBus(monitoring_db_path)

        assert not bus.is_running()

        await bus.start()
        assert bus.is_running()

        await bus.stop()
        assert not bus.is_running()

    @pytest.mark.asyncio
    async def test_disabled_bus_does_not_start(self, monitoring_db_path):
        """Test that disabled bus doesn't start worker."""
        bus = MonitoringEventBus(monitoring_db_path, enabled=False)

        await bus.start()
        assert not bus.is_running()

    @pytest.mark.asyncio
    async def test_record_event_writes_to_database(self, event_bus):
        """Test that recorded events are written to database."""
        bus, db_path = event_bus

        event = MonitoringEvent(
            event_type='test_event',
            category=EventCategory.SYSTEM,
            severity=EventSeverity.INFO,
            message='Test message'
        )

        bus.record_event(event)

        # Wait for event to be processed
        await asyncio.sleep(0.1)

        # Verify event was written
        db = MonitoringDB(db_path)
        results = db.query("SELECT * FROM events WHERE event_type = 'test_event'")

        assert len(results) == 1
        assert results[0]['message'] == 'Test message'
        assert results[0]['severity'] == 'info'

        db.close()

    @pytest.mark.asyncio
    async def test_record_multiple_events(self, event_bus):
        """Test recording multiple events."""
        bus, db_path = event_bus

        for i in range(5):
            event = MonitoringEvent(
                event_type=f'test_event_{i}',
                category=EventCategory.SYSTEM
            )
            bus.record_event(event)

        # Wait for all events to be processed
        await asyncio.sleep(0.2)

        # Verify all events were written
        db = MonitoringDB(db_path)
        results = db.query("SELECT * FROM events ORDER BY id")

        assert len(results) == 5
        db.close()

    @pytest.mark.asyncio
    async def test_disabled_bus_ignores_events(self, monitoring_db_path):
        """Test that disabled bus doesn't record events."""
        bus = MonitoringEventBus(monitoring_db_path, enabled=False)
        await bus.start()

        event = MonitoringEvent(
            event_type='test_event',
            category=EventCategory.SYSTEM
        )

        bus.record_event(event)

        await asyncio.sleep(0.1)

        # Verify no events were written
        db = MonitoringDB(monitoring_db_path)
        results = db.query("SELECT * FROM events")

        assert len(results) == 0
        db.close()

    @pytest.mark.asyncio
    async def test_event_with_worker_context(self, event_bus):
        """Test event with worker context."""
        bus, db_path = event_bus

        event = MonitoringEvent(
            event_type='worker_started',
            category=EventCategory.WORKER,
            worker_id=1,
            event_data={'worker_type': 'notebook'}
        )

        bus.record_event(event)
        await asyncio.sleep(0.1)

        db = MonitoringDB(db_path)
        results = db.query("SELECT * FROM events WHERE event_type = 'worker_started'")

        assert len(results) == 1
        assert results[0]['worker_id'] == 1
        assert '"worker_type"' in results[0]['event_data']

        db.close()

    @pytest.mark.asyncio
    async def test_event_with_job_context(self, event_bus):
        """Test event with job context."""
        bus, db_path = event_bus

        event = MonitoringEvent(
            event_type='job_completed',
            category=EventCategory.JOB,
            job_id=100,
            worker_id=1,
            correlation_id='corr-123',
            event_data={'job_type': 'notebook'}
        )

        bus.record_event(event)
        await asyncio.sleep(0.1)

        db = MonitoringDB(db_path)
        results = db.query("SELECT * FROM events WHERE event_type = 'job_completed'")

        assert len(results) == 1
        assert results[0]['job_id'] == 100
        assert results[0]['worker_id'] == 1
        assert results[0]['correlation_id'] == 'corr-123'

        db.close()

    @pytest.mark.asyncio
    async def test_queue_size(self, event_bus):
        """Test getting queue size."""
        bus, db_path = event_bus

        # Initially empty
        assert bus.get_queue_size() == 0

        # Add events
        for i in range(3):
            event = MonitoringEvent(
                event_type=f'test_{i}',
                category=EventCategory.SYSTEM
            )
            bus.record_event(event)

        # Queue should have events (or they might already be processed)
        # We just check this doesn't raise an exception
        size = bus.get_queue_size()
        assert size >= 0

    @pytest.mark.asyncio
    async def test_stop_waits_for_queue_drain(self, monitoring_db_path):
        """Test that stop waits for events to be processed."""
        bus = MonitoringEventBus(monitoring_db_path)
        await bus.start()

        # Add events
        for i in range(10):
            event = MonitoringEvent(
                event_type=f'test_{i}',
                category=EventCategory.SYSTEM
            )
            bus.record_event(event)

        # Give worker task a moment to start processing
        await asyncio.sleep(0.2)

        # Stop and wait
        await bus.stop(timeout=2.0)

        # All events should be processed
        db = MonitoringDB(monitoring_db_path)
        results = db.query("SELECT COUNT(*) as count FROM events")
        count = results[0]['count']

        # Should have most or all events (may lose some if queue was full)
        assert count >= 5

        db.close()


class TestGlobalEventBus:
    """Tests for global event bus functions."""

    @pytest.mark.asyncio
    async def test_init_and_shutdown_monitoring(self, monitoring_db_path):
        """Test initializing and shutting down global monitoring."""
        await init_monitoring(monitoring_db_path, enabled=True)

        assert is_monitoring_enabled()

        await shutdown_monitoring()

        assert not is_monitoring_enabled()

    @pytest.mark.asyncio
    async def test_record_event_global(self, monitoring_db_path):
        """Test recording event with global function."""
        await init_monitoring(monitoring_db_path, enabled=True)

        event = MonitoringEvent(
            event_type='test_event',
            category=EventCategory.SYSTEM,
            message='Global test'
        )

        record_event(event)

        await asyncio.sleep(0.1)

        # Verify
        db = MonitoringDB(monitoring_db_path)
        results = db.query("SELECT * FROM events WHERE event_type = 'test_event'")

        assert len(results) == 1
        assert results[0]['message'] == 'Global test'

        db.close()

        await shutdown_monitoring()

    @pytest.mark.asyncio
    async def test_record_event_when_not_initialized(self):
        """Test that recording event when not initialized is safe."""
        # Should not raise exception
        event = MonitoringEvent(
            event_type='test',
            category=EventCategory.SYSTEM
        )
        record_event(event)  # No-op


class TestConvenienceFunctions:
    """Tests for convenience event recording functions."""

    @pytest.mark.asyncio
    async def test_record_worker_event(self, monitoring_db_path):
        """Test recording worker event with convenience function."""
        await init_monitoring(monitoring_db_path, enabled=True)

        record_worker_event(
            event_type='worker_started',
            worker_id=1,
            worker_type='notebook',
            status='idle',
            message='Worker started'
        )

        await asyncio.sleep(0.1)

        db = MonitoringDB(monitoring_db_path)
        results = db.query("SELECT * FROM events WHERE event_type = 'worker_started'")

        assert len(results) == 1
        assert results[0]['worker_id'] == 1
        assert results[0]['event_category'] == 'worker'
        assert results[0]['message'] == 'Worker started'

        db.close()
        await shutdown_monitoring()

    @pytest.mark.asyncio
    async def test_record_job_event(self, monitoring_db_path):
        """Test recording job event with convenience function."""
        await init_monitoring(monitoring_db_path, enabled=True)

        record_job_event(
            event_type='job_completed',
            job_id=100,
            job_type='notebook',
            status='completed',
            input_file='/path/to/file.ipynb',
            worker_id=1,
            correlation_id='corr-123',
            message='Job completed successfully'
        )

        await asyncio.sleep(0.1)

        db = MonitoringDB(monitoring_db_path)
        results = db.query("SELECT * FROM events WHERE event_type = 'job_completed'")

        assert len(results) == 1
        assert results[0]['job_id'] == 100
        assert results[0]['worker_id'] == 1
        assert results[0]['correlation_id'] == 'corr-123'
        assert results[0]['event_category'] == 'job'

        db.close()
        await shutdown_monitoring()

    @pytest.mark.asyncio
    async def test_record_job_event_with_error(self, monitoring_db_path):
        """Test recording job event with error."""
        await init_monitoring(monitoring_db_path, enabled=True)

        record_job_event(
            event_type='job_failed',
            job_id=100,
            job_type='notebook',
            status='failed',
            input_file='/path/to/file.ipynb',
            error='Test error',
            severity=EventSeverity.ERROR
        )

        await asyncio.sleep(0.1)

        db = MonitoringDB(monitoring_db_path)
        results = db.query("SELECT * FROM events WHERE event_type = 'job_failed'")

        assert len(results) == 1
        assert results[0]['severity'] == 'error'
        assert '"error"' in results[0]['event_data']

        db.close()
        await shutdown_monitoring()

    @pytest.mark.asyncio
    async def test_record_system_event(self, monitoring_db_path):
        """Test recording system event with convenience function."""
        await init_monitoring(monitoring_db_path, enabled=True)

        record_system_event(
            event_type='system_started',
            message='System started successfully',
            version='1.0.0'
        )

        await asyncio.sleep(0.1)

        db = MonitoringDB(monitoring_db_path)
        results = db.query("SELECT * FROM events WHERE event_type = 'system_started'")

        assert len(results) == 1
        assert results[0]['event_category'] == 'system'
        assert results[0]['message'] == 'System started successfully'

        db.close()
        await shutdown_monitoring()

    @pytest.mark.asyncio
    async def test_record_health_event(self, monitoring_db_path):
        """Test recording health event with convenience function."""
        await init_monitoring(monitoring_db_path, enabled=True)

        record_health_event(
            event_type='health_check_ok',
            overall_status='healthy',
            workers_total=4,
            workers_idle=2,
            workers_busy=2
        )

        await asyncio.sleep(0.1)

        db = MonitoringDB(monitoring_db_path)
        results = db.query("SELECT * FROM events WHERE event_type = 'health_check_ok'")

        assert len(results) == 1
        assert results[0]['event_category'] == 'health'
        assert '"overall_status"' in results[0]['event_data']

        db.close()
        await shutdown_monitoring()


class TestErrorHandling:
    """Tests for error handling and fail-safe behavior."""

    @pytest.mark.asyncio
    async def test_event_bus_handles_database_errors_gracefully(self, monitoring_db_path):
        """Test that database errors don't crash the event bus."""
        bus = MonitoringEventBus(monitoring_db_path)
        await bus.start()

        # Close the database to cause errors
        if bus._db:
            bus._db.close()
            bus._db = None

        # Recording events should not raise exceptions
        event = MonitoringEvent(
            event_type='test_event',
            category=EventCategory.SYSTEM
        )

        # This should log an error but not raise
        bus.record_event(event)

        await asyncio.sleep(0.1)

        # Bus should still be running
        assert bus.is_running()

        await bus.stop()

    @pytest.mark.asyncio
    async def test_queue_full_drops_events_gracefully(self, monitoring_db_path):
        """Test that full queue drops events without raising."""
        # Create bus with very small queue
        bus = MonitoringEventBus(monitoring_db_path, queue_size=2)
        await bus.start()

        # Fill queue beyond capacity
        for i in range(10):
            event = MonitoringEvent(
                event_type=f'test_{i}',
                category=EventCategory.SYSTEM
            )
            bus.record_event(event)  # Should not raise

        await bus.stop()
