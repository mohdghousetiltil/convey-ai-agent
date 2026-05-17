"""First-run bootstrap.

Installs the schema on a fresh client PC and creates the local tenant +
first admin user so the operator can log in. Idempotent — safe to re-run.

Invoke via:
    python -m triconvey_agent.db.bootstrap \
        --client-slug "acme-conveyancing" \
        --client-name "Acme Conveyancing Pty Ltd" \
        --license-key "ACME-XXXX-XXXX" \
        --admin-email "admin@acme.example" \
        --admin-name "Admin" \
        --admin-password "change-me-now"

Also seeds the questions catalog from `canonical/questions/registry.yaml` if
present. Run this once at install time from the desktop installer.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from importlib.resources import files
from pathlib import Path

from sqlalchemy import text

from triconvey_agent.auth.security import hash_password
from triconvey_agent.db.repositories import ClientRepo, UserRepo
from triconvey_agent.db.session import get_engine, get_session_factory

LOG = logging.getLogger("convey.bootstrap")


def _load_schema_sql() -> str:
    # Prefer the packaged version; fall back to a sibling file for dev installs.
    try:
        resource = files("triconvey_agent.db").joinpath("schema.sql")
        return resource.read_text(encoding="utf-8")
    except Exception:
        candidate = Path(__file__).parent / "schema.sql"
        return candidate.read_text(encoding="utf-8")


def _load_migration_sql_files() -> list[tuple[str, str]]:
    migration_dir = Path(__file__).parent / "migrations"
    if not migration_dir.exists():
        return []
    items: list[tuple[str, str]] = []
    for path in sorted(migration_dir.glob("*.sql")):
        try:
            items.append((path.name, path.read_text(encoding="utf-8")))
        except Exception:
            continue
    return items


async def apply_schema() -> None:
    sql = _load_schema_sql()
    engine = get_engine()
    async with engine.begin() as conn:
        # Split on semicolons is risky for DDL with function bodies; but our
        # schema has none. Execute as a single multi-statement block.
        for statement in _split_sql(sql):
            if not statement.strip():
                continue
            await conn.execute(text(statement))
    LOG.info("Schema applied.")


def _is_missing_extension_error(exc: BaseException) -> bool:
    """True when the error is a PostgreSQL 'extension not available' failure."""
    msg = str(exc)
    return (
        "FeatureNotSupportedError" in msg
        or "extension" in msg.lower() and "not available" in msg.lower()
        or "feature_not_supported" in msg.lower()
    )


async def apply_runtime_migrations() -> None:
    engine = get_engine()
    migrations = _load_migration_sql_files()
    if not migrations:
        return
    for name, sql in migrations:
        try:
            async with engine.begin() as conn:
                for statement in _split_sql(sql):
                    if not statement.strip():
                        continue
                    await conn.execute(text(statement))
            LOG.info("Applied migration %s", name)
        except Exception as exc:
            if _is_missing_extension_error(exc):
                LOG.info(
                    "Migration %s skipped — optional PostgreSQL extension not installed "
                    "(feature disabled, everything else works normally).",
                    name,
                )
            else:
                LOG.warning("Skipping migration %s (not supported in this environment): %s", name, exc)


def _split_sql(sql: str) -> list[str]:
    parts: list[str] = []
    buf: list[str] = []
    in_dollar_quote = False
    for line in sql.splitlines():
        line_no_comment = line if in_dollar_quote else line.split("--", 1)[0]
        stripped = line_no_comment.strip()
        if not in_dollar_quote and (stripped.startswith("--") or not stripped):
            continue
        buf.append(line_no_comment.rstrip())
        # Toggle dollar-quote state for each $$ token on the line.
        in_dollar_quote = (line_no_comment.count("$$") % 2 == 1) ^ in_dollar_quote
        if not in_dollar_quote and stripped.endswith(";"):
            parts.append("\n".join(buf))
            buf = []
    if buf:
        parts.append("\n".join(buf))
    return parts


async def ensure_tenant_and_admin(
    *,
    client_slug: str,
    client_name: str,
    license_key: str | None,
    admin_email: str,
    admin_name: str,
    admin_password: str,
) -> None:
    factory = get_session_factory()
    async with factory() as session:
        client = await ClientRepo.ensure_local(
            session,
            slug=client_slug,
            name=client_name,
            license_key=license_key,
        )
        existing = await UserRepo.get_by_email(
            session, client_id=client.id, email=admin_email
        )
        if existing is None:
            await UserRepo.create(
                session,
                client_id=client.id,
                email=admin_email,
                name=admin_name,
                password_hash=hash_password(admin_password),
                role="admin",
            )
            LOG.info("Created admin user %s for client %s", admin_email, client_slug)
        else:
            LOG.info("Admin user %s already exists; skipping.", admin_email)
        await session.commit()


async def seed_questions_if_present() -> None:
    """Load `canonical/questions/registry.yaml` into the `questions` table."""
    try:
        from triconvey_agent.canonical.questions.loader import load_question_registry

        from triconvey_agent.db.repositories import QuestionRepo
    except Exception as exc:  # pragma: no cover - optional step
        LOG.warning("Skipping question seed: %s", exc)
        return

    try:
        registry = load_question_registry()
    except Exception as exc:
        LOG.warning("Could not load question registry: %s", exc)
        return

    rows: list[dict] = []
    for qid, q in registry.items():
        rows.append(
            {
                "id": qid,
                "tab": getattr(q, "tab", ""),
                "label": getattr(q, "label", qid),
                "expected_type": getattr(q, "expected_type", None),
                "answer_strategy": (
                    getattr(q.answer_strategy, "value", None)
                    if hasattr(q, "answer_strategy")
                    else None
                ),
                "fact_paths": list(getattr(q, "fact_paths", []) or []),
                "options": list(getattr(q, "options", []) or []) or None,
                "description": getattr(q, "description", None),
                "value_transform": getattr(q, "value_transform", "direct"),
                "is_active": True,
            }
        )
    if not rows:
        return

    factory = get_session_factory()
    async with factory() as session:
        await QuestionRepo.upsert_all(session, rows)
        await session.commit()
    LOG.info("Seeded %d questions.", len(rows))


async def _main(args: argparse.Namespace) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    if args.apply_schema:
        await apply_schema()

    if args.client_slug:
        if not (args.admin_email and args.admin_password and args.admin_name):
            raise SystemExit("--admin-email/--admin-name/--admin-password are required with --client-slug")
        await ensure_tenant_and_admin(
            client_slug=args.client_slug,
            client_name=args.client_name or args.client_slug,
            license_key=args.license_key,
            admin_email=args.admin_email,
            admin_name=args.admin_name,
            admin_password=args.admin_password,
        )

    if args.seed_questions:
        await seed_questions_if_present()


def main() -> int:
    parser = argparse.ArgumentParser(description="Convey Agent first-run bootstrap.")
    parser.add_argument("--apply-schema", action="store_true", help="Install/refresh DB schema.")
    parser.add_argument("--client-slug", help="Tenant slug (CONVEY_CLIENT_SLUG).")
    parser.add_argument("--client-name", help="Display name for the tenant.")
    parser.add_argument("--license-key", help="License key issued by vendor.")
    parser.add_argument("--admin-email")
    parser.add_argument("--admin-name")
    parser.add_argument("--admin-password")
    parser.add_argument("--seed-questions", action="store_true", help="Load question catalog from YAML.")
    args = parser.parse_args()
    try:
        asyncio.run(_main(args))
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
