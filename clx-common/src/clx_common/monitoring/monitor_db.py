"""Monitoring database operations.

This module provides the MonitoringDB class for interacting with the
monitoring database, including inserting events, updating state tables,
and executing queries.
"""

import json
import logging
import socket
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class MonitoringDB:
    """Thread-safe monitoring database manager.

    This class provides methods for recording events and updating state
    tables in the monitoring database. It uses thread-local connections
    for thread safety.

    Note: This class is designed to be fail-safe. All methods catch exceptions
    and log errors rather than propagating them, ensuring monitoring failures
    don't impact the main system.
    """

    def __init__(self, db_path: Path, timeout: float = 30.0):
        """Initialize monitoring database manager.

        Args:
            db_path: Path to monitoring database file
            timeout: Database busy timeout in seconds
        """
        self.db_path = db_path
        self.timeout = timeout
        self._local = threading.local()
        self._hostname = socket.gethostname()

    def _get_conn(self) -> sqlite3.Connection:
        """Get thread-local database connection.

        Returns:
            SQLite connection object
        """
        if not hasattr(self._local, 'conn'):
            self._local.conn = sqlite3.connect(
                str(self.db_path),
                check_same_thread=False,
                timeout=self.timeout
            )
            self._local.conn.row_factory = sqlite3.Row
        return self._local.conn

    def insert_event(
        self,
        event_type: str,
        event_category: str,
        severity: str = "info",
        worker_id: Optional[int] = None,
        job_id: Optional[int] = None,
        correlation_id: Optional[str] = None,
        message: Optional[str] = None,
        event_data: Optional[Dict[str, Any]] = None
    ) -> Optional[int]:
        """Insert an event into the events table.

        Args:
            event_type: Type of event (e.g., 'worker_started', 'job_completed')
            event_category: Category ('worker', 'job', 'system', 'health')
            severity: Severity level ('debug', 'info', 'warning', 'error', 'critical')
            worker_id: Optional worker ID
            job_id: Optional job ID
            correlation_id: Optional correlation ID
            message: Optional human-readable message
            event_data: Optional event-specific data dictionary

        Returns:
            Event ID if successful, None if failed
        """
        try:
            conn = self._get_conn()
            cursor = conn.cursor()

            event_data_json = json.dumps(event_data) if event_data else None
            process_id = threading.get_ident()

            cursor.execute("""
                INSERT INTO events (
                    event_type, event_category, severity,
                    worker_id, job_id, correlation_id,
                    event_data, message, hostname, process_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                event_type, event_category, severity,
                worker_id, job_id, correlation_id,
                event_data_json, message, self._hostname, process_id
            ))

            conn.commit()
            return cursor.lastrowid

        except Exception as e:
            logger.error(f"Failed to insert event: {e}")
            return None

    def update_worker_state(
        self,
        worker_id: int,
        worker_type: str,
        container_id: str,
        status: str,
        current_job_id: Optional[int] = None,
        current_job_input_file: Optional[str] = None,
        execution_mode: Optional[str] = None
    ) -> bool:
        """Update or insert worker state.

        Args:
            worker_id: Worker ID
            worker_type: Worker type ('notebook', 'drawio', 'plantuml')
            container_id: Container/process ID
            status: Worker status ('idle', 'busy', 'hung', 'dead')
            current_job_id: Currently processing job ID
            current_job_input_file: Currently processing file
            execution_mode: Execution mode ('docker', 'direct')

        Returns:
            True if successful, False otherwise
        """
        try:
            conn = self._get_conn()
            cursor = conn.cursor()

            # Check if worker exists
            cursor.execute("SELECT worker_id FROM worker_state WHERE worker_id = ?", (worker_id,))
            exists = cursor.fetchone() is not None

            if exists:
                # Update existing worker
                cursor.execute("""
                    UPDATE worker_state
                    SET status = ?,
                        last_status_change = CURRENT_TIMESTAMP,
                        current_job_id = ?,
                        current_job_started_at = CASE
                            WHEN ? IS NOT NULL AND current_job_id IS NULL
                                THEN CURRENT_TIMESTAMP
                            WHEN ? IS NULL
                                THEN NULL
                            ELSE current_job_started_at
                        END,
                        current_job_input_file = ?,
                        last_heartbeat = CURRENT_TIMESTAMP
                    WHERE worker_id = ?
                """, (status, current_job_id, current_job_id, current_job_id,
                      current_job_input_file, worker_id))
            else:
                # Insert new worker
                cursor.execute("""
                    INSERT INTO worker_state (
                        worker_id, worker_type, container_id, status,
                        current_job_id, current_job_started_at, current_job_input_file,
                        execution_mode
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    worker_id, worker_type, container_id, status,
                    current_job_id,
                    datetime.now() if current_job_id else None,
                    current_job_input_file,
                    execution_mode
                ))

            conn.commit()
            return True

        except Exception as e:
            logger.error(f"Failed to update worker state for worker {worker_id}: {e}")
            return False

    def update_worker_stats(
        self,
        worker_id: int,
        jobs_processed_delta: int = 0,
        jobs_failed_delta: int = 0,
        processing_time_seconds: Optional[float] = None
    ) -> bool:
        """Update worker statistics.

        Args:
            worker_id: Worker ID
            jobs_processed_delta: Number of jobs processed to add
            jobs_failed_delta: Number of jobs failed to add
            processing_time_seconds: Processing time for last job

        Returns:
            True if successful, False otherwise
        """
        try:
            conn = self._get_conn()

            if processing_time_seconds is not None:
                # Update with new processing time
                conn.execute("""
                    UPDATE worker_state
                    SET jobs_processed_count = jobs_processed_count + ?,
                        jobs_failed_count = jobs_failed_count + ?,
                        total_processing_time_seconds = total_processing_time_seconds + ?,
                        avg_processing_time_seconds = CASE
                            WHEN jobs_processed_count + ? > 0
                            THEN (total_processing_time_seconds + ?) / (jobs_processed_count + ?)
                            ELSE NULL
                        END
                    WHERE worker_id = ?
                """, (
                    jobs_processed_delta, jobs_failed_delta, processing_time_seconds,
                    jobs_processed_delta, processing_time_seconds, jobs_processed_delta,
                    worker_id
                ))
            else:
                # Just update counts
                conn.execute("""
                    UPDATE worker_state
                    SET jobs_processed_count = jobs_processed_count + ?,
                        jobs_failed_count = jobs_failed_count + ?
                    WHERE worker_id = ?
                """, (jobs_processed_delta, jobs_failed_delta, worker_id))

            conn.commit()
            return True

        except Exception as e:
            logger.error(f"Failed to update worker stats for worker {worker_id}: {e}")
            return False

    def mark_worker_stopped(self, worker_id: int) -> bool:
        """Mark a worker as stopped.

        Args:
            worker_id: Worker ID

        Returns:
            True if successful, False otherwise
        """
        try:
            conn = self._get_conn()
            conn.execute("""
                UPDATE worker_state
                SET status = 'dead',
                    stopped_at = CURRENT_TIMESTAMP,
                    last_status_change = CURRENT_TIMESTAMP
                WHERE worker_id = ?
            """, (worker_id,))
            conn.commit()
            return True

        except Exception as e:
            logger.error(f"Failed to mark worker {worker_id} as stopped: {e}")
            return False

    def update_job_state(
        self,
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
    ) -> bool:
        """Update or insert job state.

        Args:
            job_id: Job ID
            job_type: Job type ('notebook', 'drawio', 'plantuml')
            status: Job status ('pending', 'processing', 'completed', 'failed')
            input_file: Input file path
            output_file: Output file path
            worker_id: Worker ID processing this job
            content_hash: Content hash of input
            correlation_id: Correlation ID for tracing
            priority: Job priority
            error_message: Error message if failed
            error_traceback: Error traceback if failed

        Returns:
            True if successful, False otherwise
        """
        try:
            conn = self._get_conn()
            cursor = conn.cursor()

            # Check if job exists
            cursor.execute("SELECT job_id FROM job_state WHERE job_id = ?", (job_id,))
            exists = cursor.fetchone() is not None

            if exists:
                # Update existing job
                update_fields = ["status = ?", "last_status_change = CURRENT_TIMESTAMP"]
                params: List[Any] = [status]

                if worker_id is not None:
                    update_fields.append("worker_id = ?")
                    params.append(worker_id)

                if status == 'processing':
                    update_fields.append("started_at = CURRENT_TIMESTAMP")
                    update_fields.append("attempts = attempts + 1")
                elif status in ('completed', 'failed'):
                    update_fields.append("completed_at = CURRENT_TIMESTAMP")
                    update_fields.append("""
                        processing_time_seconds = CAST(
                            (julianday(CURRENT_TIMESTAMP) - julianday(started_at)) * 86400
                            AS REAL
                        )
                    """)

                if error_message is not None:
                    update_fields.append("error_message = ?")
                    params.append(error_message)

                if error_traceback is not None:
                    update_fields.append("error_traceback = ?")
                    params.append(error_traceback)

                params.append(job_id)
                cursor.execute(f"""
                    UPDATE job_state
                    SET {', '.join(update_fields)}
                    WHERE job_id = ?
                """, params)
            else:
                # Insert new job
                cursor.execute("""
                    INSERT INTO job_state (
                        job_id, job_type, status, input_file, output_file,
                        worker_id, content_hash, correlation_id, priority
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    job_id, job_type, status, input_file, output_file,
                    worker_id, content_hash, correlation_id, priority
                ))

            conn.commit()
            return True

        except Exception as e:
            logger.error(f"Failed to update job state for job {job_id}: {e}")
            return False

    def query(self, sql: str, params: Optional[Tuple] = None) -> List[Dict[str, Any]]:
        """Execute a SELECT query and return results.

        Args:
            sql: SQL query string
            params: Optional query parameters

        Returns:
            List of result dictionaries
        """
        try:
            conn = self._get_conn()
            cursor = conn.cursor()

            if params:
                cursor.execute(sql, params)
            else:
                cursor.execute(sql)

            # Convert rows to dictionaries
            columns = [desc[0] for desc in cursor.description]
            results = []
            for row in cursor.fetchall():
                results.append(dict(zip(columns, row)))

            return results

        except Exception as e:
            logger.error(f"Query failed: {e}")
            return []

    def query_one(self, sql: str, params: Optional[Tuple] = None) -> Optional[Dict[str, Any]]:
        """Execute a SELECT query and return first result.

        Args:
            sql: SQL query string
            params: Optional query parameters

        Returns:
            First result as dictionary, or None if no results
        """
        results = self.query(sql, params)
        return results[0] if results else None

    def cleanup_old_events(self, days: int = 7) -> int:
        """Delete events older than specified days.

        Args:
            days: Number of days to retain

        Returns:
            Number of events deleted
        """
        try:
            conn = self._get_conn()
            cursor = conn.cursor()

            cursor.execute("""
                DELETE FROM events
                WHERE event_time < datetime('now', '-' || ? || ' days')
                AND severity NOT IN ('error', 'critical')
            """, (days,))

            conn.commit()
            deleted = cursor.rowcount
            logger.info(f"Cleaned up {deleted} old events")
            return deleted

        except Exception as e:
            logger.error(f"Failed to cleanup old events: {e}")
            return 0

    def cleanup_old_jobs(self, days: int = 7) -> int:
        """Delete old completed/failed jobs.

        Args:
            days: Number of days to retain

        Returns:
            Number of jobs deleted
        """
        try:
            conn = self._get_conn()
            cursor = conn.cursor()

            cursor.execute("""
                DELETE FROM job_state
                WHERE status IN ('completed', 'failed')
                AND completed_at < datetime('now', '-' || ? || ' days')
            """, (days,))

            conn.commit()
            deleted = cursor.rowcount
            logger.info(f"Cleaned up {deleted} old jobs")
            return deleted

        except Exception as e:
            logger.error(f"Failed to cleanup old jobs: {e}")
            return 0

    def close(self):
        """Close database connection."""
        if hasattr(self._local, 'conn'):
            self._local.conn.close()
            delattr(self._local, 'conn')
