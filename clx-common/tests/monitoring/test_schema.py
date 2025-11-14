"""Tests for monitoring database schema."""

import pytest
import sqlite3
import tempfile
from pathlib import Path

from clx_common.monitoring.schema import (
    init_monitoring_database,
    get_monitoring_db_path,
    CURRENT_SCHEMA_VERSION,
    _get_schema_version,
    _drop_all_tables,
)


@pytest.fixture
def temp_db():
    """Create a temporary database file."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = Path(f.name)

    yield db_path

    # Cleanup
    if db_path.exists():
        db_path.unlink()


def test_init_monitoring_database_creates_file(temp_db):
    """Test that init_monitoring_database creates a database file."""
    # Remove the temp file so we can test creation
    temp_db.unlink()

    assert not temp_db.exists()
    init_monitoring_database(temp_db)
    assert temp_db.exists()


def test_init_monitoring_database_creates_tables(temp_db):
    """Test that all required tables are created."""
    init_monitoring_database(temp_db)

    conn = sqlite3.connect(str(temp_db))
    cursor = conn.cursor()

    # Get all table names
    cursor.execute("""
        SELECT name FROM sqlite_master
        WHERE type='table'
        ORDER BY name
    """)
    tables = {row[0] for row in cursor.fetchall()}

    # Check that all required tables exist
    required_tables = {
        'schema_version',
        'events',
        'worker_state',
        'job_state',
        'service_health',
        'alert_rules'
    }

    assert required_tables.issubset(tables)
    conn.close()


def test_init_monitoring_database_creates_indexes(temp_db):
    """Test that indexes are created."""
    init_monitoring_database(temp_db)

    conn = sqlite3.connect(str(temp_db))
    cursor = conn.cursor()

    # Get all index names
    cursor.execute("""
        SELECT name FROM sqlite_master
        WHERE type='index' AND name NOT LIKE 'sqlite_%'
        ORDER BY name
    """)
    indexes = {row[0] for row in cursor.fetchall()}

    # Check for some key indexes
    expected_indexes = {
        'idx_events_time',
        'idx_events_type',
        'idx_events_category',
        'idx_worker_state_status',
        'idx_job_state_status',
        'idx_job_state_correlation',
    }

    assert expected_indexes.issubset(indexes)
    conn.close()


def test_init_monitoring_database_sets_version(temp_db):
    """Test that schema version is set correctly."""
    init_monitoring_database(temp_db)

    conn = sqlite3.connect(str(temp_db))
    version = _get_schema_version(conn)
    conn.close()

    assert version == CURRENT_SCHEMA_VERSION


def test_init_monitoring_database_inserts_default_alert_rules(temp_db):
    """Test that default alert rules are inserted."""
    init_monitoring_database(temp_db)

    conn = sqlite3.connect(str(temp_db))
    cursor = conn.cursor()

    cursor.execute("SELECT COUNT(*) FROM alert_rules")
    count = cursor.fetchone()[0]

    assert count >= 3  # At least hung_worker, high_failure_rate, queue_backup

    # Check specific rules exist
    cursor.execute("SELECT rule_name FROM alert_rules ORDER BY rule_name")
    rule_names = {row[0] for row in cursor.fetchall()}

    expected_rules = {'hung_worker', 'high_failure_rate', 'queue_backup'}
    assert expected_rules.issubset(rule_names)

    conn.close()


def test_init_monitoring_database_idempotent(temp_db):
    """Test that calling init multiple times is safe."""
    init_monitoring_database(temp_db)
    init_monitoring_database(temp_db)  # Should not raise error

    conn = sqlite3.connect(str(temp_db))
    version = _get_schema_version(conn)
    conn.close()

    assert version == CURRENT_SCHEMA_VERSION


def test_init_monitoring_database_force_recreates(temp_db):
    """Test that force=True drops and recreates tables."""
    init_monitoring_database(temp_db)

    conn = sqlite3.connect(str(temp_db))

    # Insert some test data
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO events (event_type, event_category, severity)
        VALUES ('test', 'system', 'info')
    """)
    conn.commit()

    # Verify data exists
    cursor.execute("SELECT COUNT(*) FROM events")
    assert cursor.fetchone()[0] == 1

    conn.close()

    # Force re-initialization
    init_monitoring_database(temp_db, force=True)

    # Data should be gone
    conn = sqlite3.connect(str(temp_db))
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM events")
    assert cursor.fetchone()[0] == 0
    conn.close()


def test_get_schema_version_returns_zero_for_empty_db(temp_db):
    """Test that _get_schema_version returns 0 for uninitialized database."""
    conn = sqlite3.connect(str(temp_db))
    version = _get_schema_version(conn)
    conn.close()

    assert version == 0


def test_drop_all_tables(temp_db):
    """Test that _drop_all_tables removes all tables."""
    init_monitoring_database(temp_db)

    conn = sqlite3.connect(str(temp_db))

    # Verify tables exist
    cursor = conn.cursor()
    cursor.execute("""
        SELECT COUNT(*) FROM sqlite_master
        WHERE type='table' AND name NOT LIKE 'sqlite_%'
    """)
    assert cursor.fetchone()[0] > 0

    # Drop all tables
    _drop_all_tables(conn)

    # Verify tables are gone
    cursor.execute("""
        SELECT COUNT(*) FROM sqlite_master
        WHERE type='table' AND name NOT LIKE 'sqlite_%'
    """)
    assert cursor.fetchone()[0] == 0

    conn.close()


def test_get_monitoring_db_path_with_base_path():
    """Test get_monitoring_db_path with custom base path."""
    base_path = Path("/tmp/test")
    db_path = get_monitoring_db_path(base_path)

    assert db_path == base_path / "clx_monitoring.db"


def test_get_monitoring_db_path_without_base_path():
    """Test get_monitoring_db_path with no base path uses cwd."""
    db_path = get_monitoring_db_path()

    assert db_path == Path.cwd() / "clx_monitoring.db"


def test_events_table_schema(temp_db):
    """Test that events table has correct columns."""
    init_monitoring_database(temp_db)

    conn = sqlite3.connect(str(temp_db))
    cursor = conn.cursor()

    cursor.execute("PRAGMA table_info(events)")
    columns = {row[1] for row in cursor.fetchall()}

    expected_columns = {
        'id', 'event_time', 'event_type', 'event_category', 'severity',
        'worker_id', 'job_id', 'correlation_id', 'event_data',
        'message', 'hostname', 'process_id'
    }

    assert expected_columns == columns
    conn.close()


def test_worker_state_table_schema(temp_db):
    """Test that worker_state table has correct columns."""
    init_monitoring_database(temp_db)

    conn = sqlite3.connect(str(temp_db))
    cursor = conn.cursor()

    cursor.execute("PRAGMA table_info(worker_state)")
    columns = {row[1] for row in cursor.fetchall()}

    expected_columns = {
        'worker_id', 'worker_type', 'container_id', 'status',
        'last_status_change', 'current_job_id', 'current_job_started_at',
        'current_job_input_file', 'started_at', 'last_heartbeat', 'stopped_at',
        'jobs_processed_count', 'jobs_failed_count', 'total_processing_time_seconds',
        'avg_processing_time_seconds', 'cpu_usage_percent', 'memory_usage_mb',
        'last_health_check', 'execution_mode', 'metadata'
    }

    assert expected_columns == columns
    conn.close()


def test_job_state_table_schema(temp_db):
    """Test that job_state table has correct columns."""
    init_monitoring_database(temp_db)

    conn = sqlite3.connect(str(temp_db))
    cursor = conn.cursor()

    cursor.execute("PRAGMA table_info(job_state)")
    columns = {row[1] for row in cursor.fetchall()}

    expected_columns = {
        'job_id', 'job_type', 'status', 'last_status_change', 'worker_id',
        'input_file', 'output_file', 'content_hash', 'created_at', 'started_at',
        'completed_at', 'processing_time_seconds', 'queue_time_seconds',
        'attempts', 'max_attempts', 'error_message', 'error_traceback',
        'correlation_id', 'priority', 'payload_summary', 'metadata'
    }

    assert expected_columns == columns
    conn.close()


def test_service_health_table_schema(temp_db):
    """Test that service_health table has correct columns."""
    init_monitoring_database(temp_db)

    conn = sqlite3.connect(str(temp_db))
    cursor = conn.cursor()

    cursor.execute("PRAGMA table_info(service_health)")
    columns = {row[1] for row in cursor.fetchall()}

    expected_columns = {
        'id', 'check_time', 'overall_status', 'workers_total', 'workers_idle',
        'workers_busy', 'workers_hung', 'workers_dead', 'jobs_pending',
        'jobs_processing', 'jobs_failed_recent', 'avg_queue_time_seconds',
        'avg_processing_time_seconds', 'jobs_completed_last_hour',
        'total_cpu_percent', 'total_memory_mb', 'active_alerts', 'metadata'
    }

    assert expected_columns == columns
    conn.close()


def test_alert_rules_table_schema(temp_db):
    """Test that alert_rules table has correct columns."""
    init_monitoring_database(temp_db)

    conn = sqlite3.connect(str(temp_db))
    cursor = conn.cursor()

    cursor.execute("PRAGMA table_info(alert_rules)")
    columns = {row[1] for row in cursor.fetchall()}

    expected_columns = {
        'id', 'rule_name', 'rule_type', 'enabled', 'condition_sql',
        'threshold_value', 'time_window_seconds', 'severity',
        'message_template', 'created_at', 'last_triggered', 'trigger_count'
    }

    assert expected_columns == columns
    conn.close()
