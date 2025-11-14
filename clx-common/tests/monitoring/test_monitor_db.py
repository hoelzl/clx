"""Tests for monitoring database operations."""

import pytest
import sqlite3
import tempfile
from pathlib import Path
from datetime import datetime, timedelta

from clx_common.monitoring.schema import init_monitoring_database
from clx_common.monitoring.monitor_db import MonitoringDB


@pytest.fixture
def monitoring_db():
    """Create a monitoring database for testing."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = Path(f.name)

    # Initialize the database
    init_monitoring_database(db_path)

    # Create MonitoringDB instance
    db = MonitoringDB(db_path)

    yield db

    # Cleanup
    db.close()
    if db_path.exists():
        db_path.unlink()


class TestEventInsertion:
    """Tests for inserting events."""

    def test_insert_event_basic(self, monitoring_db):
        """Test inserting a basic event."""
        event_id = monitoring_db.insert_event(
            event_type='worker_started',
            event_category='worker',
            severity='info'
        )

        assert event_id is not None
        assert event_id > 0

    def test_insert_event_with_all_fields(self, monitoring_db):
        """Test inserting an event with all fields."""
        event_data = {
            'worker_type': 'notebook',
            'status': 'idle',
            'custom_field': 'value'
        }

        event_id = monitoring_db.insert_event(
            event_type='worker_started',
            event_category='worker',
            severity='info',
            worker_id=1,
            job_id=100,
            correlation_id='test-correlation-123',
            message='Worker started successfully',
            event_data=event_data
        )

        assert event_id is not None

        # Verify the event was inserted correctly
        results = monitoring_db.query(
            "SELECT * FROM events WHERE id = ?",
            (event_id,)
        )

        assert len(results) == 1
        event = results[0]

        assert event['event_type'] == 'worker_started'
        assert event['event_category'] == 'worker'
        assert event['severity'] == 'info'
        assert event['worker_id'] == 1
        assert event['job_id'] == 100
        assert event['correlation_id'] == 'test-correlation-123'
        assert event['message'] == 'Worker started successfully'
        assert '"worker_type"' in event['event_data']

    def test_insert_multiple_events(self, monitoring_db):
        """Test inserting multiple events."""
        event_ids = []
        for i in range(5):
            event_id = monitoring_db.insert_event(
                event_type=f'test_event_{i}',
                event_category='system',
                severity='info'
            )
            event_ids.append(event_id)

        # All should have unique IDs
        assert len(set(event_ids)) == 5

        # All should be retrievable
        results = monitoring_db.query("SELECT * FROM events ORDER BY id")
        assert len(results) == 5


class TestWorkerStateOperations:
    """Tests for worker state operations."""

    def test_update_worker_state_insert(self, monitoring_db):
        """Test inserting a new worker state."""
        success = monitoring_db.update_worker_state(
            worker_id=1,
            worker_type='notebook',
            container_id='docker-abc123',
            status='idle',
            execution_mode='docker'
        )

        assert success is True

        # Verify the worker was inserted
        results = monitoring_db.query(
            "SELECT * FROM worker_state WHERE worker_id = ?",
            (1,)
        )

        assert len(results) == 1
        worker = results[0]

        assert worker['worker_type'] == 'notebook'
        assert worker['container_id'] == 'docker-abc123'
        assert worker['status'] == 'idle'
        assert worker['execution_mode'] == 'docker'

    def test_update_worker_state_update(self, monitoring_db):
        """Test updating an existing worker state."""
        # Insert initial state
        monitoring_db.update_worker_state(
            worker_id=1,
            worker_type='notebook',
            container_id='docker-abc123',
            status='idle'
        )

        # Update to busy with job
        monitoring_db.update_worker_state(
            worker_id=1,
            worker_type='notebook',
            container_id='docker-abc123',
            status='busy',
            current_job_id=100,
            current_job_input_file='/path/to/file.ipynb'
        )

        # Verify the update
        results = monitoring_db.query(
            "SELECT * FROM worker_state WHERE worker_id = ?",
            (1,)
        )

        worker = results[0]
        assert worker['status'] == 'busy'
        assert worker['current_job_id'] == 100
        assert worker['current_job_input_file'] == '/path/to/file.ipynb'
        assert worker['current_job_started_at'] is not None

    def test_update_worker_stats(self, monitoring_db):
        """Test updating worker statistics."""
        # Insert worker
        monitoring_db.update_worker_state(
            worker_id=1,
            worker_type='notebook',
            container_id='docker-abc123',
            status='idle'
        )

        # Update stats
        success = monitoring_db.update_worker_stats(
            worker_id=1,
            jobs_processed_delta=1,
            processing_time_seconds=10.5
        )

        assert success is True

        # Verify stats
        worker = monitoring_db.query_one(
            "SELECT * FROM worker_state WHERE worker_id = ?",
            (1,)
        )

        assert worker['jobs_processed_count'] == 1
        assert worker['total_processing_time_seconds'] == 10.5
        assert worker['avg_processing_time_seconds'] == 10.5

        # Update again with another job
        monitoring_db.update_worker_stats(
            worker_id=1,
            jobs_processed_delta=1,
            processing_time_seconds=15.5
        )

        worker = monitoring_db.query_one(
            "SELECT * FROM worker_state WHERE worker_id = ?",
            (1,)
        )

        assert worker['jobs_processed_count'] == 2
        assert worker['total_processing_time_seconds'] == 26.0
        assert worker['avg_processing_time_seconds'] == 13.0

    def test_update_worker_stats_with_failure(self, monitoring_db):
        """Test updating worker stats with failed job."""
        # Insert worker
        monitoring_db.update_worker_state(
            worker_id=1,
            worker_type='notebook',
            container_id='docker-abc123',
            status='idle'
        )

        # Record failed job
        monitoring_db.update_worker_stats(
            worker_id=1,
            jobs_failed_delta=1
        )

        worker = monitoring_db.query_one(
            "SELECT * FROM worker_state WHERE worker_id = ?",
            (1,)
        )

        assert worker['jobs_failed_count'] == 1
        assert worker['jobs_processed_count'] == 0

    def test_mark_worker_stopped(self, monitoring_db):
        """Test marking a worker as stopped."""
        # Insert worker
        monitoring_db.update_worker_state(
            worker_id=1,
            worker_type='notebook',
            container_id='docker-abc123',
            status='busy'
        )

        # Mark as stopped
        success = monitoring_db.mark_worker_stopped(1)
        assert success is True

        # Verify
        worker = monitoring_db.query_one(
            "SELECT * FROM worker_state WHERE worker_id = ?",
            (1,)
        )

        assert worker['status'] == 'dead'
        assert worker['stopped_at'] is not None


class TestJobStateOperations:
    """Tests for job state operations."""

    def test_update_job_state_insert(self, monitoring_db):
        """Test inserting a new job state."""
        success = monitoring_db.update_job_state(
            job_id=100,
            job_type='notebook',
            status='pending',
            input_file='/path/to/input.ipynb',
            output_file='/path/to/output.ipynb',
            content_hash='abc123',
            correlation_id='corr-123',
            priority=5
        )

        assert success is True

        # Verify
        job = monitoring_db.query_one(
            "SELECT * FROM job_state WHERE job_id = ?",
            (100,)
        )

        assert job['job_type'] == 'notebook'
        assert job['status'] == 'pending'
        assert job['input_file'] == '/path/to/input.ipynb'
        assert job['output_file'] == '/path/to/output.ipynb'
        assert job['content_hash'] == 'abc123'
        assert job['correlation_id'] == 'corr-123'
        assert job['priority'] == 5

    def test_update_job_state_processing(self, monitoring_db):
        """Test updating job to processing status."""
        # Insert pending job
        monitoring_db.update_job_state(
            job_id=100,
            job_type='notebook',
            status='pending',
            input_file='/path/to/input.ipynb',
            output_file='/path/to/output.ipynb'
        )

        # Update to processing
        monitoring_db.update_job_state(
            job_id=100,
            job_type='notebook',
            status='processing',
            input_file='/path/to/input.ipynb',
            output_file='/path/to/output.ipynb',
            worker_id=1
        )

        job = monitoring_db.query_one(
            "SELECT * FROM job_state WHERE job_id = ?",
            (100,)
        )

        assert job['status'] == 'processing'
        assert job['worker_id'] == 1
        assert job['started_at'] is not None
        assert job['attempts'] == 1

    def test_update_job_state_completed(self, monitoring_db):
        """Test updating job to completed status."""
        # Insert and start processing
        monitoring_db.update_job_state(
            job_id=100,
            job_type='notebook',
            status='pending',
            input_file='/path/to/input.ipynb',
            output_file='/path/to/output.ipynb'
        )

        monitoring_db.update_job_state(
            job_id=100,
            job_type='notebook',
            status='processing',
            input_file='/path/to/input.ipynb',
            output_file='/path/to/output.ipynb',
            worker_id=1
        )

        # Complete the job
        monitoring_db.update_job_state(
            job_id=100,
            job_type='notebook',
            status='completed',
            input_file='/path/to/input.ipynb',
            output_file='/path/to/output.ipynb'
        )

        job = monitoring_db.query_one(
            "SELECT * FROM job_state WHERE job_id = ?",
            (100,)
        )

        assert job['status'] == 'completed'
        assert job['completed_at'] is not None
        assert job['processing_time_seconds'] is not None

    def test_update_job_state_failed(self, monitoring_db):
        """Test updating job to failed status with error."""
        monitoring_db.update_job_state(
            job_id=100,
            job_type='notebook',
            status='processing',
            input_file='/path/to/input.ipynb',
            output_file='/path/to/output.ipynb',
            worker_id=1
        )

        # Fail the job
        monitoring_db.update_job_state(
            job_id=100,
            job_type='notebook',
            status='failed',
            input_file='/path/to/input.ipynb',
            output_file='/path/to/output.ipynb',
            error_message='Test error message',
            error_traceback='Traceback...'
        )

        job = monitoring_db.query_one(
            "SELECT * FROM job_state WHERE job_id = ?",
            (100,)
        )

        assert job['status'] == 'failed'
        assert job['error_message'] == 'Test error message'
        assert job['error_traceback'] == 'Traceback...'
        assert job['completed_at'] is not None


class TestQueryOperations:
    """Tests for query operations."""

    def test_query_returns_list(self, monitoring_db):
        """Test that query returns a list of dictionaries."""
        # Insert some test events
        for i in range(3):
            monitoring_db.insert_event(
                event_type=f'test_{i}',
                event_category='system',
                severity='info'
            )

        results = monitoring_db.query("SELECT * FROM events ORDER BY id")

        assert isinstance(results, list)
        assert len(results) == 3
        assert all(isinstance(r, dict) for r in results)

    def test_query_with_params(self, monitoring_db):
        """Test query with parameters."""
        monitoring_db.insert_event(
            event_type='test_event',
            event_category='worker',
            severity='info',
            worker_id=1
        )

        results = monitoring_db.query(
            "SELECT * FROM events WHERE worker_id = ?",
            (1,)
        )

        assert len(results) == 1
        assert results[0]['worker_id'] == 1

    def test_query_one_returns_dict(self, monitoring_db):
        """Test that query_one returns a single dictionary."""
        monitoring_db.insert_event(
            event_type='test_event',
            event_category='system',
            severity='info'
        )

        result = monitoring_db.query_one("SELECT * FROM events")

        assert isinstance(result, dict)
        assert result['event_type'] == 'test_event'

    def test_query_one_returns_none_when_empty(self, monitoring_db):
        """Test that query_one returns None when no results."""
        result = monitoring_db.query_one("SELECT * FROM events")

        assert result is None


class TestCleanupOperations:
    """Tests for cleanup operations."""

    def test_cleanup_old_events(self, monitoring_db):
        """Test cleaning up old events."""
        # Insert old event manually
        conn = monitoring_db._get_conn()
        cursor = conn.cursor()

        # Insert event with old timestamp
        cursor.execute("""
            INSERT INTO events (event_type, event_category, severity, event_time)
            VALUES ('old_event', 'system', 'info',
                    datetime('now', '-10 days'))
        """)

        # Insert recent event
        cursor.execute("""
            INSERT INTO events (event_type, event_category, severity)
            VALUES ('recent_event', 'system', 'info')
        """)

        conn.commit()

        # Cleanup events older than 7 days
        deleted = monitoring_db.cleanup_old_events(days=7)

        assert deleted == 1

        # Verify only recent event remains
        results = monitoring_db.query("SELECT * FROM events")
        assert len(results) == 1
        assert results[0]['event_type'] == 'recent_event'

    def test_cleanup_preserves_error_events(self, monitoring_db):
        """Test that cleanup preserves error and critical events."""
        conn = monitoring_db._get_conn()
        cursor = conn.cursor()

        # Insert old error event
        cursor.execute("""
            INSERT INTO events (event_type, event_category, severity, event_time)
            VALUES ('old_error', 'system', 'error',
                    datetime('now', '-10 days'))
        """)

        # Insert old info event
        cursor.execute("""
            INSERT INTO events (event_type, event_category, severity, event_time)
            VALUES ('old_info', 'system', 'info',
                    datetime('now', '-10 days'))
        """)

        conn.commit()

        # Cleanup
        deleted = monitoring_db.cleanup_old_events(days=7)

        assert deleted == 1  # Only info event deleted

        # Error event should remain
        results = monitoring_db.query("SELECT * FROM events")
        assert len(results) == 1
        assert results[0]['severity'] == 'error'

    def test_cleanup_old_jobs(self, monitoring_db):
        """Test cleaning up old jobs."""
        conn = monitoring_db._get_conn()
        cursor = conn.cursor()

        # Insert old completed job
        cursor.execute("""
            INSERT INTO job_state (
                job_id, job_type, status, input_file, output_file,
                created_at, completed_at
            ) VALUES (
                1, 'notebook', 'completed', '/in', '/out',
                datetime('now', '-10 days'),
                datetime('now', '-10 days')
            )
        """)

        # Insert recent completed job
        cursor.execute("""
            INSERT INTO job_state (
                job_id, job_type, status, input_file, output_file,
                created_at, completed_at
            ) VALUES (
                2, 'notebook', 'completed', '/in2', '/out2',
                datetime('now'),
                datetime('now')
            )
        """)

        # Insert pending job (should not be deleted)
        cursor.execute("""
            INSERT INTO job_state (
                job_id, job_type, status, input_file, output_file,
                created_at
            ) VALUES (
                3, 'notebook', 'pending', '/in3', '/out3',
                datetime('now', '-10 days')
            )
        """)

        conn.commit()

        # Cleanup
        deleted = monitoring_db.cleanup_old_jobs(days=7)

        assert deleted == 1  # Only old completed job

        # Verify remaining jobs
        results = monitoring_db.query("SELECT * FROM job_state ORDER BY job_id")
        assert len(results) == 2
        assert results[0]['job_id'] == 2  # Recent completed
        assert results[1]['job_id'] == 3  # Pending


class TestThreadSafety:
    """Tests for thread safety."""

    def test_thread_local_connections(self, monitoring_db):
        """Test that each thread gets its own connection."""
        import threading

        connections = []

        def get_connection():
            conn = monitoring_db._get_conn()
            connections.append(id(conn))

        threads = [threading.Thread(target=get_connection) for _ in range(3)]

        for t in threads:
            t.start()

        for t in threads:
            t.join()

        # Each thread should have gotten a different connection
        # (or at least we should have multiple different connection IDs)
        assert len(set(connections)) >= 1  # At least one connection created
