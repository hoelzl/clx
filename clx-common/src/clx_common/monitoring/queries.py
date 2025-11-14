"""Pre-defined queries for common monitoring use cases.

This module provides a high-level query API for accessing monitoring data.
All queries are optimized for common diagnostic scenarios.
"""

import csv
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from clx_common.monitoring.monitor_db import MonitoringDB


class MonitoringQueries:
    """Pre-defined queries for common monitoring use cases.

    This class provides convenient methods for querying monitoring data
    for common diagnostic scenarios. Each method corresponds to a specific
    use case (UC-1 through UC-7).
    """

    def __init__(self, db_path: Path):
        """Initialize queries with database path.

        Args:
            db_path: Path to monitoring database
        """
        self.db = MonitoringDB(db_path)

    def close(self):
        """Close database connection."""
        self.db.close()

    # UC-1: Diagnose Stuck Job
    def get_active_jobs(self) -> List[Dict[str, Any]]:
        """Get all currently processing jobs with duration.

        This query helps diagnose stuck jobs by showing all active jobs
        and how long they've been running.

        Returns:
            List of dicts with keys: job_id, job_type, worker_id,
            input_file, duration_seconds, started_at
        """
        return self.db.query("""
            SELECT
                job_id,
                job_type,
                worker_id,
                input_file,
                CAST((julianday('now') - julianday(started_at)) * 86400 AS INTEGER)
                    as duration_seconds,
                started_at
            FROM job_state
            WHERE status = 'processing'
            ORDER BY duration_seconds DESC
        """)

    # UC-2: Check Service Health
    def get_service_health(self) -> Dict[str, Any]:
        """Get current service health overview.

        Returns comprehensive health snapshot including worker counts,
        queue depths, and overall status.

        Returns:
            Dict with 'workers', 'jobs', and 'timestamp' keys
        """
        workers = self.db.query_one("""
            SELECT
                COUNT(*) as total,
                SUM(CASE WHEN status = 'idle' THEN 1 ELSE 0 END) as idle,
                SUM(CASE WHEN status = 'busy' THEN 1 ELSE 0 END) as busy,
                SUM(CASE WHEN status = 'hung' THEN 1 ELSE 0 END) as hung,
                SUM(CASE WHEN status = 'dead' THEN 1 ELSE 0 END) as dead
            FROM worker_state
            WHERE stopped_at IS NULL
        """)

        jobs = self.db.query_one("""
            SELECT
                SUM(CASE WHEN status = 'pending' THEN 1 ELSE 0 END) as pending,
                SUM(CASE WHEN status = 'processing' THEN 1 ELSE 0 END) as processing,
                SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) as failed
            FROM job_state
            WHERE created_at > datetime('now', '-1 hour')
        """)

        # Handle NULL results when no workers/jobs exist
        if workers is None:
            workers = {
                'total': 0, 'idle': 0, 'busy': 0, 'hung': 0, 'dead': 0
            }
        if jobs is None:
            jobs = {'pending': 0, 'processing': 0, 'failed': 0}

        # Convert None to 0 for counts
        for key in workers:
            if workers[key] is None:
                workers[key] = 0
        for key in jobs:
            if jobs[key] is None:
                jobs[key] = 0

        return {
            'workers': workers,
            'jobs': jobs,
            'timestamp': datetime.now().isoformat()
        }

    # UC-3: Investigate Job Failure
    def get_job_history(self, job_id: int) -> Dict[str, Any]:
        """Get complete history of a job including all events.

        This query retrieves all information about a specific job,
        including its state and timeline of events.

        Args:
            job_id: Job ID to investigate

        Returns:
            Dict with 'job' (job state) and 'events' (list of events) keys
        """
        job = self.db.query_one("""
            SELECT * FROM job_state WHERE job_id = ?
        """, (job_id,))

        events = self.db.query("""
            SELECT
                event_time,
                event_type,
                severity,
                message,
                event_data
            FROM events
            WHERE job_id = ?
            ORDER BY event_time ASC
        """, (job_id,))

        return {
            'job': job,
            'events': events
        }

    # UC-4: Monitor Queue Depth
    def get_queue_depth(self) -> Dict[str, int]:
        """Get pending job counts by job type.

        Returns a breakdown of pending jobs by type to help identify
        queue bottlenecks.

        Returns:
            Dict mapping job_type to pending count
        """
        results = self.db.query("""
            SELECT job_type, COUNT(*) as count
            FROM job_state
            WHERE status = 'pending'
            GROUP BY job_type
        """)

        return {row['job_type']: row['count'] for row in results}

    # UC-5: Track Worker Performance
    def get_worker_performance(
        self,
        worker_type: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Get performance statistics for workers.

        Returns detailed performance metrics for all active workers,
        optionally filtered by worker type.

        Args:
            worker_type: Optional filter by worker type

        Returns:
            List of dicts with worker stats including performance metrics
        """
        where_clause = "WHERE stopped_at IS NULL"
        params = None

        if worker_type:
            where_clause += " AND worker_type = ?"
            params = (worker_type,)

        return self.db.query(f"""
            SELECT
                worker_id,
                worker_type,
                status,
                jobs_processed_count,
                jobs_failed_count,
                ROUND(avg_processing_time_seconds, 2) as avg_processing_time,
                ROUND(
                    CAST(jobs_failed_count AS REAL) /
                    NULLIF(jobs_processed_count, 0) * 100,
                    2
                ) as failure_rate_percent,
                CAST((julianday('now') - julianday(started_at)) * 24 AS INTEGER)
                    as uptime_hours
            FROM worker_state
            {where_clause}
            ORDER BY worker_type, worker_id
        """, params)

    # UC-6: Analyze System Events
    def get_recent_events(
        self,
        minutes: int = 60,
        category: Optional[str] = None,
        severity: Optional[str] = None,
        limit: int = 100
    ) -> List[Dict[str, Any]]:
        """Get recent system events with optional filtering.

        Retrieves recent events from the monitoring system with flexible
        filtering options.

        Args:
            minutes: How many minutes back to query
            category: Optional event category filter
            severity: Optional severity filter
            limit: Maximum number of events

        Returns:
            List of event dicts ordered by time (newest first)
        """
        where_clauses = [
            f"event_time > datetime('now', '-{minutes} minutes')"
        ]
        params = []

        if category:
            where_clauses.append("event_category = ?")
            params.append(category)
        if severity:
            where_clauses.append("severity = ?")
            params.append(severity)

        where_sql = " AND ".join(where_clauses)

        return self.db.query(f"""
            SELECT
                event_time,
                event_type,
                event_category,
                severity,
                worker_id,
                job_id,
                message,
                event_data
            FROM events
            WHERE {where_sql}
            ORDER BY event_time DESC
            LIMIT {limit}
        """, tuple(params) if params else None)

    # UC-7: Debug Correlation Flow
    def get_correlation_trace(self, correlation_id: str) -> Dict[str, Any]:
        """Get all events and jobs related to a correlation ID.

        Traces the complete flow of a request through the system by
        following its correlation ID.

        Args:
            correlation_id: Correlation ID to trace

        Returns:
            Dict with 'correlation_id', 'events', and 'jobs' keys
        """
        events = self.db.query("""
            SELECT
                event_time,
                event_type,
                event_category,
                job_id,
                worker_id,
                message
            FROM events
            WHERE correlation_id = ?
            ORDER BY event_time ASC
        """, (correlation_id,))

        jobs = self.db.query("""
            SELECT
                job_id,
                job_type,
                status,
                worker_id,
                created_at,
                completed_at,
                processing_time_seconds
            FROM job_state
            WHERE correlation_id = ?
            ORDER BY created_at ASC
        """, (correlation_id,))

        return {
            'correlation_id': correlation_id,
            'events': events,
            'jobs': jobs
        }

    # Export functionality
    def export_to_json(
        self,
        data: Any,
        output_file: Optional[Path] = None,
        indent: int = 2
    ) -> str:
        """Export data to JSON format.

        Args:
            data: Data to export (dict, list, etc.)
            output_file: Optional file path to write to
            indent: JSON indentation level

        Returns:
            JSON string
        """
        json_str = json.dumps(data, indent=indent, default=str)

        if output_file:
            output_file.write_text(json_str)

        return json_str

    def export_to_csv(
        self,
        data: List[Dict[str, Any]],
        output_file: Path,
        fieldnames: Optional[List[str]] = None
    ):
        """Export list of dicts to CSV format.

        Args:
            data: List of dictionaries to export
            output_file: File path to write to
            fieldnames: Optional list of field names (inferred if not provided)
        """
        if not data:
            # Write empty CSV with headers if data is empty
            if fieldnames:
                with output_file.open('w', newline='') as f:
                    writer = csv.DictWriter(f, fieldnames=fieldnames)
                    writer.writeheader()
            return

        # Infer fieldnames from first row if not provided
        if fieldnames is None:
            fieldnames = list(data[0].keys())

        with output_file.open('w', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction='ignore')
            writer.writeheader()
            writer.writerows(data)
