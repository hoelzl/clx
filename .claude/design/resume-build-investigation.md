# Resume Aborted Builds - Investigation and Design

**Date**: 2025-11-16
**Status**: Investigation Complete, Design Proposed

## Executive Summary

**Current State**: CLX currently has **NO way to resume an aborted build**. When a build is interrupted (timeout, Ctrl+C, etc.), jobs remain in the SQLite database but there's no command to continue processing them.

**Proposed Solution**: Implement a `clx resume` command that processes existing jobs in the database without creating new ones.

---

## Current Build Process

### How `clx build` Works

1. **Initialization** (`main()` in `src/clx/cli/main.py`):
   - Loads course spec from YAML file
   - Creates Course object from spec
   - Initializes SQLite database
   - Starts workers (if configured)
   - Creates SqliteBackend instance

2. **Job Creation** (`course.process_all()`):
   - Iterates through execution stages (defined in `execution_stages()`)
   - For each stage:
     - Gets all files in that stage
     - Calls `file.get_processing_operation()` to create operation
     - Calls `op.execute(backend)` which calls `backend.execute_operation()`
     - This submits jobs to SQLite database
     - Waits for all jobs in stage to complete via `backend.wait_for_completion()`

3. **Job Submission** (`SqliteBackend.execute_operation()`):
   - Checks database cache first (`db_manager.get_result()`)
   - Checks SQLite results cache (`job_queue.check_cache()`)
   - If cached, writes cached result and returns
   - Otherwise, creates new job via `job_queue.add_job()`
   - Adds to `backend.active_jobs` tracking dict
   - Logs job to progress tracker

4. **Job Processing** (Workers):
   - Workers poll database via `job_queue.get_next_job()`
   - Process job and update status via `job_queue.update_job_status()`
   - Write output files to workspace

5. **Waiting for Completion** (`SqliteBackend.wait_for_completion()`):
   - Polls database for job status changes
   - Removes completed/failed jobs from `active_jobs`
   - Periodically cleans up jobs from dead workers
   - Returns when all jobs complete or timeout occurs

---

## Database Schema

### Jobs Table

```sql
CREATE TABLE jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_type TEXT NOT NULL,  -- 'notebook', 'drawio', 'plantuml'
    status TEXT NOT NULL CHECK(status IN ('pending', 'processing', 'completed', 'failed')),
    priority INTEGER DEFAULT 0,

    input_file TEXT NOT NULL,
    output_file TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    payload TEXT NOT NULL,  -- JSON
    correlation_id TEXT,

    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    started_at TIMESTAMP,
    completed_at TIMESTAMP,
    worker_id INTEGER,

    attempts INTEGER DEFAULT 0,
    max_attempts INTEGER DEFAULT 3,
    error TEXT,
    traceback TEXT
);
```

### Job States

- **`pending`**: Job submitted, waiting for worker
- **`processing`**: Worker picked up job and is working on it
- **`completed`**: Job finished successfully
- **`failed`**: Job failed (will retry if attempts < max_attempts)

### Results Cache Table

```sql
CREATE TABLE results_cache (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    output_file TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    result_metadata TEXT,  -- JSON
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_accessed TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    access_count INTEGER DEFAULT 0,

    UNIQUE(output_file, content_hash)
);
```

---

## What Happens When Build is Aborted

When a build is interrupted (timeout, Ctrl+C, system crash, etc.):

1. **Jobs remain in database** in various states:
   - Some in `pending` (never started)
   - Some in `processing` (worker was working on them)
   - Some in `completed` (finished before abort)
   - Some in `failed` (encountered errors)

2. **Workers may continue running**:
   - If using `--no-auto-stop`, workers continue processing
   - Workers will complete jobs in `processing` state
   - Workers will pick up `pending` jobs

3. **No automatic resume**:
   - Running `clx build` again will:
     - Create new course object
     - Call `course.process_all()` which recreates all operations
     - For each file, call `execute_operation()` which checks cache
     - If cached, skip; otherwise create NEW job (duplicate of aborted job)
   - Old jobs in database are ignored

4. **Database cleanup**:
   - `--force-db-init` flag will delete database and start fresh
   - `--ignore-db` flag will skip cache checks and process all files
   - Without these flags, cache may prevent reprocessing already-completed files

---

## Existing Relevant Functionality

### Job Queue Methods (in `JobQueue`)

```python
# Query jobs by status
def get_jobs_by_status(status: str, limit: int = 100) -> List[Job]

# Get job statistics
def get_job_stats() -> Dict[str, Any]  # Counts by status
def get_queue_statistics() -> Dict[str, Any]  # Detailed stats

# Reset hung jobs back to pending
def reset_hung_jobs(timeout_seconds: int = 600) -> int

# Clear old completed jobs
def clear_old_completed_jobs(days: int = 7) -> int
```

### Backend Cleanup (in `SqliteBackend`)

```python
# Clean up jobs from dead workers
def _cleanup_dead_worker_jobs() -> int
```

This method:
- Finds jobs in `processing` state where worker status is `dead`
- Resets them to `pending` state
- Logs warnings about stuck jobs

### Existing CLI Flags

- `--force-db-init`: Delete database and start fresh
- `--ignore-db`: Skip cache checks, process all files
- `--no-auto-start`: Don't start workers automatically
- `--no-auto-stop`: Don't stop workers after build

---

## Why Current Approach Doesn't Support Resume

### Problem 1: Job Creation is Coupled to Course Processing

The `course.process_all()` method:
- Iterates through all files in the course
- Creates operations for each file
- Calls `execute_operation()` which MAY create jobs

There's no separation between:
- "What needs to be processed?" (course structure)
- "What jobs exist in the database?" (job queue state)

### Problem 2: Cache Checks are Per-Operation

Cache checking happens inside `execute_operation()`:
```python
# Check database cache
if not self.ignore_db and self.db_manager:
    result = self.db_manager.get_result(...)
    if result:
        # Write cached result
        return

# Check SQLite job cache
if self.job_queue:
    cached = self.job_queue.check_cache(...)
    if cached:
        return
```

This means:
- You must recreate all operations to benefit from caching
- There's no way to say "just finish the pending jobs"

### Problem 3: No Job-to-File Mapping

The database stores:
- `input_file` and `output_file` paths
- `payload` with processing parameters

But there's no easy way to:
- Map jobs back to CourseFile objects
- Determine if a job is still relevant (file may have changed)
- Handle partially completed multi-stage processing

---

## Design: `clx resume` Command

### Overview

A new CLI command that:
1. **Does NOT create any new jobs**
2. Queries database for incomplete jobs
3. Cleans up stuck/hung jobs
4. Waits for workers to complete remaining jobs
5. Reports on what was resumed

### Command Interface

```bash
# Basic usage - resume all incomplete jobs
clx resume

# Specify database path
clx resume --db-path=/path/to/clx_jobs.db

# Additional options
clx resume --reset-hung            # Reset hung jobs before resuming
clx resume --timeout=600           # Max wait time (seconds)
clx resume --force-retry-failed    # Retry failed jobs
```

### Implementation Plan

#### 1. Add `resume` Command to CLI

**Location**: `src/clx/cli/main.py`

```python
@cli.command()
@click.option(
    "--db-path",
    type=click.Path(exists=True, path_type=Path),
    help="Path to SQLite database (auto-detected if not specified)",
)
@click.option(
    "--reset-hung",
    is_flag=True,
    help="Reset hung jobs before resuming",
)
@click.option(
    "--timeout",
    type=int,
    default=1200,
    help="Maximum wait time in seconds (default: 1200)",
)
@click.option(
    "--force-retry-failed",
    is_flag=True,
    help="Reset failed jobs to retry them",
)
@click.option(
    "--workspace",
    type=click.Path(exists=True, path_type=Path),
    help="Workspace path (required for writing output files)",
)
@click.pass_context
def resume(ctx, db_path, reset_hung, timeout, force_retry_failed, workspace):
    """Resume processing incomplete jobs from database.

    This command continues processing jobs from a previous build that was
    interrupted (timeout, Ctrl+C, etc.). It does NOT create new jobs - it
    only processes jobs already in the database.

    Examples:
        clx resume
        clx resume --reset-hung
        clx resume --db-path=/data/clx_jobs.db --workspace=/data/output
    """
    asyncio.run(
        resume_build(
            ctx,
            db_path,
            reset_hung,
            timeout,
            force_retry_failed,
            workspace,
        )
    )
```

#### 2. Implement `resume_build()` Function

**Core Logic**:

```python
async def resume_build(
    ctx,
    db_path: Optional[Path],
    reset_hung: bool,
    timeout: int,
    force_retry_failed: bool,
    workspace: Optional[Path],
):
    """Resume processing incomplete jobs."""

    # 1. Auto-detect database path if not specified
    if not db_path:
        db_path = auto_detect_db_path()

    if not db_path.exists():
        click.echo(f"Error: Database not found: {db_path}", err=True)
        return 1

    # 2. Determine workspace path
    if not workspace:
        workspace = db_path.parent  # Default to database directory

    # 3. Initialize job queue
    job_queue = JobQueue(db_path)

    # 4. Check for incomplete jobs
    incomplete_jobs = []

    # Get pending jobs
    pending = job_queue.get_jobs_by_status('pending', limit=10000)
    incomplete_jobs.extend(pending)

    # Get processing jobs (may be stuck or actively processing)
    processing = job_queue.get_jobs_by_status('processing', limit=10000)
    incomplete_jobs.extend(processing)

    # Optionally include failed jobs
    if force_retry_failed:
        failed = job_queue.get_jobs_by_status('failed', limit=10000)
        # Filter to only retry jobs that haven't exceeded max attempts
        retryable_failed = [j for j in failed if j.attempts < j.max_attempts]
        incomplete_jobs.extend(retryable_failed)

    if not incomplete_jobs:
        click.echo("No incomplete jobs found in database")
        return 0

    click.echo(f"Found {len(incomplete_jobs)} incomplete job(s)")
    click.echo(f"  Pending: {len(pending)}")
    click.echo(f"  Processing: {len(processing)}")
    if force_retry_failed:
        click.echo(f"  Failed (retryable): {len(retryable_failed)}")

    # 5. Reset hung jobs if requested
    if reset_hung:
        click.echo("Resetting hung jobs...")
        reset_count = job_queue.reset_hung_jobs(timeout_seconds=600)
        if reset_count > 0:
            click.echo(f"  Reset {reset_count} hung job(s)")

    # 6. Check for available workers
    from clx.infrastructure.workers.discovery import WorkerDiscovery
    discovery = WorkerDiscovery(db_path)
    workers = discovery.discover_workers(status_filter=['idle', 'busy'])

    if not workers:
        click.echo("Warning: No workers found", err=True)
        click.echo("Start workers with 'clx start-services' or 'clx build --no-auto-stop'")
        click.echo("Resume will wait for workers to become available...")
    else:
        click.echo(f"Found {len(workers)} worker(s)")
        from collections import Counter
        counts = Counter(w.worker_type for w in workers)
        for worker_type, count in sorted(counts.items()):
            click.echo(f"  {worker_type}: {count}")

    # 7. Create backend and populate active_jobs
    from clx.infrastructure.backends.sqlite_backend import SqliteBackend
    from clx.infrastructure.database.db_operations import DatabaseManager

    with DatabaseManager(db_path) as db_manager:
        backend = SqliteBackend(
            db_path=db_path,
            workspace_path=workspace,
            db_manager=db_manager,
            max_wait_for_completion_duration=timeout,
        )

        # Populate active_jobs from incomplete jobs
        for job in incomplete_jobs:
            backend.active_jobs[job.id] = {
                'job_type': job.job_type,
                'input_file': job.input_file,
                'output_file': job.output_file,
                'correlation_id': job.correlation_id,
            }

        # Reset failed jobs to pending if force_retry_failed
        if force_retry_failed:
            for job in retryable_failed:
                job_queue.update_job_status(job.id, 'pending')

        # 8. Wait for completion
        async with backend:
            click.echo(f"\nWaiting for jobs to complete (timeout: {timeout}s)...")
            try:
                success = await backend.wait_for_completion()

                if success:
                    click.echo("\n✓ All jobs completed successfully")
                    return 0
                else:
                    click.echo("\n✗ Some jobs failed", err=True)
                    return 1

            except TimeoutError as e:
                click.echo(f"\n✗ Timeout: {e}", err=True)

                # Show remaining jobs
                remaining = len(backend.active_jobs)
                if remaining > 0:
                    click.echo(f"\n{remaining} job(s) still incomplete:")
                    for job_id in list(backend.active_jobs.keys())[:10]:
                        job_info = backend.active_jobs[job_id]
                        click.echo(f"  #{job_id}: {job_info['input_file']}")
                    if remaining > 10:
                        click.echo(f"  ... and {remaining - 10} more")

                return 2
            except Exception as e:
                click.echo(f"\n✗ Error: {e}", err=True)
                logger.error(f"Resume failed: {e}", exc_info=True)
                return 1
```

#### 3. Helper: Auto-detect Database Path

```python
def auto_detect_db_path() -> Optional[Path]:
    """Auto-detect database path.

    Checks in order:
    1. clx_jobs.db (current directory)
    2. clx_cache.db (current directory)
    3. Most recently modified .db file in current directory
    """
    candidates = [
        Path('clx_jobs.db'),
        Path('clx_cache.db'),
    ]

    for path in candidates:
        if path.exists():
            return path

    # Find most recent .db file
    db_files = list(Path('.').glob('*.db'))
    if db_files:
        return max(db_files, key=lambda p: p.stat().st_mtime)

    return None
```

---

## Implementation Steps

### Phase 1: Basic Resume (Minimal Viable Product)

1. **Add `resume` command to CLI** (`src/clx/cli/main.py`)
   - Command definition with options
   - Basic argument validation

2. **Implement `resume_build()` function** (`src/clx/cli/main.py` or new module)
   - Query incomplete jobs from database
   - Populate `backend.active_jobs`
   - Call `backend.wait_for_completion()`

3. **Add auto-detect database path helper**
   - Check common database locations
   - Return most recently modified .db file

4. **Test basic functionality**
   - Manual testing with interrupted builds
   - Verify jobs complete correctly
   - Verify output files are written

### Phase 2: Enhanced Features

5. **Add `--reset-hung` option**
   - Call `job_queue.reset_hung_jobs()` before resuming
   - Report number of jobs reset

6. **Add `--force-retry-failed` option**
   - Query failed jobs
   - Filter retryable jobs (attempts < max_attempts)
   - Reset to pending state

7. **Improve worker availability checks**
   - Warn if no workers available
   - Show worker counts by type
   - Optionally wait for workers to register

8. **Add progress reporting**
   - Show initial job counts
   - Use progress tracker during execution
   - Report final statistics

### Phase 3: Advanced Features

9. **Add `--dry-run` option**
   - Show what would be resumed without actually processing
   - Display job details (type, input/output files, status)

10. **Add job filtering options**
    - `--job-type=notebook` - Only resume specific job types
    - `--max-jobs=N` - Limit number of jobs to resume
    - `--min-age=MINUTES` - Only resume jobs older than N minutes

11. **Add interactive mode**
    - Show job details and ask for confirmation
    - Allow selecting which jobs to resume

12. **Add result verification**
    - Check if output files actually exist
    - Verify file sizes/checksums match expected results
    - Optionally re-queue jobs with missing outputs

---

## Edge Cases to Handle

### 1. No Workers Available

**Problem**: User runs `clx resume` but no workers are running.

**Solution**:
- Detect worker availability before resuming
- Warn user if no workers found
- Either:
  - Wait indefinitely for workers to register (with periodic status updates)
  - Or fail fast and tell user to start workers first

### 2. Jobs with Dead Workers

**Problem**: Jobs stuck in `processing` state because worker crashed.

**Solution**:
- Existing `_cleanup_dead_worker_jobs()` handles this
- It's called periodically during `wait_for_completion()`
- Jobs are automatically reset to `pending`

### 3. Hung Jobs

**Problem**: Jobs in `processing` state for too long (worker stuck).

**Solution**:
- Use `--reset-hung` flag to explicitly reset before resuming
- Or use existing `reset_hung_jobs()` with timeout parameter

### 4. Failed Jobs

**Problem**: Jobs in `failed` state that may be retryable.

**Solution**:
- By default, don't retry failed jobs
- Use `--force-retry-failed` to explicitly retry them
- Only retry jobs where `attempts < max_attempts`

### 5. Database Not Found

**Problem**: User forgets to specify database path.

**Solution**:
- Auto-detect common database locations
- Fail with helpful error if not found
- Suggest using `--db-path` flag

### 6. Output Files Already Exist

**Problem**: Resume may overwrite existing output files.

**Solution**:
- Workers already handle this - they write to output_file path
- Existing files are overwritten (same as normal build)
- No special handling needed

### 7. Course Files Changed Since Abort

**Problem**: Source files may have changed since jobs were created.

**Solution**:
- **Phase 1**: Ignore this - just process existing jobs
- **Future enhancement**: Check content_hash of input files
  - If changed, mark job as stale and skip
  - Or automatically update payload with new content

### 8. Database Schema Mismatch

**Problem**: Database from older CLX version may have different schema.

**Solution**:
- Schema migration is already handled by `init_database()`
- Database version is tracked in `schema_version` table
- Migrations run automatically on connection

---

## Testing Plan

### Unit Tests

1. **Test `resume_build()` with various job states**
   - Only pending jobs
   - Only processing jobs
   - Mix of pending/processing/failed
   - No incomplete jobs (all completed)

2. **Test auto-detect database path**
   - Multiple .db files present
   - Only one .db file
   - No .db files

3. **Test job filtering**
   - `--force-retry-failed` includes failed jobs
   - Without flag, failed jobs excluded

### Integration Tests

4. **Test resume after abort**
   - Start build with timeout
   - Let some jobs complete
   - Abort build
   - Run resume
   - Verify all jobs complete

5. **Test resume with dead workers**
   - Start build
   - Kill worker process
   - Run resume
   - Verify stuck jobs are reset and completed

6. **Test resume with no workers**
   - Create jobs in database
   - Stop all workers
   - Run resume (should wait or fail gracefully)

### End-to-End Tests

7. **Test realistic abort scenario**
   - Large course with many files
   - Interrupt build midway (Ctrl+C)
   - Verify database state
   - Run resume
   - Verify output matches full build

---

## Documentation Updates

### User Documentation

1. **Update user guide** (`docs/user-guide/`)
   - Add section on resuming builds
   - Explain when to use `clx resume` vs `clx build`
   - Document command options

2. **Update quick start** (`docs/user-guide/quick-start.md`)
   - Add example of resuming interrupted build

3. **Update troubleshooting** (`docs/user-guide/troubleshooting.md`)
   - Add section on "Build was interrupted"
   - Explain how to resume
   - Common issues and solutions

### Developer Documentation

4. **Update architecture docs** (`docs/developer-guide/architecture.md`)
   - Document resume functionality
   - Explain job lifecycle and states
   - Describe how resume differs from build

5. **Update CLAUDE.md**
   - Add resume command to CLI reference
   - Document implementation details
   - Add to common tasks section

---

## Alternative Approaches Considered

### Alternative 1: Smart Cache-Based Resume

**Idea**: Enhance `clx build` with a `--resume` flag that:
- Checks database for existing jobs
- Skips creating new jobs if old jobs exist
- Reuses old jobs instead

**Pros**:
- Single command for build and resume
- Works with existing course processing logic

**Cons**:
- Couples job queue state with course structure
- Harder to reason about (is it building or resuming?)
- More complex implementation
- Risk of confusion (when does it build vs resume?)

**Decision**: Rejected - prefer explicit separate command

### Alternative 2: Automatic Resume in `clx build`

**Idea**: Make `clx build` automatically detect incomplete jobs and finish them.

**Pros**:
- No new command needed
- Seamless user experience

**Cons**:
- Surprising behavior (may process stale jobs)
- No way to force fresh build without `--force-db-init`
- Harder to debug
- May process old jobs that are no longer relevant

**Decision**: Rejected - prefer explicit control

### Alternative 3: Job Manifest File

**Idea**: Save job manifest to file during build, resume from manifest.

**Pros**:
- Decouples from database
- Can version-control job manifests
- Clear record of what needs processing

**Cons**:
- Adds complexity (manifest file management)
- Manifest can get out of sync with database
- Redundant with database job table

**Decision**: Rejected - database already has all needed info

---

## Future Enhancements

### 1. Job Dependency Tracking

Currently jobs are independent. Future enhancement:
- Track dependencies between jobs (e.g., "generate HTML after notebook execution")
- Resume respects dependencies
- Skip downstream jobs if upstream failed

### 2. Partial Course Resume

Currently resume processes ALL incomplete jobs. Future enhancement:
- Allow resuming specific files/topics/sections
- Filter jobs by input file pattern
- Interactive selection of jobs to resume

### 3. Incremental Build

Currently build recreates all operations. Future enhancement:
- `clx build --incremental` that:
  - Checks database for existing jobs
  - Only creates new jobs for changed files
  - Reuses existing jobs for unchanged files
- Essentially merges build and resume

### 4. Job Garbage Collection

Currently old jobs accumulate in database. Future enhancement:
- Automatic cleanup of old completed jobs
- Configurable retention period
- Vacuum database to reclaim space

### 5. Job Result Validation

Currently assumes output file existence = success. Future enhancement:
- Validate output files after job completion
- Check file size, format, integrity
- Re-queue job if validation fails

---

## Conclusion

**Recommended Implementation**: Phase 1 (Basic Resume)

This provides:
- ✅ Minimal implementation complexity
- ✅ Clear separation of concerns (build vs resume)
- ✅ Explicit user control
- ✅ Works with existing backend infrastructure
- ✅ No changes to existing build process
- ✅ Extensible for future enhancements

**Estimated Effort**:
- Phase 1: 4-6 hours (command + basic logic + testing)
- Phase 2: 2-3 hours (enhanced options + reporting)
- Phase 3: 4-6 hours (advanced features + documentation)

**Total**: ~10-15 hours for complete implementation

---

## Appendix: Code References

### Key Files

- **CLI**: `src/clx/cli/main.py` - Add resume command here
- **Backend**: `src/clx/infrastructure/backends/sqlite_backend.py` - Reuse wait_for_completion()
- **Job Queue**: `src/clx/infrastructure/database/job_queue.py` - Query methods
- **Schema**: `src/clx/infrastructure/database/schema.py` - Job states

### Key Methods

```python
# Query jobs
JobQueue.get_jobs_by_status(status: str) -> List[Job]
JobQueue.get_job_stats() -> Dict[str, Any]
JobQueue.reset_hung_jobs(timeout_seconds: int) -> int

# Backend operations
SqliteBackend.wait_for_completion() -> bool
SqliteBackend._cleanup_dead_worker_jobs() -> int

# Worker discovery
WorkerDiscovery.discover_workers(status_filter: List[str]) -> List[WorkerInfo]
```

### Job States Flow

```
[pending] --worker picks up--> [processing] --success--> [completed]
                                     |
                                   fail
                                     |
                                     v
                                 [failed] --retry--> [pending]
                                            (if attempts < max_attempts)
```
