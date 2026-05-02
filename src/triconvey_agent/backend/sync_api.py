from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from triconvey_agent.auth.deps import require_api_token
from triconvey_agent.db.models import Answer, CopyRule, Matter, Run
from triconvey_agent.db.repositories import ClientRepo
from triconvey_agent.db.session import get_session

LOG = logging.getLogger(__name__)

router = APIRouter(prefix="/api/sync", tags=["sync"])


class SyncEvent(BaseModel):
    sync_queue_id: str
    entity_type: str
    entity_id: str
    operation: str
    payload: dict[str, Any] = Field(default_factory=dict)
    ts: str


class SyncBatchRequest(BaseModel):
    client_id: str
    events: list[SyncEvent] = Field(default_factory=list)


class SyncConflictDetail(BaseModel):
    sync_queue_id: str
    remote_value: dict[str, Any] = Field(default_factory=dict)


class SyncBatchResponse(BaseModel):
    ok: bool = True
    conflicts: list[SyncConflictDetail] = Field(default_factory=list)


def _parse_uuid(value: str, field_name: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(value))
    except ValueError as exc:
        raise ValueError(f"Invalid {field_name}: {value}") from exc


def _parse_optional_uuid(value: Any) -> uuid.UUID | None:
    if value in (None, ""):
        return None
    return _parse_uuid(str(value), "uuid")


def _parse_optional_datetime(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    text = str(value)
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    return datetime.fromisoformat(text)


def _serialize_model(instance: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for column in instance.__table__.columns:  # type: ignore[attr-defined]
        value = getattr(instance, column.name)
        if isinstance(value, uuid.UUID):
            payload[column.name] = str(value)
        elif isinstance(value, datetime):
            payload[column.name] = value.astimezone(UTC).isoformat()
        else:
            payload[column.name] = value
    return payload


def _normalize_operation(value: str) -> str:
    return str(value or "").strip().upper()


def _conflict(sync_queue_id: str, remote_value: dict[str, Any] | None = None) -> SyncConflictDetail:
    return SyncConflictDetail(sync_queue_id=sync_queue_id, remote_value=remote_value or {})


@router.post("/ingest", response_model=SyncBatchResponse)
async def ingest_sync_batch(
    req: SyncBatchRequest,
    token_claims: dict[str, Any] = Depends(require_api_token),
    session: AsyncSession = Depends(get_session),
) -> SyncBatchResponse:
    client_id = _parse_uuid(req.client_id, "client_id")

    token_client_id = token_claims.get("cid")
    if str(token_client_id) != str(client_id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Client mismatch on sync token.")

    client = await ClientRepo.get_by_id(session, client_id)
    if client is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Client not found.")
    if not client.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Client is inactive.")
    if not client.sync_enabled:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Sync disabled for this client.")

    conflicts: list[SyncConflictDetail] = []
    applied_any = False

    for event in req.events:
        try:
            async with session.begin_nested():
                maybe_conflict = await _apply_event(session, client_id, event)
        except Exception as exc:
            LOG.exception("Failed to apply sync event %s", event.sync_queue_id)
            maybe_conflict = _conflict(event.sync_queue_id, {"error": str(exc)})
        if maybe_conflict is not None:
            conflicts.append(maybe_conflict)
            continue
        applied_any = True

    if conflicts:
        if applied_any:
            client.last_synced_at = datetime.now(UTC)
            await session.commit()
        else:
            await session.rollback()
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content=SyncBatchResponse(ok=False, conflicts=conflicts).model_dump(mode="json"),
        )

    client.last_synced_at = datetime.now(UTC)
    await session.commit()
    return SyncBatchResponse(ok=True, conflicts=[])


async def _apply_event(
    session: AsyncSession,
    client_id: uuid.UUID,
    event: SyncEvent,
) -> SyncConflictDetail | None:
    entity_id = _parse_uuid(event.entity_id, "entity_id")
    entity_type = str(event.entity_type or "").strip().lower()

    if entity_type == "run":
        return await _apply_run_event(session, client_id, entity_id, event)
    if entity_type == "answer":
        return await _apply_answer_event(session, client_id, entity_id, event)
    if entity_type == "matter":
        return await _apply_matter_event(session, client_id, entity_id, event)
    if entity_type == "copy_rule":
        return await _apply_copy_rule_event(session, client_id, entity_id, event)
    raise ValueError(f"Unknown entity_type: {event.entity_type}")


async def _apply_run_event(
    session: AsyncSession,
    client_id: uuid.UUID,
    run_id: uuid.UUID,
    event: SyncEvent,
) -> SyncConflictDetail | None:
    existing = await session.get(Run, run_id)
    payload = dict(event.payload or {})
    operation = _normalize_operation(event.operation)

    if existing is not None and existing.client_id != client_id:
        return _conflict(event.sync_queue_id, _serialize_model(existing))

    if operation in {"INSERT", "CREATE"}:
        if existing is not None:
            return _conflict(event.sync_queue_id, _serialize_model(existing))
        row = Run(
            id=run_id,
            client_id=client_id,
            user_id=_parse_optional_uuid(payload.get("user_id")),
            matter_id=_parse_optional_uuid(payload.get("matter_id")),
            status=str(payload.get("status") or "pending"),
            model=str(payload.get("model") or "gpt-4.1-mini"),
            use_ai_review=bool(payload.get("use_ai_review", False)),
            document_count=payload.get("document_count"),
            total_facts=payload.get("total_facts"),
            summary_text=payload.get("summary_text"),
            metrics_json=payload.get("metrics_json") or {},
            error_message=payload.get("error_message"),
            completed_at=_parse_optional_datetime(payload.get("completed_at")),
            local_only=bool(payload.get("local_only", False)),
        )
        session.add(row)
        await session.flush()
        return None

    if operation in {"UPDATE", "UPSERT"}:
        if existing is None:
            if operation == "UPSERT":
                row = Run(
                    id=run_id,
                    client_id=client_id,
                    user_id=_parse_optional_uuid(payload.get("user_id")),
                    matter_id=_parse_optional_uuid(payload.get("matter_id")),
                    status=str(payload.get("status") or "pending"),
                    model=str(payload.get("model") or "gpt-4.1-mini"),
                    use_ai_review=bool(payload.get("use_ai_review", False)),
                    document_count=payload.get("document_count"),
                    total_facts=payload.get("total_facts"),
                    summary_text=payload.get("summary_text"),
                    metrics_json=payload.get("metrics_json") or {},
                    error_message=payload.get("error_message"),
                    completed_at=_parse_optional_datetime(payload.get("completed_at")),
                    local_only=bool(payload.get("local_only", False)),
                )
                session.add(row)
                await session.flush()
                return None
            return _conflict(event.sync_queue_id, {})
        for key in ("status", "model", "document_count", "total_facts", "summary_text", "metrics_json", "error_message"):
            if key in payload:
                setattr(existing, key, payload[key])
        if "use_ai_review" in payload:
            existing.use_ai_review = bool(payload["use_ai_review"])
        if "user_id" in payload:
            existing.user_id = _parse_optional_uuid(payload.get("user_id"))
        if "matter_id" in payload:
            existing.matter_id = _parse_optional_uuid(payload.get("matter_id"))
        if "completed_at" in payload:
            existing.completed_at = _parse_optional_datetime(payload.get("completed_at"))
        if "local_only" in payload:
            existing.local_only = bool(payload["local_only"])
        existing.updated_at = datetime.now(UTC)
        await session.flush()
        return None

    if operation == "DELETE":
        if existing is None:
            return None
        await session.delete(existing)
        await session.flush()
        return None

    raise ValueError(f"Unknown run operation: {event.operation}")


async def _apply_answer_event(
    session: AsyncSession,
    client_id: uuid.UUID,
    answer_id: uuid.UUID,
    event: SyncEvent,
) -> SyncConflictDetail | None:
    existing = await session.get(Answer, answer_id)
    payload = dict(event.payload or {})
    operation = _normalize_operation(event.operation)

    if existing is not None:
        owning_run = await session.get(Run, existing.run_id)
        if owning_run is None or owning_run.client_id != client_id:
            return _conflict(event.sync_queue_id, _serialize_model(existing))

    if operation in {"INSERT", "CREATE"}:
        if existing is not None:
            return _conflict(event.sync_queue_id, _serialize_model(existing))
        run_id = _parse_uuid(str(payload.get("run_id") or ""), "run_id")
        owning_run = await session.get(Run, run_id)
        if owning_run is None or owning_run.client_id != client_id:
            return _conflict(event.sync_queue_id, {})
        value = payload.get("value", payload.get("value_json"))
        question_id = str(payload.get("question_id") or payload.get("question_key") or "")
        row = Answer(
            id=answer_id,
            run_id=run_id,
            question_id=question_id,
            tab=str(payload.get("tab") or ""),
            label=str(payload.get("label") or question_id),
            expected_type=payload.get("expected_type"),
            answer_strategy=payload.get("answer_strategy"),
            options=payload.get("options"),
            description=payload.get("description"),
            value_json=value,
            human_edited=bool(payload.get("human_edited", False)),
            human_value_json=payload.get("human_value_json"),
            needs_review=bool(payload.get("needs_review", False)),
            review_reasons=payload.get("review_reasons"),
            presentation_hints=payload.get("presentation_hints") or {},
        )
        session.add(row)
        await session.flush()
        return None

    if operation in {"UPDATE", "UPSERT"}:
        if existing is None:
            return _conflict(event.sync_queue_id, {})
        if "value" in payload:
            existing.value_json = payload.get("value")
            existing.human_value_json = payload.get("value")
            existing.human_edited = True
        if "value_json" in payload:
            existing.value_json = payload.get("value_json")
        if "needs_review" in payload:
            existing.needs_review = bool(payload.get("needs_review"))
        if "review_reasons" in payload:
            existing.review_reasons = payload.get("review_reasons")
        if "presentation_hints" in payload:
            existing.presentation_hints = payload.get("presentation_hints") or {}
        if "edited_by" in payload:
            existing.human_edited_by = _parse_optional_uuid(payload.get("edited_by"))
        if "edited_at" in payload:
            existing.human_edited_at = _parse_optional_datetime(payload.get("edited_at"))
        await session.flush()
        return None

    if operation == "DELETE":
        if existing is None:
            return None
        await session.delete(existing)
        await session.flush()
        return None

    raise ValueError(f"Unknown answer operation: {event.operation}")


async def _apply_matter_event(
    session: AsyncSession,
    client_id: uuid.UUID,
    matter_id: uuid.UUID,
    event: SyncEvent,
) -> SyncConflictDetail | None:
    existing = await session.get(Matter, matter_id)
    payload = dict(event.payload or {})
    operation = _normalize_operation(event.operation)

    if existing is not None and existing.client_id != client_id:
        return _conflict(event.sync_queue_id, _serialize_model(existing))

    if operation in {"INSERT", "CREATE"}:
        if existing is not None:
            return _conflict(event.sync_queue_id, _serialize_model(existing))
        row = Matter(
            id=matter_id,
            client_id=client_id,
            matter_ref=payload.get("matter_ref"),
            property_address=payload.get("property_address"),
            volume_folio=payload.get("volume_folio"),
            council_area=payload.get("council_area"),
            water_authority=payload.get("water_authority"),
            property_type=payload.get("property_type"),
            vendor_count=payload.get("vendor_count"),
            status=str(payload.get("status") or "active"),
        )
        session.add(row)
        await session.flush()
        return None

    if operation in {"UPDATE", "UPSERT"}:
        if existing is None:
            if operation == "UPSERT":
                row = Matter(
                    id=matter_id,
                    client_id=client_id,
                    matter_ref=payload.get("matter_ref"),
                    property_address=payload.get("property_address"),
                    volume_folio=payload.get("volume_folio"),
                    council_area=payload.get("council_area"),
                    water_authority=payload.get("water_authority"),
                    property_type=payload.get("property_type"),
                    vendor_count=payload.get("vendor_count"),
                    status=str(payload.get("status") or "active"),
                )
                session.add(row)
                await session.flush()
                return None
            return _conflict(event.sync_queue_id, {})
        for key in ("matter_ref", "property_address", "volume_folio", "council_area", "water_authority", "property_type", "vendor_count", "status"):
            if key in payload:
                setattr(existing, key, payload.get(key))
        existing.updated_at = datetime.now(UTC)
        await session.flush()
        return None

    if operation == "DELETE":
        if existing is None:
            return None
        await session.delete(existing)
        await session.flush()
        return None

    raise ValueError(f"Unknown matter operation: {event.operation}")


async def _apply_copy_rule_event(
    session: AsyncSession,
    client_id: uuid.UUID,
    rule_id: uuid.UUID,
    event: SyncEvent,
) -> SyncConflictDetail | None:
    existing = await session.get(CopyRule, rule_id)
    payload = dict(event.payload or {})
    operation = _normalize_operation(event.operation)

    if existing is not None and existing.client_id != client_id:
        return _conflict(event.sync_queue_id, _serialize_model(existing))

    if operation in {"INSERT", "CREATE", "UPSERT", "UPDATE"}:
        if existing is None:
            row = CopyRule(
                id=rule_id,
                client_id=client_id,
                rule_type=str(payload.get("rule_type") or "water_authority"),
                authority_name=str(payload.get("authority_name") or "").strip(),
                annual_amount=float(payload.get("annual_amount") or 0.0),
                notes=payload.get("notes"),
                is_active=bool(payload.get("is_active", True)),
                created_by=str(payload.get("created_by") or payload.get("updated_by") or "sync"),
                created_at=_parse_optional_datetime(payload.get("created_at")) or datetime.now(UTC),
                updated_by=payload.get("updated_by"),
                updated_at=_parse_optional_datetime(payload.get("updated_at")),
            )
            session.add(row)
            await session.flush()
            return None

        existing.rule_type = str(payload.get("rule_type") or existing.rule_type)
        if "authority_name" in payload:
            existing.authority_name = str(payload.get("authority_name") or "").strip()
        if "annual_amount" in payload:
            existing.annual_amount = float(payload.get("annual_amount") or 0.0)
        if "notes" in payload:
            existing.notes = payload.get("notes")
        if "is_active" in payload:
            existing.is_active = bool(payload.get("is_active"))
        if "updated_by" in payload:
            existing.updated_by = payload.get("updated_by")
        if "updated_at" in payload:
            existing.updated_at = _parse_optional_datetime(payload.get("updated_at"))
        await session.flush()
        return None

    if operation == "DELETE":
        if existing is None:
            return None
        existing.is_active = False
        existing.updated_by = payload.get("updated_by") or existing.updated_by
        existing.updated_at = _parse_optional_datetime(payload.get("updated_at")) or datetime.now(UTC)
        await session.flush()
        return None

    raise ValueError(f"Unknown copy_rule operation: {event.operation}")
