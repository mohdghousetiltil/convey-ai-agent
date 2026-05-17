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

from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from triconvey_agent.db.models import (
    Answer,
    AnswerEvidence,
    AuditLog,
    AutofillJob,
    Client,
    CopyRule,
    CopyRuleAudit,
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
    def _slugify(value: str) -> str:
        import re as _re

        slug = _re.sub(r"[^a-z0-9]+", "-", (value or "").strip().lower()).strip("-")
        return slug[:40] or "firm"

    @staticmethod
    async def ensure_unique_slug(session: AsyncSession, base_value: str) -> str:
        base_slug = ClientRepo._slugify(base_value)
        for attempt in range(100):
            candidate = base_slug if attempt == 0 else f"{base_slug}-{attempt}"
            check = await ClientRepo.get_by_slug(session, candidate)
            if check is None:
                return candidate
        return f"{base_slug}-{uuid.uuid4().hex[:6]}"

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
    async def get_single_active(session: AsyncSession) -> Client | None:
        """Returns the single active client row. Used for desktop (single-tenant) installs.
        Returns None if there are zero or multiple active clients."""
        q = await session.execute(select(Client).where(Client.is_active.is_(True)))
        rows = list(q.scalars().all())
        return rows[0] if len(rows) == 1 else None

    @staticmethod
    async def provision_from_oauth_tenant(
        session: AsyncSession,
        *,
        tenant_id: str,
        email_domain: str,
    ) -> tuple["Client", bool]:
        """Find or create a client from a Microsoft tenant ID.

        Uses `license_key` to store the tenant ID (no schema change needed).
        Returns (client, is_new) — `is_new` is True when the org was just created.
        """
        existing = await ClientRepo.get_by_license(session, tenant_id)
        if existing is not None:
            return existing, False

        safe_slug = await ClientRepo.ensure_unique_slug(session, email_domain)

        client = Client(
            slug=safe_slug,
            name=email_domain,
            license_key=tenant_id,
        )
        session.add(client)
        await session.flush()
        return client, True

    @staticmethod
    async def provision_from_company_name(
        session: AsyncSession,
        *,
        company_name: str,
    ) -> tuple["Client", bool]:
        normalized_name = (company_name or "").strip()
        if not normalized_name:
            raise ValueError("Company name is required.")

        preferred_slug = ClientRepo._slugify(normalized_name)
        existing = await ClientRepo.get_by_slug(session, preferred_slug)
        if existing is not None:
            return existing, False

        client = Client(
            slug=await ClientRepo.ensure_unique_slug(session, normalized_name),
            name=normalized_name,
        )
        session.add(client)
        await session.flush()
        return client, True

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
    async def count_for_client(session: AsyncSession, client_id: uuid.UUID) -> int:
        q = await session.execute(
            select(func.count()).select_from(User).where(User.client_id == client_id)
        )
        return q.scalar_one() or 0

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
        default_role: str = "reviewer",
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

        # 3. New OAuth user — role determined by caller (admin for first user, reviewer otherwise)
        user = User(
            client_id=client_id,
            email=email.lower(),
            name=name,
            role=default_role,
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
        """Find-or-create matter by reference or stable property identifiers.

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

        if existing is None and volume_folio and property_address:
            q = await session.execute(
                select(Matter).where(
                    Matter.client_id == client_id,
                    Matter.volume_folio == volume_folio,
                    Matter.property_address == property_address,
                )
            )
            existing = q.scalar_one_or_none()

        if existing is None and (volume_folio or property_address):
            filters = []
            if volume_folio:
                filters.append(Matter.volume_folio == volume_folio)
            if property_address:
                filters.append(Matter.property_address == property_address)
            q = await session.execute(
                select(Matter).where(
                    Matter.client_id == client_id,
                    or_(*filters),
                )
            )
            # Use first() — OR-filter can match multiple rows if data was
            # imported more than once; pick the most-recently-updated row.
            existing = q.scalars().first()

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
        user_id: uuid.UUID | None = None,
        matter_id: uuid.UUID | None = None,
        model: str = "gpt-4.1-mini",
        use_ai_review: bool = False,
    ) -> Run:
        run = Run(
            id=run_id,
            client_id=client_id,
            user_id=user_id,
            matter_id=matter_id,
            model=model,
            use_ai_review=use_ai_review,
            status="pending",
            progress_pct=0.0,
            progress_status="Starting...",
        )
        session.add(run)
        await session.flush()
        await _enqueue_sync(
            session,
            client_id=client_id,
            entity_type="run",
            entity_id=run.id,
            operation="INSERT",
            payload={
                "status": run.status,
                "model": model,
                "use_ai_review": use_ai_review,
                "user_id": str(user_id) if user_id else None,
                "matter_id": str(matter_id) if matter_id else None,
                "local_only": bool(run.local_only),
                "created_at": run.created_at.isoformat() if run.created_at else None,
            },
        )
        return run

    @staticmethod
    async def get(
        session: AsyncSession, *, client_id: uuid.UUID, run_id: uuid.UUID, user_id: uuid.UUID | None = None
    ) -> Run | None:
        stmt = select(Run).where(Run.id == run_id, Run.client_id == client_id)
        if user_id is not None:
            stmt = stmt.where(Run.user_id == user_id)
        q = await session.execute(stmt)
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
    async def list_recent_with_matter(
        session: AsyncSession,
        *,
        client_id: uuid.UUID,
        user_id: uuid.UUID | None = None,
        limit: int = 10,
    ) -> list[tuple[Run, Matter | None]]:
        stmt = (
            select(Run, Matter)
            .outerjoin(Matter, Matter.id == Run.matter_id)
            .where(Run.client_id == client_id)
            .order_by(Run.created_at.desc())
            .limit(limit)
        )
        if user_id is not None:
            stmt = stmt.where(Run.user_id == user_id)
        q = await session.execute(stmt)
        return [(row[0], row[1]) for row in q.all()]

    @staticmethod
    async def attach_matter(
        session: AsyncSession,
        *,
        client_id: uuid.UUID,
        run_id: uuid.UUID,
        matter_id: uuid.UUID,
    ) -> None:
        values: dict[str, Any] = {"matter_id": matter_id, "updated_at": _now()}
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
        progress_pct: float | None = None,
        progress_status: str | None = None,
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
        if progress_pct is not None:
            values["progress_pct"] = progress_pct
        if progress_status is not None:
            values["progress_status"] = progress_status

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
        pending_sources: list[tuple[Fact, list[dict[str, Any]]]] = []

        for item in facts:
            payload = dict(item)
            sources = payload.pop("sources", [])
            fact = Fact(run_id=run_id, **payload)
            fact_rows.append(fact)
            pending_sources.append((fact, sources))

        session.add_all(fact_rows)
        await session.flush()

        fact_sources: list[FactSource] = []
        for fact, sources in pending_sources:
            if not sources:
                continue
            fact_sources.extend(FactSource(fact_id=fact.id, **s) for s in sources)
        if fact_sources:
            session.add_all(fact_sources)
            await session.flush()
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
        rows = []
        for row in answers:
            payload = dict(row)
            payload["run_id"] = run_id
            rows.append(payload)
        stmt = pg_insert(Answer).values(rows)
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
        # SQLite does not reliably auto-generate values for a BIGINT primary key
        # the same way PostgreSQL does. Assign the audit id explicitly so desktop
        # OAuth/login flows cannot fail on a missing audit_log.id value.
        current_max = await session.execute(select(func.max(AuditLog.id)))
        next_id = int(current_max.scalar_one() or 0) + 1
        session.add(
            AuditLog(
                id=next_id,
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


class CopyRuleRepo:
    @staticmethod
    async def list_for_client(
        session: AsyncSession,
        *,
        client_id: uuid.UUID,
        rule_type: str = "water_authority",
        include_inactive: bool = False,
    ) -> list[CopyRule]:
        stmt = (
            select(CopyRule)
            .where(CopyRule.client_id == client_id, CopyRule.rule_type == rule_type)
            .order_by(CopyRule.authority_name.asc())
        )
        if not include_inactive:
            stmt = stmt.where(CopyRule.is_active.is_(True))
        q = await session.execute(stmt)
        return list(q.scalars().all())

    @staticmethod
    async def get(
        session: AsyncSession,
        *,
        client_id: uuid.UUID,
        rule_id: uuid.UUID,
    ) -> CopyRule | None:
        q = await session.execute(
            select(CopyRule).where(CopyRule.id == rule_id, CopyRule.client_id == client_id)
        )
        return q.scalar_one_or_none()

    @staticmethod
    async def find_water_authority(
        session: AsyncSession,
        *,
        client_id: uuid.UUID,
        authority_name: str,
        min_score: float = 0.86,
    ) -> tuple[CopyRule | None, float]:
        """Fuzzy-match authority_name against active water_authority copy rules.

        Returns (best_matching_rule, score) or (None, best_score_seen).
        Exact normalized match is always preferred over fuzzy.
        Uses the shared copy_rules module so normalization is consistent.
        """
        from triconvey_agent.copy_rules import find_best_copy_rule_match

        rules = await CopyRuleRepo.list_for_client(
            session,
            client_id=client_id,
            rule_type="water_authority",
            include_inactive=False,
        )
        if not rules:
            return None, 0.0

        rule_pairs = [(r.authority_name, float(r.annual_amount)) for r in rules]
        match = find_best_copy_rule_match(
            authority_name,
            rule_pairs,
            minimum_score=min_score,
        )
        if match is None:
            return None, 0.0

        # Find the original CopyRule row that matched
        matched_row = next(
            (r for r in rules if r.authority_name == match.authority_name),
            None,
        )
        return matched_row, match.score

    @staticmethod
    async def create(
        session: AsyncSession,
        *,
        client_id: uuid.UUID,
        rule_type: str,
        authority_name: str,
        annual_amount: float,
        notes: str | None,
        is_active: bool,
        changed_by: str,
    ) -> CopyRule:
        row = CopyRule(
            client_id=client_id,
            rule_type=rule_type,
            authority_name=authority_name.strip(),
            annual_amount=float(annual_amount),
            notes=notes.strip() if isinstance(notes, str) and notes.strip() else None,
            is_active=bool(is_active),
            created_by=changed_by,
        )
        session.add(row)
        await session.flush()

        session.add(
            CopyRuleAudit(
                copy_rule_id=row.id,
                change_type="INSERT",
                changed_by=changed_by,
                new_authority_name=row.authority_name,
                new_annual_amount=row.annual_amount,
                new_is_active=row.is_active,
                new_notes=row.notes,
            )
        )
        await _enqueue_sync(
            session,
            client_id=client_id,
            entity_type="copy_rule",
            entity_id=row.id,
            operation="UPSERT",
            payload={
                "rule_type": row.rule_type,
                "authority_name": row.authority_name,
                "annual_amount": row.annual_amount,
                "notes": row.notes,
                "is_active": row.is_active,
                "created_by": row.created_by,
                "created_at": row.created_at.isoformat(),
                "updated_by": row.updated_by,
                "updated_at": row.updated_at.isoformat() if row.updated_at else None,
            },
        )
        return row

    @staticmethod
    async def update(
        session: AsyncSession,
        *,
        client_id: uuid.UUID,
        rule_id: uuid.UUID,
        authority_name: str,
        annual_amount: float,
        notes: str | None,
        is_active: bool,
        changed_by: str,
    ) -> CopyRule | None:
        row = await CopyRuleRepo.get(session, client_id=client_id, rule_id=rule_id)
        if row is None:
            return None

        before = {
            "authority_name": row.authority_name,
            "annual_amount": row.annual_amount,
            "notes": row.notes,
            "is_active": row.is_active,
        }
        row.authority_name = authority_name.strip()
        row.annual_amount = float(annual_amount)
        row.notes = notes.strip() if isinstance(notes, str) and notes.strip() else None
        row.is_active = bool(is_active)
        row.updated_by = changed_by
        row.updated_at = _now()
        await session.flush()

        session.add(
            CopyRuleAudit(
                copy_rule_id=row.id,
                change_type="UPDATE",
                changed_by=changed_by,
                old_authority_name=before["authority_name"],
                new_authority_name=row.authority_name,
                old_annual_amount=before["annual_amount"],
                new_annual_amount=row.annual_amount,
                old_is_active=before["is_active"],
                new_is_active=row.is_active,
                old_notes=before["notes"],
                new_notes=row.notes,
            )
        )
        await _enqueue_sync(
            session,
            client_id=client_id,
            entity_type="copy_rule",
            entity_id=row.id,
            operation="UPSERT",
            payload={
                "rule_type": row.rule_type,
                "authority_name": row.authority_name,
                "annual_amount": row.annual_amount,
                "notes": row.notes,
                "is_active": row.is_active,
                "created_by": row.created_by,
                "created_at": row.created_at.isoformat(),
                "updated_by": row.updated_by,
                "updated_at": row.updated_at.isoformat() if row.updated_at else None,
            },
        )
        return row

    @staticmethod
    @staticmethod
    async def upsert_water_authority(
        session: AsyncSession,
        *,
        client_id: uuid.UUID,
        authority_name: str,
        annual_amount: float,
        changed_by: str,
    ) -> CopyRule:
        """Create or update a water-authority copy rule by exact authority name.

        If an active rule with the same authority_name already exists, its amount
        is updated in-place (and the change is enqueued for cloud sync).
        Otherwise a new rule is created.
        """
        from sqlalchemy import func as _func

        stmt = (
            select(CopyRule)
            .where(
                CopyRule.client_id == client_id,
                CopyRule.rule_type == "water_authority",
                _func.lower(CopyRule.authority_name) == authority_name.strip().lower(),
            )
            .limit(1)
        )
        q = await session.execute(stmt)
        existing = q.scalar_one_or_none()

        if existing:
            if existing.annual_amount == float(annual_amount) and existing.is_active:
                return existing
            before = {
                "authority_name": existing.authority_name,
                "annual_amount": existing.annual_amount,
                "notes": existing.notes,
                "is_active": existing.is_active,
            }
            existing.annual_amount = float(annual_amount)
            existing.is_active = True
            existing.updated_by = changed_by
            existing.updated_at = _now()
            await session.flush()
            session.add(
                CopyRuleAudit(
                    copy_rule_id=existing.id,
                    change_type="UPDATE",
                    changed_by=changed_by,
                    old_authority_name=before["authority_name"],
                    old_annual_amount=before["annual_amount"],
                    old_is_active=before["is_active"],
                    old_notes=before["notes"],
                    new_authority_name=existing.authority_name,
                    new_annual_amount=existing.annual_amount,
                    new_is_active=existing.is_active,
                    new_notes=existing.notes,
                )
            )
            await _enqueue_sync(
                session,
                client_id=client_id,
                entity_type="copy_rule",
                entity_id=existing.id,
                operation="UPSERT",
                payload={
                    "rule_type": existing.rule_type,
                    "authority_name": existing.authority_name,
                    "annual_amount": existing.annual_amount,
                    "notes": existing.notes,
                    "is_active": existing.is_active,
                    "updated_by": existing.updated_by,
                    "updated_at": existing.updated_at.isoformat() if existing.updated_at else None,
                },
            )
            return existing

        return await CopyRuleRepo.create(
            session,
            client_id=client_id,
            rule_type="water_authority",
            authority_name=authority_name.strip(),
            annual_amount=float(annual_amount),
            notes="Auto-saved after Smokeball push",
            is_active=True,
            changed_by=changed_by,
        )

    @staticmethod
    async def soft_delete(
        session: AsyncSession,
        *,
        client_id: uuid.UUID,
        rule_id: uuid.UUID,
        changed_by: str,
    ) -> CopyRule | None:
        row = await CopyRuleRepo.get(session, client_id=client_id, rule_id=rule_id)
        if row is None:
            return None

        before = {
            "authority_name": row.authority_name,
            "annual_amount": row.annual_amount,
            "notes": row.notes,
            "is_active": row.is_active,
        }
        row.is_active = False
        row.updated_by = changed_by
        row.updated_at = _now()
        await session.flush()

        session.add(
            CopyRuleAudit(
                copy_rule_id=row.id,
                change_type="DELETE",
                changed_by=changed_by,
                old_authority_name=before["authority_name"],
                old_annual_amount=before["annual_amount"],
                old_is_active=before["is_active"],
                old_notes=before["notes"],
                new_authority_name=row.authority_name,
                new_annual_amount=row.annual_amount,
                new_is_active=row.is_active,
                new_notes=row.notes,
            )
        )
        await _enqueue_sync(
            session,
            client_id=client_id,
            entity_type="copy_rule",
            entity_id=row.id,
            operation="DELETE",
            payload={
                "updated_by": row.updated_by,
                "updated_at": row.updated_at.isoformat() if row.updated_at else None,
            },
        )
        return row


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
