"""Monitoring database schema definition and initialization.

This module defines the schema for the CLX monitoring database, which stores
operational state and events for just-in-time monitoring and diagnostics.
"""

import logging
import sqlite3
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Current schema version
CURRENT_SCHEMA_VERSION = 1


def init_monitoring_database(db_path: Path, force: bool = False) -> None:
    """Initialize monitoring database with schema.

    Args:
        db_path: Path to monitoring database file
        force: If True, drop and recreate all tables

    Raises:
        sqlite3.Error: If database initialization fails
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(db_path))
    try:
        if force:
            logger.info(f"Force-initializing monitoring database at {db_path}")
            _drop_all_tables(conn)

        # Check if database is already initialized
        current_version = _get_schema_version(conn)

        if current_version == 0:
            # New database, create schema
            logger.info(f"Creating monitoring database schema at {db_path}")
            _create_schema(conn)
            _set_schema_version(conn, CURRENT_SCHEMA_VERSION)
            logger.info(f"Monitoring database initialized (version {CURRENT_SCHEMA_VERSION})")
        elif current_version == CURRENT_SCHEMA_VERSION:
            logger.debug(f"Monitoring database already at version {CURRENT_SCHEMA_VERSION}")
        else:
            # Migration needed (future versions)
            logger.info(
                f"Monitoring database needs migration from version {current_version} "
                f"to {CURRENT_SCHEMA_VERSION}"
            )
            _migrate_schema(conn, current_version, CURRENT_SCHEMA_VERSION)

        conn.commit()
    finally:
        conn.close()


def _get_schema_version(conn: sqlite3.Connection) -> int:
    """Get current schema version from database.

    Args:
        conn: Database connection

    Returns:
        Schema version number, or 0 if not initialized
    """
    cursor = conn.cursor()

    # Check if schema_version table exists
    cursor.execute("""
        SELECT name FROM sqlite_master
        WHERE type='table' AND name='schema_version'
    """)

    if cursor.fetchone() is None:
        return 0

    # Get current version
    cursor.execute("SELECT MAX(version) FROM schema_version")
    row = cursor.fetchone()
    return row[0] if row and row[0] is not None else 0


def _set_schema_version(conn: sqlite3.Connection, version: int) -> None:
    """Set schema version in database.

    Args:
        conn: Database connection
        version: Schema version to set
    """
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO schema_version (version, description)
        VALUES (?, ?)
    """, (version, f"Schema version {version}"))
    conn.commit()


def _drop_all_tables(conn: sqlite3.Connection) -> None:
    """Drop all tables in the database.

    Args:
        conn: Database connection
    """
    cursor = conn.cursor()

    # Get all table names
    cursor.execute("""
        SELECT name FROM sqlite_master
        WHERE type='table' AND name NOT LIKE 'sqlite_%'
    """)

    tables = cursor.fetchall()

    # Drop each table
    for (table_name,) in tables:
        logger.debug(f"Dropping table: {table_name}")
        cursor.execute(f"DROP TABLE IF EXISTS {table_name}")

    conn.commit()


def _create_schema(conn: sqlite3.Connection) -> None:
    """Create monitoring database schema.

    Args:
        conn: Database connection
    """
    cursor = conn.cursor()

    # Schema version table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS schema_version (
            version INTEGER PRIMARY KEY,
            applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            description TEXT
        )
    """)

    # Events table (append-only log)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS events (
            -- Identity
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

            -- Event Classification
            event_type TEXT NOT NULL,
            event_category TEXT NOT NULL,
            severity TEXT NOT NULL,

            -- Event Context
            worker_id INTEGER,
            job_id INTEGER,
            correlation_id TEXT,

            -- Event Data
            event_data TEXT,

            -- Additional Context
            message TEXT,
            hostname TEXT,
            process_id INTEGER
        )
    """)

    # Indexes for events table
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_events_time ON events(event_time DESC)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_events_type ON events(event_type)")
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_events_category "
        "ON events(event_category, event_time DESC)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_events_worker "
        "ON events(worker_id, event_time DESC)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_events_job "
        "ON events(job_id, event_time DESC)"
    )
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_events_correlation ON events(correlation_id)")
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_events_severity "
        "ON events(severity, event_time DESC)"
    )

    # Worker state table (current state)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS worker_state (
            -- Identity
            worker_id INTEGER PRIMARY KEY,
            worker_type TEXT NOT NULL,
            container_id TEXT NOT NULL,

            -- Current Status
            status TEXT NOT NULL,
            last_status_change TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

            -- Current Job (if busy)
            current_job_id INTEGER,
            current_job_started_at TIMESTAMP,
            current_job_input_file TEXT,

            -- Lifecycle
            started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_heartbeat TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            stopped_at TIMESTAMP,

            -- Statistics (derived from events)
            jobs_processed_count INTEGER DEFAULT 0,
            jobs_failed_count INTEGER DEFAULT 0,
            total_processing_time_seconds REAL DEFAULT 0,
            avg_processing_time_seconds REAL,

            -- Health Metrics
            cpu_usage_percent REAL,
            memory_usage_mb REAL,
            last_health_check TIMESTAMP,

            -- Metadata
            execution_mode TEXT,
            metadata TEXT
        )
    """)

    # Indexes for worker_state table
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_worker_state_status ON worker_state(status)")
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_worker_state_type "
        "ON worker_state(worker_type, status)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_worker_state_heartbeat "
        "ON worker_state(last_heartbeat)"
    )

    # Job state table (current + recent history)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS job_state (
            -- Identity
            job_id INTEGER PRIMARY KEY,
            job_type TEXT NOT NULL,

            -- Status
            status TEXT NOT NULL,
            last_status_change TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

            -- Assignment
            worker_id INTEGER,

            -- Files
            input_file TEXT NOT NULL,
            output_file TEXT NOT NULL,
            content_hash TEXT,

            -- Lifecycle Timing
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            started_at TIMESTAMP,
            completed_at TIMESTAMP,

            -- Performance
            processing_time_seconds REAL,
            queue_time_seconds REAL,

            -- Error Handling
            attempts INTEGER DEFAULT 0,
            max_attempts INTEGER DEFAULT 3,
            error_message TEXT,
            error_traceback TEXT,

            -- Context
            correlation_id TEXT,
            priority INTEGER DEFAULT 0,

            -- Metadata
            payload_summary TEXT,
            metadata TEXT
        )
    """)

    # Indexes for job_state table
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_job_state_status "
        "ON job_state(status, created_at DESC)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_job_state_type "
        "ON job_state(job_type, status)"
    )
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_job_state_worker ON job_state(worker_id)")
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_job_state_correlation "
        "ON job_state(correlation_id)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_job_state_created "
        "ON job_state(created_at DESC)"
    )

    # Service health table (system-level state snapshots)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS service_health (
            -- Time-series entry
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            check_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

            -- Overall Health
            overall_status TEXT NOT NULL,

            -- Worker Counts
            workers_total INTEGER DEFAULT 0,
            workers_idle INTEGER DEFAULT 0,
            workers_busy INTEGER DEFAULT 0,
            workers_hung INTEGER DEFAULT 0,
            workers_dead INTEGER DEFAULT 0,

            -- Queue Metrics
            jobs_pending INTEGER DEFAULT 0,
            jobs_processing INTEGER DEFAULT 0,
            jobs_failed_recent INTEGER DEFAULT 0,

            -- Performance Metrics
            avg_queue_time_seconds REAL,
            avg_processing_time_seconds REAL,
            jobs_completed_last_hour INTEGER DEFAULT 0,

            -- Resource Metrics
            total_cpu_percent REAL,
            total_memory_mb REAL,

            -- Issues
            active_alerts TEXT,

            -- Metadata
            metadata TEXT
        )
    """)

    # Index for service_health table
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_service_health_time "
        "ON service_health(check_time DESC)"
    )

    # Alert rules table (configuration)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS alert_rules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            rule_name TEXT NOT NULL UNIQUE,
            rule_type TEXT NOT NULL,
            enabled BOOLEAN DEFAULT 1,

            -- Condition
            condition_sql TEXT NOT NULL,
            threshold_value REAL,
            time_window_seconds INTEGER,

            -- Action
            severity TEXT NOT NULL,
            message_template TEXT NOT NULL,

            -- Metadata
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_triggered TIMESTAMP,
            trigger_count INTEGER DEFAULT 0
        )
    """)

    # Insert default alert rules
    _insert_default_alert_rules(cursor)

    conn.commit()


def _insert_default_alert_rules(cursor: sqlite3.Cursor) -> None:
    """Insert default alert rules.

    Args:
        cursor: Database cursor
    """
    default_rules = [
        (
            "hung_worker",
            "threshold",
            'status = "hung"',
            None,
            None,
            "error",
            "Worker {worker_id} is hung"
        ),
        (
            "high_failure_rate",
            "threshold",
            "jobs_failed_count > 10",
            None,
            None,
            "warning",
            "Worker {worker_id} has high failure rate"
        ),
        (
            "queue_backup",
            "threshold",
            "jobs_pending > 100",
            None,
            None,
            "warning",
            "Job queue has {jobs_pending} pending jobs"
        ),
    ]

    for rule in default_rules:
        cursor.execute("""
            INSERT OR IGNORE INTO alert_rules
            (rule_name, rule_type, condition_sql, threshold_value,
             time_window_seconds, severity, message_template)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, rule)


def _migrate_schema(
    conn: sqlite3.Connection,
    from_version: int,
    to_version: int
) -> None:
    """Migrate database schema from one version to another.

    Args:
        conn: Database connection
        from_version: Current schema version
        to_version: Target schema version

    Raises:
        ValueError: If migration path is not supported
    """
    if from_version == to_version:
        logger.debug("No migration needed")
        return

    if from_version > to_version:
        raise ValueError(
            f"Downgrade not supported: {from_version} -> {to_version}"
        )

    logger.info(f"Migrating schema from version {from_version} to {to_version}")

    # Future migrations will be implemented here
    # For now, we only have version 1
    if from_version < 1 and to_version >= 1:
        # This would be a migration from pre-versioned to version 1
        # For now, we don't support this - force re-initialization
        raise ValueError(
            f"Migration from version {from_version} not supported. "
            "Please use force=True to re-initialize."
        )

    _set_schema_version(conn, to_version)
    logger.info(f"Migration completed to version {to_version}")


def get_monitoring_db_path(base_path: Optional[Path] = None) -> Path:
    """Get the path to the monitoring database.

    Args:
        base_path: Base directory path. If None, uses current directory.

    Returns:
        Path to monitoring database file
    """
    if base_path is None:
        base_path = Path.cwd()

    return base_path / "clx_monitoring.db"
