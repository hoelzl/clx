"""Tests for monitoring queries."""

import csv
import json
import tempfile
from pathlib import Path

import pytest

from clx_common.monitoring.monitor_db import MonitoringDB
from clx_common.monitoring.queries import MonitoringQueries
from clx_common.monitoring.schema import init_monitoring_database


@pytest.fixture
def monitoring_db():
    """Create a temporary monitoring database."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = Path(f.name)

    # Initialize database
    init_monitoring_database(db_path)

    # Get database instance
    db = MonitoringDB(db_path)

    yield db

    # Cleanup
    db.close()
    if db_path.exists():
        db_path.unlink()


@pytest.fixture
def queries(monitoring_db):
    """Create MonitoringQueries instance with test data."""
    # Get the database path from the MonitoringDB instance
    db_path = monitoring_db.db_path

    # Insert test data
    # Add workers
    monitoring_db.update_worker_state(
        worker_id=1,
        worker_type='notebook',
        container_id='container-1',
        status='busy',
        current_job_id=100,
        current_job_input_file='/path/to/notebook.ipynb'
    )
    monitoring_db.update_worker_state(
        worker_id=2,
        worker_type='notebook',
        container_id='container-2',
        status='idle'
    )
    monitoring_db.update_worker_state(
        worker_id=3,
        worker_type='drawio',
        container_id='container-3',
        status='busy',
        current_job_id=101,
        current_job_input_file='/path/to/diagram.drawio'
    )

    # Update worker stats
    monitoring_db.update_worker_stats(
        worker_id=1,
        jobs_processed_delta=10,
        processing_time_seconds=12.5
    )
    monitoring_db.update_worker_stats(
        worker_id=2,
        jobs_processed_delta=15,
        jobs_failed_delta=2,
        processing_time_seconds=10.0
    )
    monitoring_db.update_worker_stats(
        worker_id=3,
        jobs_processed_delta=8,
        processing_time_seconds=5.0
    )

    # Add jobs - first insert as pending, then update to processing to set started_at
    monitoring_db.update_job_state(
        job_id=100,
        job_type='notebook',
        status='pending',
        input_file='/path/to/notebook.ipynb',
        output_file='/path/to/notebook.html',
        correlation_id='corr-123'
    )
    monitoring_db.update_job_state(
        job_id=100,
        job_type='notebook',
        status='processing',
        input_file='/path/to/notebook.ipynb',
        output_file='/path/to/notebook.html',
        worker_id=1,
        correlation_id='corr-123'
    )

    monitoring_db.update_job_state(
        job_id=101,
        job_type='drawio',
        status='pending',
        input_file='/path/to/diagram.drawio',
        output_file='/path/to/diagram.png',
        correlation_id='corr-456'
    )
    monitoring_db.update_job_state(
        job_id=101,
        job_type='drawio',
        status='processing',
        input_file='/path/to/diagram.drawio',
        output_file='/path/to/diagram.png',
        worker_id=3,
        correlation_id='corr-456'
    )
    monitoring_db.update_job_state(
        job_id=102,
        job_type='notebook',
        status='pending',
        input_file='/path/to/notebook2.ipynb',
        output_file='/path/to/notebook2.html'
    )
    monitoring_db.update_job_state(
        job_id=103,
        job_type='notebook',
        status='completed',
        input_file='/path/to/notebook3.ipynb',
        output_file='/path/to/notebook3.html',
        worker_id=2
    )
    monitoring_db.update_job_state(
        job_id=104,
        job_type='drawio',
        status='failed',
        input_file='/path/to/diagram2.drawio',
        output_file='/path/to/diagram2.png',
        worker_id=3,
        error_message='Conversion failed'
    )

    # Add events
    monitoring_db.insert_event(
        event_type='job_submitted',
        event_category='job',
        severity='info',
        job_id=100,
        correlation_id='corr-123',
        message='Job 100 submitted'
    )
    monitoring_db.insert_event(
        event_type='job_submitted',
        event_category='job',
        severity='info',
        job_id=101,
        correlation_id='corr-456',
        message='Job 101 submitted'
    )
    monitoring_db.insert_event(
        event_type='worker_started',
        event_category='worker',
        severity='info',
        worker_id=1,
        message='Worker 1 started'
    )
    monitoring_db.insert_event(
        event_type='job_failed',
        event_category='job',
        severity='error',
        job_id=104,
        worker_id=3,
        message='Job 104 failed',
        event_data={'error': 'Conversion failed'}
    )

    # Create queries instance
    q = MonitoringQueries(db_path)

    yield q

    # Cleanup
    q.close()


class TestActiveJobs:
    """Tests for get_active_jobs query (UC-1)."""

    def test_get_active_jobs_returns_processing_jobs(self, queries):
        """Test that get_active_jobs returns currently processing jobs."""
        result = queries.get_active_jobs()

        assert len(result) == 2
        job_ids = {job['job_id'] for job in result}
        assert 100 in job_ids
        assert 101 in job_ids

    def test_get_active_jobs_includes_duration(self, queries):
        """Test that active jobs include duration in seconds."""
        result = queries.get_active_jobs()

        for job in result:
            assert 'duration_seconds' in job
            assert isinstance(job['duration_seconds'], int)
            assert job['duration_seconds'] >= 0

    def test_get_active_jobs_ordered_by_duration(self, queries):
        """Test that active jobs are ordered by duration (longest first)."""
        result = queries.get_active_jobs()

        # Should be ordered by duration descending
        if len(result) > 1:
            for i in range(len(result) - 1):
                assert result[i]['duration_seconds'] >= result[i + 1]['duration_seconds']

    def test_get_active_jobs_includes_required_fields(self, queries):
        """Test that active jobs include all required fields."""
        result = queries.get_active_jobs()

        if result:
            job = result[0]
            assert 'job_id' in job
            assert 'job_type' in job
            assert 'worker_id' in job
            assert 'input_file' in job
            assert 'duration_seconds' in job
            assert 'started_at' in job


class TestServiceHealth:
    """Tests for get_service_health query (UC-2)."""

    def test_get_service_health_returns_worker_counts(self, queries):
        """Test that service health includes worker counts."""
        result = queries.get_service_health()

        assert 'workers' in result
        workers = result['workers']

        assert workers['total'] == 3
        assert workers['idle'] == 1
        assert workers['busy'] == 2
        assert workers['hung'] == 0
        assert workers['dead'] == 0

    def test_get_service_health_returns_job_counts(self, queries):
        """Test that service health includes job counts."""
        result = queries.get_service_health()

        assert 'jobs' in result
        jobs = result['jobs']

        # These counts are for last hour only
        assert 'pending' in jobs
        assert 'processing' in jobs
        assert 'failed' in jobs

    def test_get_service_health_includes_timestamp(self, queries):
        """Test that service health includes timestamp."""
        result = queries.get_service_health()

        assert 'timestamp' in result
        assert isinstance(result['timestamp'], str)

    def test_get_service_health_handles_no_workers(self):
        """Test that service health handles empty database gracefully."""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = Path(f.name)

        init_monitoring_database(db_path)
        q = MonitoringQueries(db_path)

        result = q.get_service_health()

        assert result['workers']['total'] == 0
        assert result['workers']['idle'] == 0
        assert result['jobs']['pending'] == 0

        q.close()
        db_path.unlink()


class TestJobHistory:
    """Tests for get_job_history query (UC-3)."""

    def test_get_job_history_returns_job_and_events(self, queries):
        """Test that job history includes both job state and events."""
        result = queries.get_job_history(100)

        assert 'job' in result
        assert 'events' in result

    def test_get_job_history_includes_job_details(self, queries):
        """Test that job history includes complete job details."""
        result = queries.get_job_history(100)

        job = result['job']
        assert job is not None
        assert job['job_id'] == 100
        assert job['job_type'] == 'notebook'
        assert job['status'] == 'processing'

    def test_get_job_history_includes_events(self, queries):
        """Test that job history includes all events for the job."""
        result = queries.get_job_history(100)

        events = result['events']
        assert len(events) >= 1

        # Should include job_submitted event
        event_types = {e['event_type'] for e in events}
        assert 'job_submitted' in event_types

    def test_get_job_history_events_ordered_chronologically(self, queries):
        """Test that events are ordered chronologically."""
        result = queries.get_job_history(100)

        events = result['events']
        if len(events) > 1:
            for i in range(len(events) - 1):
                assert events[i]['event_time'] <= events[i + 1]['event_time']

    def test_get_job_history_nonexistent_job(self, queries):
        """Test that job history for nonexistent job returns None."""
        result = queries.get_job_history(99999)

        assert result['job'] is None
        assert result['events'] == []


class TestQueueDepth:
    """Tests for get_queue_depth query (UC-4)."""

    def test_get_queue_depth_returns_counts_by_type(self, queries):
        """Test that queue depth returns counts by job type."""
        result = queries.get_queue_depth()

        assert isinstance(result, dict)
        assert 'notebook' in result
        assert result['notebook'] == 1  # Job 102

    def test_get_queue_depth_only_includes_pending(self, queries):
        """Test that queue depth only counts pending jobs."""
        result = queries.get_queue_depth()

        # Should not include processing, completed, or failed jobs
        total_pending = sum(result.values())
        assert total_pending == 1

    def test_get_queue_depth_empty_queue(self):
        """Test queue depth with no pending jobs."""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = Path(f.name)

        init_monitoring_database(db_path)
        q = MonitoringQueries(db_path)

        result = q.get_queue_depth()

        assert result == {}

        q.close()
        db_path.unlink()


class TestWorkerPerformance:
    """Tests for get_worker_performance query (UC-5)."""

    def test_get_worker_performance_returns_all_workers(self, queries):
        """Test that worker performance returns all active workers."""
        result = queries.get_worker_performance()

        assert len(result) == 3
        worker_ids = {w['worker_id'] for w in result}
        assert worker_ids == {1, 2, 3}

    def test_get_worker_performance_includes_stats(self, queries):
        """Test that worker performance includes statistics."""
        result = queries.get_worker_performance()

        worker = result[0]
        assert 'worker_id' in worker
        assert 'worker_type' in worker
        assert 'status' in worker
        assert 'jobs_processed_count' in worker
        assert 'jobs_failed_count' in worker
        assert 'avg_processing_time' in worker
        assert 'failure_rate_percent' in worker
        assert 'uptime_hours' in worker

    def test_get_worker_performance_filters_by_type(self, queries):
        """Test filtering worker performance by worker type."""
        result = queries.get_worker_performance(worker_type='notebook')

        assert len(result) == 2
        for worker in result:
            assert worker['worker_type'] == 'notebook'

    def test_get_worker_performance_calculates_failure_rate(self, queries):
        """Test that failure rate is calculated correctly."""
        result = queries.get_worker_performance()

        # Find worker 2 which has failures
        worker_2 = next(w for w in result if w['worker_id'] == 2)

        # Worker 2 has 15 processed, 2 failed = 13.33% failure rate
        assert worker_2['failure_rate_percent'] is not None
        assert 13 <= worker_2['failure_rate_percent'] <= 14


class TestRecentEvents:
    """Tests for get_recent_events query (UC-6)."""

    def test_get_recent_events_returns_events(self, queries):
        """Test that recent events returns event list."""
        result = queries.get_recent_events(minutes=60)

        assert isinstance(result, list)
        assert len(result) >= 1

    def test_get_recent_events_includes_required_fields(self, queries):
        """Test that events include all required fields."""
        result = queries.get_recent_events()

        if result:
            event = result[0]
            assert 'event_time' in event
            assert 'event_type' in event
            assert 'event_category' in event
            assert 'severity' in event

    def test_get_recent_events_filters_by_category(self, queries):
        """Test filtering events by category."""
        result = queries.get_recent_events(category='job')

        assert len(result) >= 1
        for event in result:
            assert event['event_category'] == 'job'

    def test_get_recent_events_filters_by_severity(self, queries):
        """Test filtering events by severity."""
        result = queries.get_recent_events(severity='error')

        assert len(result) >= 1
        for event in result:
            assert event['severity'] == 'error'

    def test_get_recent_events_respects_limit(self, queries):
        """Test that event limit is respected."""
        result = queries.get_recent_events(limit=2)

        assert len(result) <= 2

    def test_get_recent_events_ordered_by_time_desc(self, queries):
        """Test that events are ordered newest first."""
        result = queries.get_recent_events()

        if len(result) > 1:
            for i in range(len(result) - 1):
                assert result[i]['event_time'] >= result[i + 1]['event_time']


class TestCorrelationTrace:
    """Tests for get_correlation_trace query (UC-7)."""

    def test_get_correlation_trace_returns_events_and_jobs(self, queries):
        """Test that correlation trace includes events and jobs."""
        result = queries.get_correlation_trace('corr-123')

        assert 'correlation_id' in result
        assert 'events' in result
        assert 'jobs' in result

    def test_get_correlation_trace_finds_related_events(self, queries):
        """Test that correlation trace finds all related events."""
        result = queries.get_correlation_trace('corr-123')

        events = result['events']
        assert len(events) >= 1

        # All events should have the same correlation
        for event in events:
            # The event should be related to job 100
            assert event['job_id'] == 100 or event.get('correlation_id') == 'corr-123'

    def test_get_correlation_trace_finds_related_jobs(self, queries):
        """Test that correlation trace finds all related jobs."""
        result = queries.get_correlation_trace('corr-123')

        jobs = result['jobs']
        assert len(jobs) == 1
        assert jobs[0]['job_id'] == 100

    def test_get_correlation_trace_events_ordered_chronologically(self, queries):
        """Test that correlation events are ordered chronologically."""
        result = queries.get_correlation_trace('corr-123')

        events = result['events']
        if len(events) > 1:
            for i in range(len(events) - 1):
                assert events[i]['event_time'] <= events[i + 1]['event_time']

    def test_get_correlation_trace_nonexistent_correlation(self, queries):
        """Test correlation trace for nonexistent correlation ID."""
        result = queries.get_correlation_trace('nonexistent')

        assert result['events'] == []
        assert result['jobs'] == []


class TestExportFunctionality:
    """Tests for export functionality."""

    def test_export_to_json_returns_string(self, queries):
        """Test that export_to_json returns JSON string."""
        data = {'test': 'data', 'number': 42}
        result = queries.export_to_json(data)

        assert isinstance(result, str)
        # Verify it's valid JSON
        parsed = json.loads(result)
        assert parsed == data

    def test_export_to_json_writes_to_file(self, queries):
        """Test that export_to_json writes to file."""
        data = {'test': 'data'}

        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            output_path = Path(f.name)

        queries.export_to_json(data, output_file=output_path)

        assert output_path.exists()
        content = json.loads(output_path.read_text())
        assert content == data

        output_path.unlink()

    def test_export_to_csv_creates_file(self, queries):
        """Test that export_to_csv creates CSV file."""
        data = [
            {'id': 1, 'name': 'test1'},
            {'id': 2, 'name': 'test2'}
        ]

        with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False) as f:
            output_path = Path(f.name)

        queries.export_to_csv(data, output_path)

        assert output_path.exists()

        # Verify CSV content
        with output_path.open('r') as f:
            reader = csv.DictReader(f)
            rows = list(reader)
            assert len(rows) == 2
            assert rows[0]['id'] == '1'
            assert rows[0]['name'] == 'test1'

        output_path.unlink()

    def test_export_to_csv_handles_empty_data(self, queries):
        """Test that export_to_csv handles empty data."""
        data = []

        with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False) as f:
            output_path = Path(f.name)

        queries.export_to_csv(data, output_path, fieldnames=['id', 'name'])

        assert output_path.exists()

        # Should have headers but no data
        with output_path.open('r') as f:
            reader = csv.DictReader(f)
            rows = list(reader)
            assert len(rows) == 0

        output_path.unlink()

    def test_export_to_csv_with_custom_fieldnames(self, queries):
        """Test export_to_csv with custom field order."""
        data = [
            {'name': 'test', 'id': 1, 'extra': 'ignored'},
        ]

        with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False) as f:
            output_path = Path(f.name)

        queries.export_to_csv(data, output_path, fieldnames=['id', 'name'])

        # Verify field order
        with output_path.open('r') as f:
            reader = csv.reader(f)
            headers = next(reader)
            assert headers == ['id', 'name']

        output_path.unlink()


class TestQueriesIntegration:
    """Integration tests for queries."""

    def test_workflow_diagnose_stuck_job(self, queries):
        """Test complete workflow for diagnosing stuck job."""
        # 1. Check active jobs
        active_jobs = queries.get_active_jobs()
        assert len(active_jobs) > 0

        # 2. Get details of first active job
        job_id = active_jobs[0]['job_id']
        job_history = queries.get_job_history(job_id)

        assert job_history['job'] is not None
        assert len(job_history['events']) >= 0

    def test_workflow_check_system_health(self, queries):
        """Test complete workflow for checking system health."""
        # Get overall health
        health = queries.get_service_health()

        assert 'workers' in health
        assert 'jobs' in health

        # Get worker performance details
        workers = queries.get_worker_performance()
        assert len(workers) > 0

        # Check queue depth
        queue = queries.get_queue_depth()
        assert isinstance(queue, dict)

    def test_workflow_investigate_failure(self, queries):
        """Test complete workflow for investigating failure."""
        # Find failed jobs via events
        failed_events = queries.get_recent_events(severity='error')
        assert len(failed_events) >= 1

        # Get job history for failed job
        job_id = failed_events[0]['job_id']
        if job_id:
            job_history = queries.get_job_history(job_id)
            assert job_history['job'] is not None

    def test_workflow_trace_correlation(self, queries):
        """Test complete workflow for tracing correlation."""
        # Get a correlation ID from recent events
        events = queries.get_recent_events()
        correlation_events = [e for e in events if e.get('job_id') == 100]

        if correlation_events:
            # Trace the correlation
            trace = queries.get_correlation_trace('corr-123')
            assert len(trace['events']) >= 1
            assert len(trace['jobs']) >= 1
