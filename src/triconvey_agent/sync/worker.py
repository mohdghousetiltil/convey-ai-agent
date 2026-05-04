"""Background sync worker.

Design:
- Runs as an asyncio task inside the FastAPI process.
- Every N seconds, reads up to `batch_size` rows from `sync_queue` where
  `synced_at IS NULL` and retry_count < max_retries.
- POSTs the batch to the cloud endpoint.
- On 2xx: marks each row `synced_at = NOW()`.
- On 4xx (other than 409): logs and stops retrying that row.
- On 5xx / network error: increments retry_count.
- On 409 Conflict: writes a sync_conflicts row with local_value / remote_value.

Cloud endpoint contract (simple): POST {CONVEY_CLOUD_SYNC_URL}/ingest
with Authorization: Bearer {CONVEY_CLOUD_SYNC_TOKEN}
Body: {"client_id": str, "events": [ {entity_type, entity_id, operation, payload, ts} ... ]}
Response: 200 {"ok": true} | 409 {"conflicts": [{sync_queue_id, remote_value}]}
"""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from datetime import UTC, datetime
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession
from tenacity import AsyncRetrying, RetryError, stop_after_attempt, wait_exponential

from triconvey_agent.db.models import SyncConflict, SyncQueue
from triconvey_agent.db.repositories import SyncQueueRepo
from triconvey_agent.db.session import ensure_runtime_schema, get_session_factory

LOG = logging.getLogger(__name__)


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


class SyncWorker:
    def __init__(
        self,
        *,
        client_id: uuid.UUID,
        cloud_url: str,
        cloud_token: str,
        interval_seconds: int = 60,
        batch_size: int = 100,
    ) -> None:
        self.client_id = client_id
        self.cloud_url = cloud_url.rstrip("/")
        self.cloud_token = cloud_token
        self.interval_seconds = interval_seconds
        self.batch_size = batch_size
        self._task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()
        self._wake_event = asyncio.Event()
        self._http: httpx.AsyncClient | None = None

    # ----- lifecycle -------------------------------------------------------

    async def start(self) -> None:
        if self._task is not None:
            return
        self._http = httpx.AsyncClient(
            timeout=httpx.Timeout(30.0),
            headers={"Authorization": f"Bearer {self.cloud_token}"},
        )
        self._task = asyncio.create_task(self._run(), name="convey-sync-worker")
        LOG.info(
            "SyncWorker started for client %s -> %s (interval=%ss)",
            self.client_id,
            self.cloud_url,
            self.interval_seconds,
        )

    async def stop(self) -> None:
        self._stop_event.set()
        self._wake_event.set()
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, timeout=10)
            except asyncio.TimeoutError:
                self._task.cancel()
            self._task = None
        if self._http is not None:
            await self._http.aclose()
            self._http = None
        LOG.info("SyncWorker stopped.")

    async def nudge(self) -> None:
        """Request an immediate sync cycle instead of waiting for the next poll."""
        self._wake_event.set()

    # ----- main loop -------------------------------------------------------

    async def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                await self._drain_once()
            except Exception as exc:  # don't let one bad cycle kill the loop
                LOG.exception("Sync cycle failed: %s", exc)
            if self._stop_event.is_set():
                break
            self._wake_event.clear()
            stop_wait = asyncio.create_task(self._stop_event.wait())
            wake_wait = asyncio.create_task(self._wake_event.wait())
            try:
                done, pending = await asyncio.wait(
                    {stop_wait, wake_wait},
                    timeout=self.interval_seconds,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                for task in pending:
                    task.cancel()
                if wake_wait in done:
                    self._wake_event.clear()
            finally:
                for task in (stop_wait, wake_wait):
                    if not task.done():
                        task.cancel()

    async def _drain_once(self) -> None:
        await ensure_runtime_schema()
        factory = get_session_factory()
        async with factory() as session:
            rows = await SyncQueueRepo.pending_batch(
                session, client_id=self.client_id, limit=self.batch_size
            )
            if not rows:
                return
            LOG.info("Syncing %d events for client %s", len(rows), self.client_id)
            try:
                conflicts = await self._post_batch(rows)
            except Exception as exc:
                # Whole batch failed; bump retry_count on each row.
                for row in rows:
                    await SyncQueueRepo.mark_failed(session, row.id, str(exc))
                await session.commit()
                return

            ok_ids = [r.id for r in rows if r.id not in conflicts]
            await SyncQueueRepo.mark_synced(session, ok_ids)

            for row in rows:
                if row.id in conflicts:
                    session.add(
                        SyncConflict(
                            client_id=self.client_id,
                            sync_queue_id=row.id,
                            entity_type=row.entity_type,
                            entity_id=row.entity_id,
                            local_value=row.payload_json,
                            remote_value=conflicts[row.id],
                        )
                    )
                    await SyncQueueRepo.mark_failed(
                        session, row.id, "remote conflict — manual resolution required"
                    )
            await session.commit()

    # ----- HTTP ------------------------------------------------------------

    async def _post_batch(
        self, rows: list[SyncQueue]
    ) -> dict[uuid.UUID, dict[str, Any]]:
        """Returns a map of sync_queue_id -> remote_value for conflicts."""
        assert self._http is not None
        body = {
            "client_id": str(self.client_id),
            "events": [
                {
                    "sync_queue_id": str(r.id),
                    "entity_type": r.entity_type,
                    "entity_id": str(r.entity_id),
                    "operation": r.operation,
                    "payload": r.payload_json,
                    "ts": r.created_at.isoformat(),
                }
                for r in rows
            ],
        }

        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(3),
            wait=wait_exponential(multiplier=1, min=2, max=30),
            reraise=True,
        ):
            with attempt:
                resp = await self._http.post(f"{self.cloud_url}/ingest", json=body)
                if resp.status_code == 409:
                    data = resp.json()
                    return {
                        uuid.UUID(c["sync_queue_id"]): c.get("remote_value", {})
                        for c in data.get("conflicts", [])
                    }
                if resp.status_code >= 500:
                    resp.raise_for_status()  # trigger retry
                if resp.status_code >= 400:
                    # 4xx other than 409 — permanent
                    raise RuntimeError(
                        f"Cloud sync rejected batch: {resp.status_code} {resp.text[:500]}"
                    )
                return {}
        return {}


# ---------------------------------------------------------------------------
# Convenience starter (called from FastAPI lifespan)
# ---------------------------------------------------------------------------


async def start_sync_worker(client_id: uuid.UUID) -> SyncWorker | None:
    """Returns a started worker or None if sync is disabled / unconfigured."""
    if not _env_bool("CONVEY_SYNC_ENABLED", True):
        LOG.info("Cloud sync disabled by CONVEY_SYNC_ENABLED=false")
        return None

    url = os.getenv("CONVEY_CLOUD_SYNC_URL", "").strip()
    token = os.getenv("CONVEY_CLOUD_SYNC_TOKEN", "").strip()
    if not url or not token:
        LOG.warning(
            "Cloud sync not configured (CONVEY_CLOUD_SYNC_URL/TOKEN missing); "
            "running local-only."
        )
        return None

    interval = int(os.getenv("CONVEY_SYNC_INTERVAL_SECONDS", "600"))
    batch = int(os.getenv("CONVEY_SYNC_BATCH_SIZE", "100"))

    worker = SyncWorker(
        client_id=client_id,
        cloud_url=url,
        cloud_token=token,
        interval_seconds=interval,
        batch_size=batch,
    )
    await worker.start()
    return worker
