"""Local-first sync layer.

Pattern:
    1. Repos write to local Postgres AND insert a `sync_queue` row.
    2. Background worker drains the queue via HTTPS to the cloud backend.
    3. On transient failures, retries with exponential backoff.
    4. On 409 conflicts, records a `sync_conflicts` row for user resolution.

The worker is started by the FastAPI app (lifespan) and stopped on shutdown.
"""

from triconvey_agent.sync.worker import SyncWorker, start_sync_worker

__all__ = ["SyncWorker", "start_sync_worker"]
