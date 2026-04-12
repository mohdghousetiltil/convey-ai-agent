from __future__ import annotations

import json
from datetime import datetime, UTC
from pathlib import Path
from typing import Any

import typer
from rich.console import Console
from rich.table import Table

from triconvey_agent.ai.fake_client import FakeAIClient
from triconvey_agent.ai.normalizer import run_ai_normalization
from triconvey_agent.ai.openai_client import OpenAIResponsesClient
from triconvey_agent.ai.semantic_mapper import run_ai_confirmation
from triconvey_agent.config import settings
from triconvey_agent.ingest.pdf_loader import load_pdf_document
from triconvey_agent.ingest.yaml_loader import load_yaml_document
from triconvey_agent.output.normalized_builder import build_normalized_output
from triconvey_agent.output.validated_mapper import map_answers_with_validation
from triconvey_agent.output.writer import write_final_extraction, write_json
from triconvey_agent.output.triconvey_mapper import map_answers, load_client_policy, _apply_client_policy_overrides, get_question_lookup
from triconvey_agent.pipeline import collect_files, load_document, run_pipeline_with_artifacts_from_paths
from triconvey_agent.schemas.documents import Document, InputFileType

app = typer.Typer(help="Triconvey agent CLI")
console = Console()


def _load_document(path: Path) -> Document:
    suffix = path.suffix.lower()
    if suffix in settings.supported_pdf_suffixes:
        return load_pdf_document(path)
    if suffix in settings.supported_yaml_suffixes:
        return load_yaml_document(path)
    raise ValueError(f"Unsupported file type: {path.name}")


@app.command()
def ingest(
    input_path: str = typer.Argument(..., help="File or folder to ingest"),
    output_path: str | None = typer.Option(None, "--output", "-o", help="Optional JSON output path"),
    recursive: bool = typer.Option(True, "--recursive/--no-recursive", help="Walk folders recursively"),
) -> None:
    source = settings.resolve_path(input_path)
    if not source.exists():
        console.print(f"[red]Input path not found:[/red] {source}")
        raise typer.Exit(code=1)

    files = collect_files(source, recursive=recursive)
    if not files:
        console.print("[red]No supported files found.[/red]")
        raise typer.Exit(code=1)

    documents = [_load_document(file_path) for file_path in files]

    table = Table(title="Ingest Summary")
    table.add_column("Filename")
    table.add_column("File Type")
    table.add_column("Document Type")
    table.add_column("Confidence")
    table.add_column("Pages/Fields")
    table.add_column("Preview")

    for doc in documents:
        pages_or_fields = str(len(doc.pages)) if doc.file_type == InputFileType.PDF else str(doc.yaml_total_fields or len(doc.yaml_fields))
        preview = doc.metadata.get("text_preview", "")
        table.add_row(
            doc.filename,
            doc.file_type.value,
            doc.document_type.value,
            f"{doc.classification_confidence:.2f}",
            pages_or_fields,
            preview,
        )

    console.print(table)

    if output_path:
        destination = settings.resolve_path(output_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        payload = [doc.model_dump(mode="json") for doc in documents]
        destination.write_text(json.dumps(payload, indent=2), encoding=settings.default_encoding)
        console.print(f"[green]Wrote ingest output to:[/green] {destination}")


@app.command()
def extract(
    input_paths: list[str] = typer.Argument(..., help="One or more files/folders to process"),
    output_path: str = typer.Option("final_output.json", "--output", "-o", help="Final JSON output path"),
    debug_output_path: str | None = typer.Option(
        None,
        "--debug-output",
        help="Optional debug summary JSON output path",
    ),
    fill_artifact_output_path: str | None = typer.Option(
        None,
        "--fill-artifact-output",
        help="Optional AI-ready fill artifact JSON output path",
    ),
    recursive: bool = typer.Option(True, "--recursive/--no-recursive", help="Walk folders recursively"),
    use_ai_review: bool = typer.Option(False, "--use-ai-review", help="Enable AI review suggestions"),
    use_ai_extract: bool = typer.Option(
        False,
        "--use-ai-extract",
        help="Run grounded AI extraction on each PDF and reconcile with rules (requires OPENAI_API_KEY)",
    ),
    agent: bool = typer.Option(
        False,
        "--agent",
        help=(
            "Agent mode: send ALL documents to GPT-4o in one unified call, "
            "then self-audit uncertain fields. Highest accuracy. Requires OPENAI_API_KEY."
        ),
    ),
    model: str = typer.Option("gpt-4o", "--model", help="OpenAI model for AI extraction"),
    debug_files: bool = typer.Option(False, "--debug-files", help="Print discovered files and loaded document types"),
) -> None:
    sources = [settings.resolve_path(path) for path in input_paths]
    missing = [path for path in sources if not path.exists()]
    if missing:
        for path in missing:
            console.print(f"[red]Input path not found:[/red] {path}")
        raise typer.Exit(code=1)

    files: list[Path] = []
    seen: set[Path] = set()
    for source in sources:
        for file_path in collect_files(source, recursive=recursive):
            if file_path in seen:
                continue
            seen.add(file_path)
            files.append(file_path)

    if not files:
        console.print("[red]No supported files found.[/red]")
        raise typer.Exit(code=1)

    if debug_files:
        console.print("[bold]Discovered files:[/bold]")
        for file_path in files:
            console.print(f" - {file_path}")

        console.print("[bold]Loaded document types:[/bold]")
        for file_path in files:
            doc = load_document(file_path)
            console.print(f" - {doc.filename}: {doc.document_type.value}")

    # Build the AI client — used for agent mode, grounded extraction, and review suggestions.
    ai_client = None
    if agent or use_ai_extract or use_ai_review:
        try:
            ai_client = OpenAIResponsesClient(model=model)
            if agent:
                console.print(f"[bold cyan]Agent mode enabled[/bold cyan] (model: {model}) — unified multi-doc AI reasoning + self-audit.")
            elif use_ai_extract:
                console.print(f"[dim]Grounded AI extraction enabled (model: {model}).[/dim]")
        except ValueError:
            console.print("[yellow]OPENAI_API_KEY not set — AI features disabled.[/yellow]")
    elif use_ai_review:
        ai_client = FakeAIClient()

    # Agent mode implies use_ai_extract (it's a superset)
    effective_agent = agent and ai_client is not None
    effective_ai_extract = use_ai_extract and ai_client is not None and not effective_agent

    extraction, fill_artifact = run_pipeline_with_artifacts_from_paths(
        files,
        ai_client=ai_client,
        use_ai_extract=effective_ai_extract,
        agent_mode=effective_agent,
    )
    final_path = write_final_extraction(output_path, extraction)

    table = Table(title="Extraction Summary")
    table.add_column("Section")
    table.add_column("Fields")
    table.add_row("vendor_core", str(len(extraction.vendor_core)))
    table.add_row("trustee", str(len(extraction.trustee)))
    table.add_row("property_details", str(len(extraction.property_details)))
    table.add_row("services_connected", str(len(extraction.services_connected)))
    table.add_row("rates_taxes_charges", str(len(extraction.rates_taxes_charges)))
    table.add_row("planning_building_permits", str(len(extraction.planning_building_permits)))
    table.add_row("vic_title_extract", str(len(extraction.vic_title_extract)))
    table.add_row("section32_yes", str(len(extraction.section32_questions.yes_questions)))
    table.add_row("section32_no", str(len(extraction.section32_questions.no_questions)))
    table.add_row("section32_unanswered", str(len(extraction.section32_questions.unanswered_or_not_found)))
    console.print(table)

    rs = extraction.review_summary or {}

    # Agent mode summary
    agent_summary = rs.get("agent_summary")
    if agent_summary:
        agent_table = Table(title="[bold cyan]Agent Mode - Unified AI Reasoning[/bold cyan]")
        agent_table.add_column("Metric")
        agent_table.add_column("Value")
        agent_table.add_row("PDFs processed",          str(agent_summary.get("source_files_processed", 0)))
        agent_table.add_row("fields extracted",        str(agent_summary.get("fields_filled", 0)))
        agent_table.add_row("fields quote-verified",   str(agent_summary.get("fields_verified", 0)))
        agent_table.add_row("cross-doc conflicts",     str(agent_summary.get("conflicts_detected", 0)))
        agent_table.add_row("missing info items",      str(agent_summary.get("missing_information_items", 0)))
        console.print(agent_table)

        # Self-audit results
        agent_recon = rs.get("agent_reconciliation", {})
        audit = agent_recon.get("audit_summary", {})
        if audit:
            audit_table = Table(title="Self-Audit Results")
            audit_table.add_column("Action")
            audit_table.add_column("Count")
            audit_table.add_row("fields checked",   str(audit.get("fields_checked", 0)))
            audit_table.add_row("confirmed",        str(audit.get("confirmed", 0)))
            audit_table.add_row("corrected",        str(audit.get("corrected", 0)))
            audit_table.add_row("rejected",         str(audit.get("rejected", 0)))
            audit_table.add_row("status",           audit.get("status", ""))
            console.print(audit_table)

        missing = agent_recon.get("missing_information", [])
        if missing:
            console.print("[yellow]Missing information flagged by agent:[/yellow]")
            for item in missing:
                console.print(f"  [yellow]•[/yellow] {item}")

    # Phase 2: per-doc grounded extraction summary
    ai_recon = rs.get("ai_reconciliation")
    if ai_recon and not agent_summary:
        recon_table = Table(title="AI Extraction & Reconciliation")
        recon_table.add_column("Metric")
        recon_table.add_column("Value")
        recon_table.add_row("docs processed by AI",   str(ai_recon.get("ai_docs_processed", 0)))
        recon_table.add_row("docs with AI errors",    str(ai_recon.get("ai_docs_errored", 0)))
        recon_table.add_row("fields filled by AI",    str(ai_recon.get("fields_filled_by_ai", 0)))
        recon_table.add_row("fields corrected by AI", str(ai_recon.get("fields_corrected_by_ai", 0)))
        recon_table.add_row("conflicts flagged",      str(ai_recon.get("conflicts_found", 0)))
        console.print(recon_table)

    console.print(f"[green]Wrote final extraction to:[/green] {final_path}")

    if fill_artifact_output_path:
        fill_artifact_path = write_json(fill_artifact_output_path, fill_artifact.model_dump(mode="json"))
        console.print(f"[green]Wrote fill artifact to:[/green] {fill_artifact_path}")

    if debug_output_path:
        debug_payload = {
            "source_summary": extraction.source_summary,
            "skipped_sections": extraction.skipped_sections,
            "field_counts": {
                "vendor_core": len(extraction.vendor_core),
                "trustee": len(extraction.trustee),
                "property_details": len(extraction.property_details),
                "services_connected": len(extraction.services_connected),
                "rates_taxes_charges": len(extraction.rates_taxes_charges),
                "planning_building_permits": len(extraction.planning_building_permits),
                "vic_title_extract": len(extraction.vic_title_extract),
            },
        }
        debug_path = write_json(debug_output_path, debug_payload)
        console.print(f"[green]Wrote debug summary to:[/green] {debug_path}")


@app.command()
def map_triconvey(
    final_output_path: str = typer.Argument("final_output.json", help="Path to extracted final_output JSON"),
    template_path: str = typer.Argument("triconvey_tab.json", help="Path to TriConvey template JSON"),
    normalized_output_path: str | None = typer.Option(
        None,
        "--normalized-output",
        help="Optional normalized output JSON path to drive validated mapping",
    ),
    output_path: str = typer.Option(
        "answered_triconvey_tab.json",
        "--output",
        "-o",
        help="Answered TriConvey template output path",
    ),
    report_output_path: str = typer.Option(
        "mapping_report.json",
        "--report-output",
        help="Mapping report JSON output path",
    ),
) -> None:
    final_output_file = settings.resolve_path(final_output_path)
    template_file = settings.resolve_path(template_path)

    maybe_paths = [final_output_file, template_file]
    normalized_file = settings.resolve_path(normalized_output_path) if normalized_output_path else None
    if normalized_file is not None:
        maybe_paths.append(normalized_file)
    missing = [path for path in maybe_paths if not path.exists()]
    if missing:
        for path in missing:
            console.print(f"[red]Input path not found:[/red] {path}")
        raise typer.Exit(code=1)

    final_output = json.loads(final_output_file.read_text(encoding=settings.default_encoding))
    template = json.loads(template_file.read_text(encoding=settings.default_encoding))
    if normalized_file is not None:
        normalized_output = json.loads(normalized_file.read_text(encoding=settings.default_encoding))
        answered, report = map_answers_with_validation(final_output, template, normalized_output)
    else:
        answered, report = map_answers(final_output, template)
    answered["generated_at"] = datetime.now(UTC).isoformat()

    answered_path = write_json(output_path, answered)
    report_path = write_json(report_output_path, report)

    table = Table(title="TriConvey Mapping Summary")
    table.add_column("Metric")
    table.add_column("Count")
    table.add_row("mapped_count", str(report["mapped_count"]))
    table.add_row("unmapped_count", str(report["unmapped_count"]))
    table.add_row("attachment_review_items", str(len(report.get("attachment_review_items", []))))
    table.add_row("validator_issues", str(len(report.get("validator_issues", []))))

    console.print(table)
    console.print(f"[green]Wrote answered template to:[/green] {answered_path}")
    console.print(f"[green]Wrote mapping report to:[/green] {report_path}")


@app.command()
def normalize_output(
    final_output_path: str = typer.Argument("final_output.json", help="Path to extracted final_output JSON"),
    output_path: str = typer.Option(
        "normalized_output.json",
        "--output",
        "-o",
        help="Normalized output path",
    ),
) -> None:
    final_output_file = settings.resolve_path(final_output_path)
    if not final_output_file.exists():
        console.print(f"[red]Input path not found:[/red] {final_output_file}")
        raise typer.Exit(code=1)

    final_output = json.loads(final_output_file.read_text(encoding=settings.default_encoding))
    normalized = build_normalized_output(final_output)
    normalized_path = write_json(output_path, normalized.model_dump(mode="json"))

    table = Table(title="Normalization Summary")
    table.add_column("Metric")
    table.add_column("Count")
    table.add_row("attachments_normalized", str(len(normalized.attachments_normalized)))
    table.add_row("review_flags", str(len(normalized.review_flags)))
    console.print(table)
    console.print(f"[green]Wrote normalized output to:[/green] {normalized_path}")


@app.command()
def ai_normalize_output(
    final_output_path: str = typer.Argument("final_output.json", help="Path to extracted final_output JSON"),
    output_path: str = typer.Option(
        "normalized_output.json",
        "--output",
        "-o",
        help="AI-normalized output path",
    ),
    model: str = typer.Option("gpt-4o", "--model", help="OpenAI model to use for normalization"),
    baseline_output_path: str | None = typer.Option(
        None,
        "--baseline-output",
        help="Optional path to also write the deterministic baseline normalized output",
    ),
) -> None:
    final_output_file = settings.resolve_path(final_output_path)
    if not final_output_file.exists():
        console.print(f"[red]Input path not found:[/red] {final_output_file}")
        raise typer.Exit(code=1)

    try:
        client = OpenAIResponsesClient(model=model)
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        console.print("[yellow]Set OPENAI_API_KEY in your environment, then rerun this command.[/yellow]")
        raise typer.Exit(code=1)

    final_output = json.loads(final_output_file.read_text(encoding=settings.default_encoding))
    baseline = build_normalized_output(final_output).model_dump(mode="json")

    try:
        normalized = run_ai_normalization(final_output, baseline, client)
    except Exception as exc:
        console.print(f"[red]AI normalization failed:[/red] {exc}")
        raise typer.Exit(code=1)

    normalized_path = write_json(output_path, normalized)
    if baseline_output_path:
        baseline_path = write_json(baseline_output_path, baseline)
        console.print(f"[green]Wrote baseline normalized output to:[/green] {baseline_path}")

    table = Table(title="AI Normalization Summary")
    table.add_column("Metric")
    table.add_column("Count")
    table.add_row("attachments_normalized", str(len(normalized.get("attachments_normalized", []))))
    table.add_row("review_flags", str(len(normalized.get("review_flags", []))))
    table.add_row("ai_notes", str(len(normalized.get("ai_notes", []))))
    console.print(table)
    console.print(f"[green]Wrote AI-normalized output to:[/green] {normalized_path}")


@app.command()
def ai_map_triconvey(
    final_output_path: str = typer.Argument("final_output.json", help="Path to extracted final_output JSON"),
    template_path: str = typer.Argument("triconvey_tab.json", help="Path to TriConvey template JSON"),
    normalized_output_path: str | None = typer.Option(
        None,
        "--normalized-output",
        help="Pre-built normalized output JSON (skip AI normalization if provided)",
    ),
    policy_path: str | None = typer.Option(
        None,
        "--policy",
        help="Client policy overrides JSON (default: client_policy.json in CWD)",
    ),
    output_path: str = typer.Option("answered_triconvey_tab.json", "--output", "-o"),
    report_output_path: str = typer.Option("mapping_report.json", "--report-output"),
    model: str = typer.Option("gpt-4o", "--model", help="OpenAI model for AI confirmation"),
    skip_ai: bool = typer.Option(False, "--skip-ai", help="Run deterministic + scope + policy only, no AI call"),
    no_scope: bool = typer.Option(False, "--no-scope", help="Disable scope filtering (fill all questions)"),
) -> None:
    """Full Step B pipeline: scope, deterministic mapping, AI confirmation, policy overrides."""
    final_output_file = settings.resolve_path(final_output_path)
    template_file = settings.resolve_path(template_path)

    for path in [final_output_file, template_file]:
        if not path.exists():
            console.print(f"[red]Input path not found:[/red] {path}")
            raise typer.Exit(code=1)

    final_output = json.loads(final_output_file.read_text(encoding=settings.default_encoding))
    template = json.loads(template_file.read_text(encoding=settings.default_encoding))

    # --- Normalization (services + attachments) ---
    if normalized_output_path:
        normalized_file = settings.resolve_path(normalized_output_path)
        if not normalized_file.exists():
            console.print(f"[red]Normalized output not found:[/red] {normalized_file}")
            raise typer.Exit(code=1)
        normalized = json.loads(normalized_file.read_text(encoding=settings.default_encoding))
        console.print("[dim]Using pre-built normalized output.[/dim]")
    else:
        from triconvey_agent.output.normalized_builder import build_normalized_output
        try:
            client_ai = OpenAIResponsesClient(model=model)
            from triconvey_agent.ai.normalizer import run_ai_normalization
            baseline = build_normalized_output(final_output).model_dump(mode="json")
            normalized = run_ai_normalization(final_output, baseline, client_ai)
            console.print("[dim]AI normalization complete (services + attachments).[/dim]")
        except ValueError:
            normalized = build_normalized_output(final_output).model_dump(mode="json")
            console.print("[yellow]OPENAI_API_KEY not set — using deterministic normalization.[/yellow]")

    # --- Step B-1: Build scope ---
    from triconvey_agent.output.scope import build_scope
    scope = None if no_scope else build_scope(final_output)
    if scope is not None:
        console.print(f"[dim]Scope: {len(scope)} questions in scope for this matter.[/dim]")
    else:
        console.print("[dim]Scope filtering disabled (--no-scope).[/dim]")

    # --- Step B-2: Deterministic mapping + scope filter ---
    # client_overrides={} — policy is applied after AI confirmation (Step B-4)
    answered, report = map_answers(
        final_output, template,
        normalized_output=normalized,
        client_overrides={},
        scope=scope,
    )
    console.print(
        f"[dim]Deterministic: {report['mapped_count']} filled, "
        f"{report['unmapped_count']} null, "
        f"{report.get('out_of_scope_count', 0)} out-of-scope.[/dim]"
    )

    # --- Step B-3: AI confirmation (GPT-4o confirms/corrects targeted fields) ---
    ai_report: dict[str, Any] = {
        "targets_sent": 0, "confirmed": 0, "corrected": 0, "corrected_ids": [], "error": None,
    }
    if not skip_ai:
        try:
            client_ai = OpenAIResponsesClient(model=model)
            answered, ai_report = run_ai_confirmation(final_output, answered, client_ai)
            if ai_report.get("error"):
                console.print(f"[yellow]AI confirmation warning:[/yellow] {ai_report['error']}")
            else:
                console.print(
                    f"[dim]AI confirmation: {ai_report['targets_sent']} reviewed, "
                    f"{ai_report['confirmed']} confirmed, "
                    f"{ai_report.get('filled', 0)} filled, "
                    f"{ai_report.get('corrected', 0)} corrected.[/dim]"
                )
        except ValueError:
            console.print("[yellow]OPENAI_API_KEY not set — skipping AI confirmation.[/yellow]")
    else:
        console.print("[dim]AI confirmation skipped (--skip-ai).[/dim]")

    # --- Step B-4: Client policy overrides (always last, always win) ---
    policy = load_client_policy(policy_path)
    question_lookup = get_question_lookup(answered)
    _apply_client_policy_overrides(question_lookup, policy)

    # Final counts
    total = sum(len(tab.get("questions", [])) for tab in answered.get("tabs", []))
    answered_count = sum(
        1 for tab in answered.get("tabs", [])
        for q in tab.get("questions", [])
        if q.get("answer") is not None
    )
    out_of_scope_count = sum(
        1 for tab in answered.get("tabs", [])
        for q in tab.get("questions", [])
        if q.get("skip_reason") == "out_of_scope"
    )

    answered["generated_at"] = datetime.now(UTC).isoformat()
    full_report = {
        **report,
        "ai_confirmation": ai_report,
        "policy_overrides_applied": len(policy),
        "final_answered_count": answered_count,
        "final_null_count": total - answered_count - out_of_scope_count,
        "final_out_of_scope_count": out_of_scope_count,
    }

    answered_path = write_json(output_path, answered)
    report_path = write_json(report_output_path, full_report)

    table = Table(title="Step B — Mapping Pipeline")
    table.add_column("Step")
    table.add_column("Result")
    table.add_row("B-1  Scope", f"{len(scope)} in scope" if scope else "all questions")
    table.add_row("B-2  Deterministic", f"{report['mapped_count']} filled")
    ai_summary = (
        f"{ai_report.get('filled', 0)} filled, {ai_report.get('corrected', 0)} corrected"
        if not skip_ai else "skipped"
    )
    table.add_row("B-3  AI confirmation", ai_summary)
    table.add_row("B-4  Policy overrides", f"{len(policy)} fields")
    table.add_row("Final answered", str(answered_count))
    table.add_row("Out of scope", str(out_of_scope_count))
    table.add_row("Null (genuine gaps)", str(total - answered_count - out_of_scope_count))
    console.print(table)
    console.print(f"[green]Wrote answered template to:[/green] {answered_path}")
    console.print(f"[green]Wrote mapping report to:[/green] {report_path}")


@app.command()
def fill_form(
    input_paths: list[str] = typer.Argument(..., help="PDF files or folder to process"),
    template_path: str = typer.Option("triconvey_tab.json", "--template", "-t", help="TriConvey template JSON"),
    output_path: str = typer.Option("answered_triconvey_tab.json", "--output", "-o"),
    report_path: str = typer.Option("qa_report.json", "--report", help="Full audit trail JSON"),
    policy_path: str | None = typer.Option(None, "--policy", help="Client policy overrides JSON"),
    model: str = typer.Option("gpt-4o", "--model"),
    recursive: bool = typer.Option(True, "--recursive/--no-recursive"),
    save_final_output: bool = typer.Option(False, "--save-extraction", help="Also save final_output.json for debugging"),
) -> None:
    """Direct AI agent: reads ALL documents, answers ALL 78 TriConvey questions.

    This is the recommended single-command workflow. It skips the intermediate
    final_output.json schema and has the AI answer each TriConvey question
    directly from document text with anti-hallucination quote verification.
    """
    from triconvey_agent.ai.qa_agent import run_qa_agent

    # --- Resolve paths ---
    template_file = settings.resolve_path(template_path)
    if not template_file.exists():
        console.print(f"[red]Template not found:[/red] {template_file}")
        raise typer.Exit(code=1)

    sources = [settings.resolve_path(p) for p in input_paths]
    missing = [p for p in sources if not p.exists()]
    if missing:
        for p in missing:
            console.print(f"[red]Not found:[/red] {p}")
        raise typer.Exit(code=1)

    files: list[Path] = []
    seen: set[Path] = set()
    for source in sources:
        for file_path in collect_files(source, recursive=recursive):
            if file_path not in seen:
                seen.add(file_path)
                files.append(file_path)

    if not files:
        console.print("[red]No supported files found.[/red]")
        raise typer.Exit(code=1)

    # --- Load AI client ---
    try:
        ai_client = OpenAIResponsesClient(model=model)
    except ValueError:
        console.print("[red]OPENAI_API_KEY not set. This command requires an AI model.[/red]")
        raise typer.Exit(code=1)

    # --- Load template ---
    template = json.loads(template_file.read_text(encoding=settings.default_encoding))

    # --- Load policy overrides ---
    policy_overrides: dict[str, Any] = {}
    if policy_path:
        policy_file = settings.resolve_path(policy_path)
        if policy_file.exists():
            raw_policy = json.loads(policy_file.read_text(encoding=settings.default_encoding))
            # Support both list-of-overrides and dict formats
            if isinstance(raw_policy, list):
                for item in raw_policy:
                    if "label" in item and "answer" in item:
                        policy_overrides[item["label"]] = item["answer"]
            elif isinstance(raw_policy, dict):
                policy_overrides = raw_policy
            console.print(f"[dim]Loaded {len(policy_overrides)} policy overrides from {policy_path}[/dim]")

    # --- Load documents ---
    console.print(f"[dim]Loading {len(files)} file(s)...[/dim]")
    docs = [load_document(f) for f in files]
    pdf_count = sum(1 for d in docs if d.is_pdf)
    console.print(f"[dim]{pdf_count} PDF(s) loaded, text extracted.[/dim]")

    # --- Optionally save final_output.json for debugging ---
    if save_final_output:
        from triconvey_agent.pipeline import run_pipeline_with_artifacts_from_paths
        ext, _ = run_pipeline_with_artifacts_from_paths(
            files, ai_client=ai_client, agent_mode=True
        )
        from triconvey_agent.output.writer import write_final_extraction
        fo_path = write_final_extraction("final_output.json", ext)
        console.print(f"[dim]Saved extraction debug file: {fo_path}[/dim]")

    # --- Run Q&A agent ---
    console.print(f"\n[bold cyan]Running Q&A agent[/bold cyan] (model: {model})")
    console.print(f"[dim]Sending {pdf_count} documents + {sum(len(t.get('questions',[])) for t in template.get('tabs',[]))} questions to AI...[/dim]")

    answered, report = run_qa_agent(docs, template, ai_client, policy_overrides)

    from datetime import datetime, UTC
    answered["generated_at"] = datetime.now(UTC).isoformat()

    # --- Write outputs ---
    from triconvey_agent.output.writer import write_json
    out_path = write_json(output_path, answered)
    rep_path = write_json(report_path, report)

    # --- Display results ---
    stats = answered.get("_qa_agent_stats", {})
    total_q = stats.get("total", 0)
    filled  = stats.get("filled", 0)
    null_c  = stats.get("null", 0)

    table = Table(title="[bold cyan]Q&A Agent Results[/bold cyan]")
    table.add_column("Metric")
    table.add_column("Value")
    table.add_row("Questions sent to AI",   str(report.get("questions_sent", 0)))
    table.add_row("Answers filled",         f"[green]{filled}[/green]")
    table.add_row("Null (not in documents)",str(null_c))
    table.add_row("Quote-verified answers", f"[green]{report.get('verified', 0)}[/green]")
    table.add_row("Hallucinations rejected",f"[yellow]{report.get('rejected_hallucinations', 0)}[/yellow]")
    table.add_row("Policy overrides",       str(len(policy_overrides)))
    console.print(table)

    # Show what's still null (genuine gaps the AI couldn't find)
    null_labels = [
        f"  [{a['tab']}] {a['label'][:70]}"
        for a in report.get("answers", [])
        if a.get("value") is None
    ]
    if null_labels:
        console.print("\n[yellow]Questions AI could not answer (not in any document):[/yellow]")
        for lbl in null_labels:
            console.print(lbl)

    console.print(f"\n[green]Answered template:[/green] {out_path}")
    console.print(f"[green]Audit report:[/green]     {rep_path}")


def main() -> None:
    app()


if __name__ == "__main__":
    main()
