# CLX Just-in-Time Monitoring System - Requirements and Design

**Version:** 1.0
**Date:** 2025-11-13
**Status:** Draft for Approval

---

## Table of Contents

1. [Problem Statement](#problem-statement)
2. [Requirements](#requirements)
3. [Design Goals](#design-goals)
4. [Architecture Overview](#architecture-overview)
5. [Database Schema Design](#database-schema-design)
6. [Backend API Design](#backend-api-design)
7. [Frontend Design](#frontend-design)
8. [Implementation Plan](#implementation-plan)
9. [Migration Strategy](#migration-strategy)
10. [Open Questions](#open-questions)

---

## 1. Problem Statement

### Current State

The CLX codebase currently uses extensive logging throughout the system:
- Worker status updates (starting, processing, stopping)
- Job processing events (submitted, completed, failed)
- Database operations
- File operations
- Backend messaging events
- Correlation ID tracking

**Issues with Current Approach:**

1. **Volume:** Extensive logging creates large log files that are difficult to navigate
2. **Local Usage:** CLX is primarily used locally, not in distributed environments
3. **Limited Tooling:** Unlike distributed systems, there are no advanced log analysis or sanitization tools
4. **Diagnostic Needs:** The primary use case is debugging stuck services or hung tasks
5. **Real-time Visibility:** Need to see current state, not just historical logs

### Target State

A just-in-time monitoring system that:
- Stores operational state data in a structured database
- Provides query interfaces for common diagnostic scenarios
- Reduces logging volume to critical errors only
- Enables real-time system health visibility
- Maintains historical data for trend analysis

---

## 2. Requirements

### 2.1 Functional Requirements

**FR-1: Worker Monitoring**
- System MUST track which workers are currently active
- System MUST record worker status (idle, busy, hung, dead)
- System MUST track what job each worker is currently processing
- System MUST record when workers started processing their current job
- System MUST track worker health metrics (heartbeat, CPU, memory)

**FR-2: Job Monitoring**
- System MUST track all jobs (pending, processing, completed, failed)
- System MUST record job processing times
- System MUST track which worker is processing each job
- System MUST record job lifecycle events (created, started, completed)
- System MUST maintain job error information

**FR-3: Service Health**
- System MUST provide overall service health status
- System MUST detect hung workers (busy but no CPU activity)
- System MUST detect dead workers (no heartbeat)
- System MUST track queue depth (pending jobs by type)

**FR-4: Historical Analysis**
- System MUST maintain recent historical data (configurable retention)
- System MUST track worker performance trends
- System MUST record job failure patterns

**FR-5: Query Interface**
- System MUST provide API to query current system state
- System MUST support common diagnostic queries (defined in use cases)
- System MUST provide data export capabilities (JSON, CSV)

**FR-6: Event Recording**
- System MUST record significant state changes as events
- System MUST support event queries with time-based filtering
- System MUST maintain event context (correlation IDs, related entities)

### 2.2 Non-Functional Requirements

**NFR-1: Performance**
- Recording events MUST NOT significantly impact job processing performance
- Queries MUST return results within 1 second for typical datasets
- Database writes MUST be asynchronous where possible

**NFR-2: Storage**
- Event retention MUST be configurable (default: 7 days)
- Database size MUST be manageable for local deployments
- Old events MUST be automatically purged

**NFR-3: Reliability**
- Monitoring system failures MUST NOT impact job processing
- Database corruption MUST NOT affect job queue integrity
- System MUST gracefully handle monitoring database unavailability

**NFR-4: Usability**
- Common queries MUST be pre-defined and easy to execute
- Query results MUST be human-readable
- Frontend MUST work without external dependencies

**NFR-5: Compatibility**
- System MUST work with existing SQLite database infrastructure
- System MUST integrate with current worker/job architecture
- System MUST support both Docker and direct execution modes

### 2.3 Use Cases

**UC-1: Diagnose Stuck Job**
*As a developer, I want to see which workers are processing jobs and for how long, so I can identify stuck processing.*

**UC-2: Check Service Health**
*As a developer, I want to see the status of all services at a glance, so I can verify the system is running correctly.*

**UC-3: Investigate Job Failure**
*As a developer, I want to see the history of a failed job, including which worker processed it and what errors occurred.*

**UC-4: Monitor Queue Depth**
*As a developer, I want to see how many pending jobs exist for each job type, so I can assess system load.*

**UC-5: Track Worker Performance**
*As a developer, I want to see worker performance statistics (jobs processed, average time, failure rate), so I can identify performance issues.*

**UC-6: Analyze System Events**
*As a developer, I want to query recent system events (worker starts, job completions, errors), so I can understand system behavior over time.*

**UC-7: Debug Correlation Flow**
*As a developer, I want to trace all events related to a specific correlation ID, so I can debug complex multi-job workflows.*

---

## 3. Design Goals

### 3.1 Principles

1. **Non-Invasive:** Minimal changes to existing job processing logic
2. **Separation of Concerns:** Monitoring database separate from job queue database
3. **Fail-Safe:** Monitoring failures must not affect job processing
4. **Query-Optimized:** Schema optimized for common diagnostic queries
5. **Time-Bounded:** Automatic retention management to prevent unbounded growth
6. **Progressive Enhancement:** Can be disabled/enabled without code changes

### 3.2 Design Decisions

**Decision 1: Separate Monitoring Database**
- **Rationale:** Isolates monitoring from critical job queue; allows independent optimization
- **Trade-off:** Additional database file to manage
- **Alternative Considered:** Single database with separate tables (rejected due to failure domain coupling)

**Decision 2: Event-Based Architecture**
- **Rationale:** Captures state changes as discrete events; enables temporal queries
- **Trade-off:** More storage than state-only tracking
- **Alternative Considered:** Pure state tables (rejected due to loss of historical context)

**Decision 3: Hybrid State + Events**
- **Rationale:** Current state tables for fast queries; events for historical analysis
- **Trade-off:** Some data duplication
- **Alternative Considered:** Events-only with materialized views (rejected due to SQLite limitations)

**Decision 4: CLI + Simple Web UI**
- **Rationale:** CLI for scripting; web UI for visual exploration
- **Trade-off:** Need to maintain two interfaces
- **Alternative Considered:** CLI-only (rejected due to poor UX for exploration)

**Decision 5: Push-Based Event Recording**
- **Rationale:** Workers/queue push events to monitoring DB
- **Trade-off:** Coupling between components
- **Alternative Considered:** Pull-based polling (rejected due to missed events)

---

## 4. Architecture Overview

### 4.1 System Components

```
┌─────────────────────────────────────────────────────────────┐
│                     CLX System                               │
├─────────────────────────────────────────────────────────────┤
│                                                               │
│  ┌─────────────┐      ┌──────────────┐                      │
│  │   Workers   │      │  Job Queue   │                      │
│  │  (Existing) │─────▶│  (Existing)  │                      │
│  └─────────────┘      └──────────────┘                      │
│         │                                                     │
│         │ (record events)                                    │
│         ▼                                                     │
│  ┌──────────────────────────────────────┐                   │
│  │   Monitoring Event Bus                │                   │
│  │   (New - async, non-blocking)        │                   │
│  └──────────────────────────────────────┘                   │
│         │                                                     │
│         ▼                                                     │
│  ┌──────────────────────────────────────┐                   │
│  │   Monitoring Database                 │                   │
│  │   (New - SQLite)                      │                   │
│  │   - Events table                      │                   │
│  │   - Worker state table                │                   │
│  │   - Job state table                   │                   │
│  │   - Service health table              │                   │
│  └──────────────────────────────────────┘                   │
│         │                                                     │
│         ▼                                                     │
│  ┌──────────────────────────────────────┐                   │
│  │   Monitoring API                      │                   │
│  │   (New - Python module)               │                   │
│  │   - Query interface                   │                   │
│  │   - Export functions                  │                   │
│  └──────────────────────────────────────┘                   │
│         │                                                     │
│         ├────────────────┬────────────────┐                 │
│         ▼                ▼                ▼                 │
│  ┌──────────┐    ┌──────────┐    ┌──────────┐             │
│  │   CLI    │    │  Web UI  │    │ JSON API │             │
│  │  (New)   │    │  (New)   │    │  (New)   │             │
│  └──────────┘    └──────────┘    └──────────┘             │
│                                                               │
└─────────────────────────────────────────────────────────────┘
```

### 4.2 Data Flow

**Event Recording Flow:**
```
1. Worker/Job state change occurs
2. Component calls MonitoringEventBus.record_event(event)
3. Event Bus (async):
   a. Validates event
   b. Adds timestamp/metadata
   c. Writes to monitoring DB (non-blocking)
   d. Updates current state tables
   e. On error: logs but doesn't propagate exception
4. Original operation continues unaffected
```

**Query Flow:**
```
1. User/UI issues query via Monitoring API
2. API translates to SQL query
3. Query executes against monitoring DB
4. Results formatted (JSON/table/CSV)
5. Results returned to caller
```

### 4.3 Component Interactions

**Workers → Monitoring:**
- Record worker lifecycle events (start, stop, crash)
- Record job processing events (start, complete, fail)
- Update heartbeat (existing mechanism, optionally mirror to monitoring)

**Job Queue → Monitoring:**
- Record job submission events
- Record job status changes

**Pool Manager → Monitoring:**
- Record worker health check results
- Record restart events

**Monitoring API → Monitoring DB:**
- Read-only queries for diagnostics
- Write operations for maintenance (cleanup, export)

---

## 5. Database Schema Design

### 5.1 Database File Structure

**File:** `clx_monitoring.db` (separate from `clx_jobs.db`)

**Rationale:**
- Isolation: Monitoring DB failures don't affect job processing
- Performance: Separate write domains
- Maintenance: Can be deleted/recreated without affecting jobs
- Backup: Can exclude from critical backups

### 5.2 Schema Version Management

```sql
CREATE TABLE schema_version (
    version INTEGER PRIMARY KEY,
    applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    description TEXT
);

INSERT INTO schema_version (version, description)
VALUES (1, 'Initial monitoring schema');
```

### 5.3 Core Tables

#### 5.3.1 Events Table (Append-Only Log)

```sql
CREATE TABLE events (
    -- Identity
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    -- Event Classification
    event_type TEXT NOT NULL,  -- Enum: see below
    event_category TEXT NOT NULL,  -- 'worker', 'job', 'system', 'health'
    severity TEXT NOT NULL,  -- 'debug', 'info', 'warning', 'error', 'critical'

    -- Event Context
    worker_id INTEGER,
    job_id INTEGER,
    correlation_id TEXT,

    -- Event Data
    event_data TEXT,  -- JSON blob with event-specific data

    -- Additional Context
    message TEXT,  -- Human-readable description
    hostname TEXT,  -- For distributed setups
    process_id INTEGER
);

-- Indexes for common queries
CREATE INDEX idx_events_time ON events(event_time DESC);
CREATE INDEX idx_events_type ON events(event_type);
CREATE INDEX idx_events_category ON events(event_category, event_time DESC);
CREATE INDEX idx_events_worker ON events(worker_id, event_time DESC);
CREATE INDEX idx_events_job ON events(job_id, event_time DESC);
CREATE INDEX idx_events_correlation ON events(correlation_id);
CREATE INDEX idx_events_severity ON events(severity, event_time DESC);
```

**Event Types:**
- Worker Events: `worker_started`, `worker_stopped`, `worker_idle`, `worker_busy`, `worker_hung`, `worker_dead`, `worker_heartbeat`
- Job Events: `job_submitted`, `job_started`, `job_completed`, `job_failed`, `job_retry`
- Health Events: `health_check_ok`, `health_check_warning`, `health_check_error`
- System Events: `system_started`, `system_stopped`, `pool_started`, `pool_stopped`

#### 5.3.2 Worker State Table (Current State)

```sql
CREATE TABLE worker_state (
    -- Identity
    worker_id INTEGER PRIMARY KEY,
    worker_type TEXT NOT NULL,
    container_id TEXT NOT NULL,

    -- Current Status
    status TEXT NOT NULL,  -- 'idle', 'busy', 'hung', 'dead'
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
    execution_mode TEXT,  -- 'docker', 'direct'
    metadata TEXT  -- JSON for extensibility
);

CREATE INDEX idx_worker_state_status ON worker_state(status);
CREATE INDEX idx_worker_state_type ON worker_state(worker_type, status);
CREATE INDEX idx_worker_state_heartbeat ON worker_state(last_heartbeat);
```

#### 5.3.3 Job State Table (Current + Recent History)

```sql
CREATE TABLE job_state (
    -- Identity
    job_id INTEGER PRIMARY KEY,
    job_type TEXT NOT NULL,

    -- Status
    status TEXT NOT NULL,  -- 'pending', 'processing', 'completed', 'failed'
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
    queue_time_seconds REAL,  -- time from created to started

    -- Error Handling
    attempts INTEGER DEFAULT 0,
    max_attempts INTEGER DEFAULT 3,
    error_message TEXT,
    error_traceback TEXT,

    -- Context
    correlation_id TEXT,
    priority INTEGER DEFAULT 0,

    -- Metadata
    payload_summary TEXT,  -- JSON summary of payload
    metadata TEXT  -- JSON for extensibility
);

CREATE INDEX idx_job_state_status ON job_state(status, created_at DESC);
CREATE INDEX idx_job_state_type ON job_state(job_type, status);
CREATE INDEX idx_job_state_worker ON job_state(worker_id);
CREATE INDEX idx_job_state_correlation ON job_state(correlation_id);
CREATE INDEX idx_job_state_created ON job_state(created_at DESC);
```

#### 5.3.4 Service Health Table (System-Level State)

```sql
CREATE TABLE service_health (
    -- Time-series entry
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    check_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    -- Overall Health
    overall_status TEXT NOT NULL,  -- 'healthy', 'degraded', 'unhealthy'

    -- Worker Counts
    workers_total INTEGER DEFAULT 0,
    workers_idle INTEGER DEFAULT 0,
    workers_busy INTEGER DEFAULT 0,
    workers_hung INTEGER DEFAULT 0,
    workers_dead INTEGER DEFAULT 0,

    -- Queue Metrics
    jobs_pending INTEGER DEFAULT 0,
    jobs_processing INTEGER DEFAULT 0,
    jobs_failed_recent INTEGER DEFAULT 0,  -- last hour

    -- Performance Metrics
    avg_queue_time_seconds REAL,
    avg_processing_time_seconds REAL,
    jobs_completed_last_hour INTEGER DEFAULT 0,

    -- Resource Metrics
    total_cpu_percent REAL,
    total_memory_mb REAL,

    -- Issues
    active_alerts TEXT,  -- JSON array of current alerts

    -- Metadata
    metadata TEXT  -- JSON for extensibility
);

CREATE INDEX idx_service_health_time ON service_health(check_time DESC);
```

#### 5.3.5 Alert Rules Table (Configuration)

```sql
CREATE TABLE alert_rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    rule_name TEXT NOT NULL UNIQUE,
    rule_type TEXT NOT NULL,  -- 'threshold', 'pattern', 'absence'
    enabled BOOLEAN DEFAULT 1,

    -- Condition
    condition_sql TEXT NOT NULL,  -- SQL WHERE clause
    threshold_value REAL,
    time_window_seconds INTEGER,

    -- Action
    severity TEXT NOT NULL,  -- 'warning', 'error', 'critical'
    message_template TEXT NOT NULL,

    -- Metadata
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_triggered TIMESTAMP,
    trigger_count INTEGER DEFAULT 0
);

-- Example Rules
INSERT INTO alert_rules (rule_name, rule_type, condition_sql, severity, message_template)
VALUES
    ('hung_worker', 'threshold', 'status = "hung"', 'error', 'Worker {worker_id} is hung'),
    ('high_failure_rate', 'threshold', 'jobs_failed_count > 10', 'warning', 'Worker {worker_id} has high failure rate'),
    ('queue_backup', 'threshold', 'jobs_pending > 100', 'warning', 'Job queue has {jobs_pending} pending jobs');
```

### 5.4 Retention and Cleanup

```sql
-- Cleanup old events (run daily)
CREATE TRIGGER cleanup_old_events
AFTER INSERT ON events
WHEN (SELECT COUNT(*) FROM events) > 100000
BEGIN
    DELETE FROM events
    WHERE event_time < datetime('now', '-7 days')
    AND severity NOT IN ('error', 'critical');
END;

-- Cleanup old job state (run daily)
-- Keep only recent completed/failed jobs
CREATE TRIGGER cleanup_old_jobs
AFTER INSERT ON job_state
WHEN (SELECT COUNT(*) FROM job_state WHERE status IN ('completed', 'failed')) > 10000
BEGIN
    DELETE FROM job_state
    WHERE status IN ('completed', 'failed')
    AND completed_at < datetime('now', '-7 days');
END;

-- Cleanup old health snapshots (keep 1 week of hourly snapshots)
CREATE TRIGGER cleanup_old_health
AFTER INSERT ON service_health
WHEN (SELECT COUNT(*) FROM service_health) > 168  -- 7 days * 24 hours
BEGIN
    DELETE FROM service_health
    WHERE id NOT IN (
        SELECT id FROM service_health
        ORDER BY check_time DESC
        LIMIT 168
    );
END;
```

---

## 6. Backend API Design

### 6.1 Module Structure

```
clx-common/src/clx_common/monitoring/
├── __init__.py
├── event_bus.py          # Event recording
├── monitor_db.py         # Database operations
├── queries.py            # Pre-defined queries
├── api.py                # High-level API
├── schema.py             # Schema definition
└── cleanup.py            # Retention management
```

### 6.2 Event Bus API

```python
# clx-common/src/clx_common/monitoring/event_bus.py

from typing import Optional, Dict, Any
from datetime import datetime
from enum import Enum
import asyncio
import logging

logger = logging.getLogger(__name__)

class EventCategory(Enum):
    WORKER = "worker"
    JOB = "job"
    SYSTEM = "system"
    HEALTH = "health"

class EventSeverity(Enum):
    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"

class MonitoringEvent:
    """Represents a monitoring event."""
    def __init__(
        self,
        event_type: str,
        category: EventCategory,
        severity: EventSeverity = EventSeverity.INFO,
        worker_id: Optional[int] = None,
        job_id: Optional[int] = None,
        correlation_id: Optional[str] = None,
        message: Optional[str] = None,
        event_data: Optional[Dict[str, Any]] = None
    ):
        self.event_type = event_type
        self.category = category
        self.severity = severity
        self.worker_id = worker_id
        self.job_id = job_id
        self.correlation_id = correlation_id
        self.message = message
        self.event_data = event_data or {}
        self.event_time = datetime.now()

class MonitoringEventBus:
    """
    Asynchronous event bus for monitoring events.

    Design principles:
    - Non-blocking: Events recorded asynchronously
    - Fail-safe: Exceptions caught and logged, never propagated
    - Buffered: Events batched for performance
    """

    def __init__(self, db_path: Path, enabled: bool = True):
        self.db_path = db_path
        self.enabled = enabled
        self._event_queue = asyncio.Queue()
        self._worker_task = None

    async def start(self):
        """Start the event processing worker."""
        if self.enabled:
            self._worker_task = asyncio.create_task(self._process_events())

    async def stop(self):
        """Stop the event processing worker."""
        if self._worker_task:
            await self._event_queue.put(None)  # Sentinel
            await self._worker_task

    def record_event(self, event: MonitoringEvent):
        """
        Record a monitoring event (non-blocking).

        This method never raises exceptions. Failures are logged.
        """
        if not self.enabled:
            return

        try:
            # Non-blocking put
            asyncio.create_task(self._event_queue.put(event))
        except Exception as e:
            logger.error(f"Failed to queue monitoring event: {e}")

    async def _process_events(self):
        """Background worker that writes events to database."""
        while True:
            try:
                event = await self._event_queue.get()

                if event is None:  # Sentinel value
                    break

                await self._write_event(event)

            except Exception as e:
                logger.error(f"Error processing monitoring event: {e}")

    async def _write_event(self, event: MonitoringEvent):
        """Write event to database and update state tables."""
        try:
            # Import here to avoid circular dependencies
            from clx_common.monitoring.monitor_db import MonitoringDB

            db = MonitoringDB(self.db_path)

            # Write to events table
            db.insert_event(event)

            # Update state tables based on event type
            if event.category == EventCategory.WORKER:
                db.update_worker_state(event)
            elif event.category == EventCategory.JOB:
                db.update_job_state(event)
            elif event.category == EventCategory.HEALTH:
                db.update_service_health(event)

        except Exception as e:
            logger.error(f"Failed to write monitoring event to DB: {e}")

# Global event bus instance
_event_bus: Optional[MonitoringEventBus] = None

def init_monitoring(db_path: Path, enabled: bool = True):
    """Initialize global monitoring event bus."""
    global _event_bus
    _event_bus = MonitoringEventBus(db_path, enabled)
    asyncio.create_task(_event_bus.start())

def record_event(event: MonitoringEvent):
    """Record a monitoring event using the global event bus."""
    if _event_bus:
        _event_bus.record_event(event)

# Convenience functions for common events
def record_worker_event(
    event_type: str,
    worker_id: int,
    worker_type: str,
    status: Optional[str] = None,
    job_id: Optional[int] = None,
    **kwargs
):
    """Record a worker-related event."""
    record_event(MonitoringEvent(
        event_type=event_type,
        category=EventCategory.WORKER,
        worker_id=worker_id,
        job_id=job_id,
        event_data={
            'worker_type': worker_type,
            'status': status,
            **kwargs
        }
    ))

def record_job_event(
    event_type: str,
    job_id: int,
    job_type: str,
    status: str,
    worker_id: Optional[int] = None,
    correlation_id: Optional[str] = None,
    **kwargs
):
    """Record a job-related event."""
    record_event(MonitoringEvent(
        event_type=event_type,
        category=EventCategory.JOB,
        job_id=job_id,
        worker_id=worker_id,
        correlation_id=correlation_id,
        event_data={
            'job_type': job_type,
            'status': status,
            **kwargs
        }
    ))
```

### 6.3 Query API

```python
# clx-common/src/clx_common/monitoring/queries.py

from typing import List, Dict, Any, Optional
from pathlib import Path
from datetime import datetime, timedelta

class MonitoringQueries:
    """Pre-defined queries for common monitoring use cases."""

    def __init__(self, db_path: Path):
        from clx_common.monitoring.monitor_db import MonitoringDB
        self.db = MonitoringDB(db_path)

    # UC-1: Diagnose Stuck Job
    def get_active_jobs(self) -> List[Dict[str, Any]]:
        """
        Get all currently processing jobs with duration.

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
        """
        Get current service health overview.

        Returns:
            Dict with worker counts, queue depths, and overall status
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

        return {
            'workers': workers,
            'jobs': jobs,
            'timestamp': datetime.now().isoformat()
        }

    # UC-3: Investigate Job Failure
    def get_job_history(self, job_id: int) -> Dict[str, Any]:
        """
        Get complete history of a job including all events.

        Args:
            job_id: Job ID to investigate

        Returns:
            Dict with job state and list of events
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
        """
        Get pending job counts by job type.

        Returns:
            Dict mapping job_type to count
        """
        results = self.db.query("""
            SELECT job_type, COUNT(*) as count
            FROM job_state
            WHERE status = 'pending'
            GROUP BY job_type
        """)

        return {row['job_type']: row['count'] for row in results}

    # UC-5: Track Worker Performance
    def get_worker_performance(self, worker_type: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Get performance statistics for workers.

        Args:
            worker_type: Optional filter by worker type

        Returns:
            List of dicts with worker stats
        """
        where_clause = f"WHERE worker_type = '{worker_type}'" if worker_type else ""

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
            WHERE stopped_at IS NULL
            ORDER BY worker_type, worker_id
        """)

    # UC-6: Analyze System Events
    def get_recent_events(
        self,
        minutes: int = 60,
        category: Optional[str] = None,
        severity: Optional[str] = None,
        limit: int = 100
    ) -> List[Dict[str, Any]]:
        """
        Get recent system events with optional filtering.

        Args:
            minutes: How many minutes back to query
            category: Optional event category filter
            severity: Optional severity filter
            limit: Maximum number of events

        Returns:
            List of event dicts
        """
        where_clauses = [
            f"event_time > datetime('now', '-{minutes} minutes')"
        ]

        if category:
            where_clauses.append(f"event_category = '{category}'")
        if severity:
            where_clauses.append(f"severity = '{severity}'")

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
        """)

    # UC-7: Debug Correlation Flow
    def get_correlation_trace(self, correlation_id: str) -> Dict[str, Any]:
        """
        Get all events and jobs related to a correlation ID.

        Args:
            correlation_id: Correlation ID to trace

        Returns:
            Dict with events and related jobs
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
```

---

## 7. Frontend Design

### 7.1 CLI Interface

```bash
# Command structure
clx-monitor <command> [options]

# Commands:
clx-monitor status              # Overall system health (UC-2)
clx-monitor workers             # Worker status and performance (UC-5)
clx-monitor jobs                # Active and recent jobs (UC-1)
clx-monitor queue               # Queue depth by type (UC-4)
clx-monitor events [--minutes=60] [--category=worker]  # Recent events (UC-6)
clx-monitor job <id>            # Job details and history (UC-3)
clx-monitor trace <correlation-id>  # Correlation trace (UC-7)
clx-monitor export [--format=json|csv] [--output=file]  # Export data
clx-monitor cleanup [--dry-run] # Manual cleanup of old data

# Output format options:
--format=table|json|csv         # Output format (default: table)
--output=<file>                 # Write to file instead of stdout

# Filtering options:
--worker-type=<type>            # Filter by worker type
--status=<status>               # Filter by status
--since=<time>                  # Time filter (e.g., "1h", "30m", "2d")
```

**Example CLI Output:**

```bash
$ clx-monitor status

CLX System Status
=================
Timestamp: 2025-11-13 14:30:15

Workers:
  Total: 4
  Idle: 2
  Busy: 2
  Hung: 0
  Dead: 0

Jobs (last hour):
  Pending: 5
  Processing: 2
  Failed: 0

Overall Status: HEALTHY

$ clx-monitor workers

Worker Performance
==================

ID  Type       Status  Jobs  Failed  Avg Time  Failure %  Uptime
--  ---------- ------- ----- ------- --------- ---------- -------
1   notebook   busy    142   2       12.3s     1.4%       48h
2   notebook   idle    138   1       11.8s     0.7%       48h
3   drawio     busy    89    0       5.2s      0.0%       48h
4   plantuml   idle    76    0       3.1s      0.0%       48h

$ clx-monitor jobs

Active Jobs
===========

Job ID  Type       Worker  Input File                      Duration
------  ---------- ------- ------------------------------- ---------
245     notebook   1       src/course/topic-1/slides.ipynb  15s
246     drawio     3       diagrams/architecture.drawio     8s

Recent Completed (last 10):
[...]

Recent Failed:
(none)
```

### 7.2 Web UI Design

**Technology Choice:**
- **Simple HTML + JavaScript** (no build step, no npm dependencies)
- **SQLite → JSON API** (simple Flask/FastAPI endpoint)
- **Chart.js** for visualization (optional, loaded from CDN)

**Page Structure:**

```
┌─────────────────────────────────────────────┐
│ CLX Monitoring Dashboard                    │
├─────────────────────────────────────────────┤
│                                              │
│ [Dashboard] [Workers] [Jobs] [Events]       │
│                                              │
│ ┌─────────────────────────────────────────┐ │
│ │ System Health                           │ │
│ │                                         │ │
│ │ Status: ● HEALTHY                      │ │
│ │                                         │ │
│ │ Workers: 2 idle, 2 busy, 0 hung        │ │
│ │ Queue: 5 pending, 2 processing         │ │
│ └─────────────────────────────────────────┘ │
│                                              │
│ ┌─────────────────────────────────────────┐ │
│ │ Active Jobs                             │ │
│ │                                         │ │
│ │ Job 245 | notebook | 15s | Worker 1    │ │
│ │   src/course/topic-1/slides.ipynb       │ │
│ │                                         │ │
│ │ Job 246 | drawio | 8s | Worker 3        │ │
│ │   diagrams/architecture.drawio          │ │
│ └─────────────────────────────────────────┘ │
│                                              │
│ ┌─────────────────────────────────────────┐ │
│ │ Worker Performance                      │ │
│ │                                         │ │
│ │ [Bar chart: Jobs processed by worker]   │ │
│ │ [Line chart: Processing time trend]     │ │
│ └─────────────────────────────────────────┘ │
│                                              │
│ Auto-refresh: [On ▼] Every [5 ▼] seconds   │
└─────────────────────────────────────────────┘
```

**Implementation:**

```html
<!-- index.html -->
<!DOCTYPE html>
<html>
<head>
    <title>CLX Monitoring</title>
    <style>
        /* Simple, clean CSS - no framework needed */
        body { font-family: monospace; margin: 20px; }
        .status-healthy { color: green; }
        .status-degraded { color: orange; }
        .status-unhealthy { color: red; }
        table { border-collapse: collapse; width: 100%; }
        th, td { border: 1px solid #ddd; padding: 8px; text-align: left; }
    </style>
</head>
<body>
    <h1>CLX Monitoring Dashboard</h1>

    <div id="health-status"></div>
    <div id="active-jobs"></div>
    <div id="worker-stats"></div>

    <script>
        // Simple polling-based dashboard
        async function fetchStatus() {
            const response = await fetch('/api/status');
            const data = await response.json();
            updateUI(data);
        }

        function updateUI(data) {
            // Update DOM with data
            // ...
        }

        // Auto-refresh every 5 seconds
        setInterval(fetchStatus, 5000);
        fetchStatus();  // Initial load
    </script>
</body>
</html>
```

```python
# Simple API server (clx-monitor-web.py)
from flask import Flask, jsonify
from clx_common.monitoring.queries import MonitoringQueries
from pathlib import Path

app = Flask(__name__)
queries = MonitoringQueries(Path('clx_monitoring.db'))

@app.route('/api/status')
def api_status():
    return jsonify(queries.get_service_health())

@app.route('/api/workers')
def api_workers():
    return jsonify(queries.get_worker_performance())

@app.route('/api/jobs/active')
def api_active_jobs():
    return jsonify(queries.get_active_jobs())

@app.route('/api/events')
def api_events():
    minutes = request.args.get('minutes', 60, type=int)
    return jsonify(queries.get_recent_events(minutes=minutes))

if __name__ == '__main__':
    app.run(host='127.0.0.1', port=8080, debug=False)
```

---

## 8. Implementation Plan

### Phase 1: Foundation (Week 1)
- [ ] Create monitoring database schema
- [ ] Implement MonitoringDB class
- [ ] Implement schema migration system
- [ ] Write unit tests for database operations
- [ ] Add database initialization to schema.py

**Deliverable:** Working monitoring database with schema

### Phase 2: Event Bus (Week 1-2)
- [ ] Implement MonitoringEvent class
- [ ] Implement MonitoringEventBus with async processing
- [ ] Add global event bus initialization
- [ ] Add convenience functions for common events
- [ ] Write unit tests for event bus
- [ ] Add configuration option to enable/disable monitoring

**Deliverable:** Event recording infrastructure

### Phase 3: Integration (Week 2-3)
- [ ] Add event recording to Worker base class
- [ ] Add event recording to JobQueue
- [ ] Add event recording to WorkerPoolManager
- [ ] Update existing heartbeat mechanism to also record events
- [ ] Test event recording with example workers
- [ ] Verify monitoring failures don't impact job processing

**Deliverable:** Fully integrated event recording

### Phase 4: Query API (Week 3)
- [ ] Implement MonitoringQueries class
- [ ] Implement all use case queries (UC-1 through UC-7)
- [ ] Add export functionality (JSON, CSV)
- [ ] Write unit tests for queries
- [ ] Add query performance benchmarks

**Deliverable:** Query API with all use cases

### Phase 5: CLI Interface (Week 4)
- [ ] Create clx-monitor CLI tool
- [ ] Implement all CLI commands
- [ ] Add table formatting for output
- [ ] Add JSON/CSV export options
- [ ] Write CLI tests
- [ ] Write user documentation

**Deliverable:** Functional CLI tool

### Phase 6: Web UI (Week 4-5)
- [ ] Create simple web UI (HTML/JS)
- [ ] Implement Flask API server
- [ ] Add auto-refresh functionality
- [ ] Add basic charting (optional)
- [ ] Test in browser
- [ ] Write deployment documentation

**Deliverable:** Web-based monitoring dashboard

### Phase 7: Cleanup & Optimization (Week 5)
- [ ] Implement retention policies
- [ ] Add automatic cleanup triggers
- [ ] Add manual cleanup command
- [ ] Performance optimization for queries
- [ ] Add database vacuum scheduling
- [ ] Load testing

**Deliverable:** Production-ready monitoring system

### Phase 8: Documentation & Migration (Week 6)
- [ ] Write architecture documentation
- [ ] Write user guide
- [ ] Write operator guide (maintenance, troubleshooting)
- [ ] Create migration guide from logging to monitoring
- [ ] Add examples and tutorials
- [ ] Update existing logging to reduce verbosity

**Deliverable:** Complete documentation and migration path

---

## 9. Migration Strategy

### 9.1 Phased Migration

**Phase 1: Parallel Operation (2 weeks)**
- Enable monitoring alongside existing logging
- Both systems operational
- Validate monitoring data accuracy
- Tune retention policies

**Phase 2: Gradual Logging Reduction (2 weeks)**
- Reduce INFO-level logging for events covered by monitoring
- Keep ERROR logging unchanged
- Monitor for gaps in coverage

**Phase 3: Final Cutover (1 week)**
- Disable/remove redundant logging
- Keep only critical error logging
- Finalize monitoring configuration

### 9.2 Rollback Plan

If monitoring system has issues:
1. Disable monitoring via configuration flag
2. Re-enable detailed logging
3. Fix monitoring issues
4. Re-enable monitoring

**Safety Mechanism:**
```python
# All monitoring calls wrapped in try/except
try:
    record_event(...)
except Exception as e:
    logger.error(f"Monitoring failed: {e}")
    # Continue normal operation
```

### 9.3 Configuration

```yaml
# clx-config.yaml (new file)
monitoring:
  enabled: true
  db_path: clx_monitoring.db
  retention_days: 7
  async_queue_size: 1000

  # Event severity thresholds
  record_debug_events: false
  record_heartbeat_events: false  # Too verbose

  # Performance
  batch_size: 100
  batch_timeout_seconds: 5

logging:
  # Reduced logging levels when monitoring enabled
  level: WARNING
  file: clx.log
  max_size_mb: 100
```

---

## 10. Open Questions

### Q1: Monitoring Database Location
**Question:** Should monitoring DB be in same directory as jobs DB, or configurable separately?

**Options:**
- A) Same directory as CLX_DB_PATH
- B) Separate CLX_MONITORING_DB_PATH env variable
- C) Always in workspace root

**Recommendation:** Option A (same directory) for simplicity.

---

### Q2: Heartbeat Event Recording
**Question:** Should every heartbeat update create an event, or only state changes?

**Analysis:**
- Heartbeats occur every few seconds
- Recording all creates high volume
- Only recording state changes loses temporal precision

**Recommendation:**
- Don't record routine heartbeats as events
- Only record heartbeat failures/recoveries
- Use worker_state table for current heartbeat time

---

### Q3: Event Data Format
**Question:** Should event_data be JSON text or use SQLite JSON functions?

**Analysis:**
- JSON text: Simple, compatible with all SQLite versions
- JSON functions: Better querying, requires SQLite 3.38+

**Recommendation:**
- Use JSON text for maximum compatibility
- Add helper methods for parsing in Python
- Consider JSON functions in future if needed

---

### Q4: Web UI Dependency
**Question:** Should web UI be optional or required?

**Analysis:**
- CLI covers all functionality
- Web UI is convenience feature
- Some users may not need it

**Recommendation:**
- Make web UI optional
- Provide simple Flask server that can be started on-demand
- Document how to run it

---

### Q5: Integration with Existing Prometheus/Grafana
**Question:** Should we integrate with existing Prometheus metrics?

**Analysis:**
- Existing infrastructure already collects some metrics
- Monitoring DB provides finer-grained data
- Could export metrics from monitoring DB

**Recommendation:**
- Phase 1: Separate systems
- Phase 2: Consider adding Prometheus exporter that reads from monitoring DB
- This provides best of both worlds: structured queries + time-series metrics

---

### Q6: Correlation ID Tracking
**Question:** Should correlation ID tracking move entirely to monitoring DB?

**Analysis:**
- Current system tracks in-memory
- Monitoring DB could provide durable storage
- Risk of creating dependency on monitoring for core functionality

**Recommendation:**
- Keep existing in-memory correlation ID tracking for job processing
- Mirror to monitoring DB for analysis
- Monitoring failures don't impact correlation tracking

---

## Summary

This design provides:

1. **Structured Monitoring:** Database-backed event and state tracking
2. **Query Interface:** Pre-defined queries for common diagnostic scenarios
3. **Multiple UIs:** CLI for automation, Web UI for visual exploration
4. **Low Impact:** Async, fail-safe design that doesn't impact job processing
5. **Retention Management:** Automatic cleanup of old data
6. **Migration Path:** Clear path from logging to monitoring

**Key Benefits:**
- Reduced log file size and complexity
- Faster diagnostics with structured queries
- Real-time system visibility
- Historical trend analysis
- Better developer experience

**Next Steps:**
1. Review and approve this design
2. Clarify open questions
3. Begin Phase 1 implementation
4. Iterate based on feedback
