"""Data-access layer.

Every repository method:
- Takes an AsyncSession (injected from FastAPI dep).
- Scopes all queries by `client_id` for tenant isolation.
- Never commits (the session dep handles commit/rollback).
- Enqueues sync events for mutations via `_enqueue_sync` helper.

Business logic (orchestration across repos) belongs in backend/service.py,
not here.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Iterable

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from triconvey_agent.db.models import (
    Answer,
    AnswerEvidence,
    AuditLog,
    AutofillJob,
    Client,
    Document,
    Fact,
    FactConflict,
    FactSource,
    Matter,
    Question,
    Run,
    SessionToken,
    SyncQueue,
    User,
)

# ---------------------------------------------------------------------------
# Sync queue helper — every repo that mutates user-visible state calls this.
# ---------------------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(UTC)


async def _enqueue_sync(
    session: AsyncSession,
    *,
    client_id: uuid.UUID,
    entity_type: str,
    entity_id: uuid.UUID,
    operation: str,
    payload: dict[str, Any],
) -> None:
    """Append a row to `sync_queue` so the worker can push upstream."""
    session.add(
        SyncQueue(
            client_id=client_id,
            entity_type=entity_type,
            entity_id=entity_id,
            operation=operation,
            payload_json=payload,
        )
    )


# ---------------------------------------------------------------------------
# Client / User / Session repositories
# ---------------------------------------------------------------------------


class ClientRepo:
    @staticmethod
    async def get_by_id(session: AsyncSession, client_id: uuid.UUID) -> Client | None:
        return await session.get(Client, client_id)

    @staticmethod
    async def get_by_slug(session: AsyncSession, slug: str) -> Client | None:
        q = await session.execute(select(Client).where(Client.slug == slug))
        return q.scalar_one_or_none()

    @staticmethod
    async def get_by_license(session: AsyncSession, license_key: str) -> Client | None:
        q = await session.execute(select(Client).where(Client.license_key == license_key))
        return q.scalar_one_or_none()

    @staticmethod
    async def ensure_local(
        session: AsyncSession,
        *,
        slug: str,
        name: str,
        license_key: str | None = None,
        version: str | None = None,
    ) -> Client:
        """Used on first boot: creates the client row if missing."""
        existing = await ClientRepo.get_by_slug(session, slug)
        if existing is not None:
            return existing
        client = Client(
            slug=slug,
            name=name,
            license_key=license_key,
            version=version,
        )
        session.add(client)
        await session.flush()
        return client


class UserRepo:
    @staticmethod
    async def get_by_id(session: AsyncSession, user_id: uuid.UUID) -> User | None:
        return await session.get(User, user_id)

    @staticmethod
    async def get_by_email(
        session: AsyncSession, *, client_id: uuid.UUID, email: str
    ) -> User | None:
        q = await session.execute(
            select(User).where(User.client_id == client_id, User.email == email.lower())
        )
        return q.scalar_one_or_none()

    @staticmethod
    async def list_for_client(session: AsyncSession, client_id: uuid.UUID) -> list[User]:
        q = await session.execute(
            select(User).where(User.client_id == client_id, User.is_active.is_(True))
        )
        return list(q.scalars().all())

    @staticmethod
    async def create(
        session: AsyncSession,
        *,
        client_id: uuid.UUID,
        email: str,
        name: str,
        password_hash: str,
        role: str = "reviewer",
    ) -> User:
        user = User(
            client_id=client_id,
            email=email.lower(),
            name=name,
            password_hash=password_hash,
            role=role,
        )
        session.add(user)
        await session.flush()
        await _enqueue_sync(
            session,
            client_id=client_id,
            entity_type="user",
            entity_id=user.id,
            operation="INSERT",
            payload={"email": user.email, "name": user.name, "role": user.role},
        )
        return user

    @staticmethod
    async def mark_login(session: AsyncSession, user_id: uuid.UUID) -> None:
        await session.execute(
            update(User).where(User.id == user_id).values(last_login_at=_now())
        )

    @staticmethod
    async def upsert_oauth_user(
        session: AsyncSession,
        *,
        client_id: uuid.UUID,
        oauth_provider: str,
        oauth_subject: str,
        email: str,
        name: str,
    ) -> User:
        """Find-or-create a user from an OAuth ID token.

        If the user already has a local password account with the same email,
        we link the OAuth provider to it (idempotent). Otherwise we create a
        new OAuth-only account (no password_hash).
        """
        # 1. Try to find by provider + subject (fastest, most precise)
        q = await session.execute(
            select(User).where(
                User.client_id == client_id,
                User.oauth_provider == oauth_provider,
                User.oauth_subject == oauth_subject,
            )
        )
        user = q.scalar_one_or_none()
        if user is not None:
            return user

        # 2. Fallback: find by email (links existing password account)
        q = await session.execute(
            select(User).where(User.client_id == client_id, User.email == email.lower())
        )
        user = q.scalar_one_or_none()
        if user is not None:
            user.oauth_provider = oauth_provider
            user.oauth_subject = oauth_subject
            user.updated_at = _now()
            await session.flush()
            return user

        # 3. New OAuth user — default to reviewer role
        user = User(
            client_id=client_id,
            email=email.lower(),
            name=name,
            role="reviewer",
            oauth_provider=oauth_provider,
            oauth_subject=oauth_subject,
        )
        session.add(user)
        await session.flush()
        await _enqueue_sync(
            session,
            client_id=client_id,
            entity_type="user",
            entity_id=user.id,
            operation="INSERT",
            payload={"email": user.email, "name": user.name, "role": user.role,
                     "oauth_provider": oauth_provider},
        )
        return user


class SessionRepo:
    @staticmethod
    async def create(
        session: AsyncSession,
        *,
        user_id: uuid.UUID,
        token_hash: str,
        ttl: timedelta,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> SessionToken:
        row = SessionToken(
            user_id=user_id,
            token_hash=token_hash,
            expires_at=_now() + ttl,
            ip_address=ip_address,
            user_agent=user_agent,
        )
        session.add(row)
        await session.flush()
        return row

    @staticmethod
    async def get_by_hash(session: AsyncSession, token_hash: str) -> SessionToken | None:
        q = await session.execute(
            select(SessionToken).where(
                SessionToken.token_hash == token_hash,
                SessionToken.expires_at > _now(),
            )
        )
        return q.scalar_one_or_none()

    @staticmethod
    async def revoke(session: AsyncSession, token_hash: str) -> None:
        await session.execute(
            delete(SessionToken).where(SessionToken.token_hash == token_hash)
        )

    @staticmethod
    async def purge_expired(session: AsyncSession) -> int:
        result = await session.execute(
            delete(SessionToken).where(SessionToken.expires_at <= _now())
        )
        return int(result.rowcount or 0)


# ---------------------------------------------------------------------------
# Matter repository
# ---------------------------------------------------------------------------


class MatterRepo:
    @staticmethod
    async def list_for_client(
        session: AsyncSession,
        *,
        client_id: uuid.UUID,
        status: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Matter]:
        stmt = select(Matter).where(Matter.client_id == client_id)
        if status:
            stmt = stmt.where(Matter.status == status)
        stmt = stmt.order_by(Matter.updated_at.desc()).limit(limit).offset(offset)
        q = await session.execute(stmt)
        return list(q.scalars().all())

    @staticmethod
    async def get(
        session: AsyncSession, *, client_id: uuid.UUID, matter_id: uuid.UUID
    ) -> Matter | None:
        q = await session.execute(
            select(Matter).where(Matter.id == matter_id, Matter.client_id == client_id)
        )
        return q.scalar_one_or_none()

    @staticmethod
    async def upsert(
        session: AsyncSession,
        *,
        client_id: uuid.UUID,
        matter_ref: str | None = None,
        property_address: str | None = None,
        volume_folio: str | None = None,
        **kwargs: Any,
    ) -> Matter:
        """Find-or-create matter by `(client_id, matter_ref)`.

        Always updates `updated_at` + any provided fields on existing row.
        """
        existing: Matter | None = None
        if matter_ref:
            q = await session.execute(
                select(Matter).where(
                    Matter.client_id == client_id, Matter.matter_ref == matter_ref
                )
            )
            existing = q.scalar_one_or_none()

        if existing is None:
            matter = Matter(
                client_id=client_id,
                matter_ref=matter_ref,
                property_address=property_address,
                volume_folio=volume_folio,
                **kwargs,
            )
            session.add(matter)
            await session.flush()
        else:
            matter = existing
            for field, value in (
                {"property_address": property_address, "volume_folio": volume_folio, **kwargs}
            ).items():
                if value is not None:
                    setattr(matter, field, value)
            matter.updated_at = _now()

        await _enqueue_sync(
            session,
            client_id=client_id,
            entity_type="matter",
            entity_id=matter.id,
            operation="UPSERT",
            payload={
                "matter_ref": matter.matter_ref,
                "property_address": matter.property_address,
                "volume_folio": matter.volume_folio,
                "status": matter.status,
            },
        )
        return matter


# ---------------------------------------------------------------------------
# Run repository
# ---------------------------------------------------------------------------


class RunRepo:
    @staticmethod
    async def create(
        session: AsyncSession,
        *,
        client_id: uuid.UUID,
        run_id: uuid.UUID,
        matter_id: uuid.UUID | None = None,
        model: str = "gpt-4.1-mini",
        use_ai_review: bool = False,
    ) -> Run:
        run = Run(
            id=run_id,
            client_id=client_id,
            matter_id=matter_id,
            model=model,
            use_ai_review=use_ai_review,
            status="pending",
        )
        session.add(run)
        await session.flush()
        await _enqueue_sync(
            session,
            client_id=client_id,
            entity_type="run",
            entity_id=run.id,
            operation="INSERT",
            payload={"status": run.status, "model": model},
        )
        return run

    @staticmethod
    async def get(
        session: AsyncSession, *, client_id: uuid.UUID, run_id: uuid.UUID
    ) -> Run | None:
        q = await session.execute(
            select(Run).where(Run.id == run_id, Run.client_id == client_id)
        )
        return q.scalar_one_or_none()

    @staticmethod
    async def list_for_client(
        session: AsyncSession,
        *,
        client_id: uuid.UUID,
        status: str | None = None,
        matter_id: uuid.UUID | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Run]:
        stmt = select(Run).where(Run.client_id == client_id)
        if status:
            stmt = stmt.where(Run.status == status)
        if matter_id:
            stmt = stmt.where(Run.matter_id == matter_id)
        stmt = stmt.order_by(Run.created_at.desc()).limit(limit).offset(offset)
        q = await session.execute(stmt)
        return list(q.scalars().all())

    @staticmethod
    async def update_status(
        session: AsyncSession,
        *,
        client_id: uuid.UUID,
        run_id: uuid.UUID,
        status: str,
        error_message: str | None = None,
        completed: bool = False,
        metrics: dict[str, Any] | None = None,
        summary_text: str | None = None,
        document_count: int | None = None,
        total_facts: int | None = None,
    ) -> None:
        values: dict[str, Any] = {"status": status, "updated_at": _now()}
        if error_message is not None:
            values["error_message"] = error_message
        if completed:
            values["completed_at"] = _now()
        if metrics is not None:
            values["metrics_json"] = metrics
        if summary_text is not None:
            values["summary_text"] = summary_text
        if document_count is not None:
            values["document_count"] = document_count
        if total_facts is not None:
            values["total_facts"] = total_facts

        await session.execute(
            update(Run).where(Run.id == run_id, Run.client_id == client_id).values(**values)
        )
        await _enqueue_sync(
            session,
            client_id=client_id,
            entity_type="run",
            entity_id=run_id,
            operation="UPDATE",
            payload={k: _json_safe(v) for k, v in values.items()},
        )


# ---------------------------------------------------------------------------
# Document repository
# ---------------------------------------------------------------------------


class DocumentRepo:
    @staticmethod
    async def create_batch(
        session: AsyncSession,
        *,
        run_id: uuid.UUID,
        documents: Iterable[dict[str, Any]],
    ) -> list[Document]:
        rows = [Document(run_id=run_id, **d) for d in documents]
        session.add_all(rows)
        await session.flush()
        return rows

    @staticmethod
    async def mark_deleted(session: AsyncSession, document_id: uuid.UUID) -> None:
        await session.execute(
            update(Document)
            .where(Document.id == document_id)
            .values(marked_for_deletion=True, deleted_at=_now())
        )

    @staticmethod
    async def for_run(session: AsyncSession, run_id: uuid.UUID) -> list[Document]:
        q = await session.execute(select(Document).where(Document.run_id == run_id))
        return list(q.scalars().all())


# ---------------------------------------------------------------------------
# Fact repository (bulk insert oriented — extraction produces many at once)
# ---------------------------------------------------------------------------


class FactRepo:
    @staticmethod
    async def bulk_insert(
        session: AsyncSession,
        *,
        run_id: uuid.UUID,
        facts: list[dict[str, Any]],
    ) -> list[Fact]:
        """`facts` is a list of dicts with keys matching Fact columns (minus id/run_id).

        Each item may include `sources: list[dict]` — inserted into fact_sources.
        """
        fact_rows: list[Fact] = []
        for item in facts:
            sources = item.pop("sources", [])
            fact = Fact(run_id=run_id, **item)
            session.add(fact)
            fact_rows.append(fact)
            await session.flush()  # get fact.id before inserting sources
            if sources:
                session.add_all([FactSource(fact_id=fact.id, **s) for s in sources])
        return fact_rows

    @staticmethod
    async def for_run(session: AsyncSession, run_id: uuid.UUID) -> list[Fact]:
        q = await session.execute(
            select(Fact).where(Fact.run_id == run_id).order_by(Fact.path)
        )
        return list(q.scalars().all())

    @staticmethod
    async def winning_by_path(
        session: AsyncSession, *, run_id: uuid.UUID
    ) -> dict[str, Fact]:
        q = await session.execute(
            select(Fact).where(Fact.run_id == run_id, Fact.is_winning.is_(True))
        )
        return {f.path: f for f in q.scalars().all()}


# ---------------------------------------------------------------------------
# Question repository (catalog; seldom mutated)
# ---------------------------------------------------------------------------


class QuestionRepo:
    @staticmethod
    async def upsert_all(
        session: AsyncSession, rows: Iterable[dict[str, Any]]
    ) -> None:
        """Bulk upsert the question catalog from YAML registry."""
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        for row in rows:
            stmt = pg_insert(Question).values(**row)
            stmt = stmt.on_conflict_do_update(
                index_elements=[Question.id],
                set_={
                    "tab": stmt.excluded.tab,
                    "label": stmt.excluded.label,
                    "expected_type": stmt.excluded.expected_type,
                    "answer_strategy": stmt.excluded.answer_strategy,
                    "fact_paths": stmt.excluded.fact_paths,
                    "options": stmt.excluded.options,
                    "description": stmt.excluded.description,
                    "value_transform": stmt.excluded.value_transform,
                    "version": stmt.excluded.version,
                    "is_active": stmt.excluded.is_active,
                    "updated_at": _now(),
                },
            )
            await session.execute(stmt)

    @staticmethod
    async def all_active(session: AsyncSession) -> list[Question]:
        q = await session.execute(select(Question).where(Question.is_active.is_(True)))
        return list(q.scalars().all())


# ---------------------------------------------------------------------------
# Answer repository (the one the UI edits — high-frequency mutations)
# ---------------------------------------------------------------------------


class AnswerRepo:
    @staticmethod
    async def bulk_upsert(
        session: AsyncSession,
        *,
        client_id: uuid.UUID,
        run_id: uuid.UUID,
        answers: list[dict[str, Any]],
    ) -> None:
        """Insert new answers or skip if (run_id, question_id) already exists."""
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        if not answers:
            return
        for row in answers:
            row["run_id"] = run_id
            stmt = pg_insert(Answer).values(**row)
            stmt = stmt.on_conflict_do_nothing(
                index_elements=[Answer.run_id, Answer.question_id]
            )
            await session.execute(stmt)

    @staticmethod
    async def for_run(session: AsyncSession, run_id: uuid.UUID) -> list[Answer]:
        q = await session.execute(
            select(Answer).where(Answer.run_id == run_id).order_by(Answer.tab, Answer.label)
        )
        return list(q.scalars().all())

    @staticmethod
    async def apply_human_edit(
        session: AsyncSession,
        *,
        client_id: uuid.UUID,
        run_id: uuid.UUID,
        question_id: str,
        user_id: uuid.UUID,
        new_value: Any,
        needs_review: bool | None = None,
    ) -> Answer | None:
        """Apply a human edit, write audit log, enqueue sync."""
        q = await session.execute(
            select(Answer).where(Answer.run_id == run_id, Answer.question_id == question_id)
        )
        answer = q.scalar_one_or_none()
        if answer is None:
            return None

        before = {
            "value": answer.human_value_json if answer.human_edited else answer.value_json,
            "needs_review": answer.needs_review,
        }

        answer.human_edited = True
        answer.human_value_json = new_value
        answer.human_edited_at = _now()
        answer.human_edited_by = user_id
        answer.needs_sync = True
        answer.is_synced = False
        if needs_review is not None:
            answer.needs_review = needs_review

        await session.flush()

        session.add(
            AuditLog(
                client_id=client_id,
                run_id=run_id,
                user_id=user_id,
                event_type="answer_edited",
                entity_type="answer",
                entity_id=answer.id,
                before_value=_json_safe(before),
                after_value=_json_safe({"value": new_value, "needs_review": answer.needs_review}),
            )
        )
        await _enqueue_sync(
            session,
            client_id=client_id,
            entity_type="answer",
            entity_id=answer.id,
            operation="UPDATE",
            payload={
                "run_id": str(run_id),
                "question_id": question_id,
                "value": _json_safe(new_value),
                "needs_review": answer.needs_review,
                "edited_by": str(user_id),
                "edited_at": answer.human_edited_at.isoformat() if answer.human_edited_at else None,
            },
        )
        return answer


# ---------------------------------------------------------------------------
# Autofill jobs
# ---------------------------------------------------------------------------


class AutofillJobRepo:
    @staticmethod
    async def create(
        session: AsyncSession,
        *,
        run_id: uuid.UUID,
        dry_run: bool,
        skip_review_gate: bool,
        triconvey_exe: str | None = None,
    ) -> AutofillJob:
        job = AutofillJob(
            run_id=run_id,
            dry_run=dry_run,
            skip_review_gate=skip_review_gate,
            triconvey_exe=triconvey_exe,
        )
        session.add(job)
        await session.flush()
        return job

    @staticmethod
    async def get(session: AsyncSession, job_id: uuid.UUID) -> AutofillJob | None:
        return await session.get(AutofillJob, job_id)

    @staticmethod
    async def update_status(
        session: AsyncSession,
        *,
        job_id: uuid.UUID,
        status: str,
        started: bool = False,
        completed: bool = False,
        error: str | None = None,
        totals: dict[str, int] | None = None,
    ) -> None:
        values: dict[str, Any] = {"status": status}
        if started:
            values["started_at"] = _now()
        if completed:
            values["completed_at"] = _now()
        if error is not None:
            values["error"] = error
        if totals:
            values.update(
                {
                    "total_filled": totals.get("filled"),
                    "total_verified": totals.get("verified"),
                    "total_failed": totals.get("failed"),
                    "total_skipped": totals.get("skipped"),
                    "total_pending_review": totals.get("pending_review"),
                }
            )
        await session.execute(
            update(AutofillJob).where(AutofillJob.id == job_id).values(**values)
        )


# ---------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------


class AuditRepo:
    @staticmethod
    async def record(
        session: AsyncSession,
        *,
        client_id: uuid.UUID,
        event_type: str,
        user_id: uuid.UUID | None = None,
        run_id: uuid.UUID | None = None,
        entity_type: str | None = None,
        entity_id: uuid.UUID | None = None,
        before: Any | None = None,
        after: Any | None = None,
        ip_address: str | None = None,
    ) -> None:
        session.add(
            AuditLog(
                client_id=client_id,
                user_id=user_id,
                run_id=run_id,
                event_type=event_type,
                entity_type=entity_type,
                entity_id=entity_id,
                before_value=_json_safe(before) if before is not None else None,
                after_value=_json_safe(after) if after is not None else None,
                ip_address=ip_address,
            )
        )


# ---------------------------------------------------------------------------
# Sync queue repo (for the worker)
# ---------------------------------------------------------------------------


class SyncQueueRepo:
    @staticmethod
    async def pending_batch(
        session: AsyncSession, *, client_id: uuid.UUID, limit: int = 100
    ) -> list[SyncQueue]:
        q = await session.execute(
            select(SyncQueue)
            .where(
                SyncQueue.client_id == client_id,
                SyncQueue.synced_at.is_(None),
                SyncQueue.retry_count < SyncQueue.max_retries,
            )
            .order_by(SyncQueue.created_at.asc())
            .limit(limit)
        )
        return list(q.scalars().all())

    @staticmethod
    async def mark_synced(session: AsyncSession, ids: list[uuid.UUID]) -> None:
        if not ids:
            return
        await session.execute(
            update(SyncQueue).where(SyncQueue.id.in_(ids)).values(synced_at=_now())
        )

    @staticmethod
    async def mark_failed(
        session: AsyncSession, id_: uuid.UUID, error: str
    ) -> None:
        await session.execute(
            update(SyncQueue)
            .where(SyncQueue.id == id_)
            .values(
                retry_count=SyncQueue.retry_count + 1,
                attempted_at=_now(),
                error_message=error[:2000],
            )
        )

    @staticmethod
    async def pending_count(session: AsyncSession, client_id: uuid.UUID) -> int:
        q = await session.execute(
            select(func.count(SyncQueue.id)).where(
                SyncQueue.client_id == client_id, SyncQueue.synced_at.is_(None)
            )
        )
        return int(q.scalar_one())


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


class ActionResultRepo:
    @staticmethod
    async def insert_batch(
        session: AsyncSession,
        *,
        job_id: uuid.UUID,
        results: list[dict[str, Any]],
    ) -> None:
        """Bulk-insert action results after autofill completes."""
        from triconvey_agent.db.models import ActionResult

        session.add_all(
            [ActionResult(job_id=job_id, **{k: v for k, v in r.items() if k != "job_id"})
             for r in results]
        )


def _json_safe(value: Any) -> Any:
    """Serialize arbitrary values for JSONB storage (handles UUID, datetime, Path)."""
    try:
        json.dumps(value)
        return value
    except TypeError:
        return json.loads(json.dumps(value, default=str))
