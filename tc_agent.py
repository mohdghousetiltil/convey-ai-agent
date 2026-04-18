#!/usr/bin/env python
"""tc_agent  -  TriConvey Agent CLI.

Commands
--------
  run       Full pipeline: extract + answer + policy + plan [+ fill]
  extract   Brain A only - extract facts from PDFs into facts.json
  answer    Brain B + C - answer questions and apply policy (reads facts.json)
  plan      Brain D - build action plan from answers.json
  fill      Brain E - fill TriConvey form from action_plan.json
  review    Show all flagged review items from answers.json

Examples
--------
  # Full run with every PDF found in samples/:
  python tc_agent.py run

  python tc_agent.py run --fill
  python tc_agent.py run --docs path/to/title.pdf path/to/water.pdf
  python tc_agent.py extract --docs path/to/title.pdf
  python tc_agent.py answer --out output/
  python tc_agent.py fill --dry-run
  python tc_agent.py fill
  python tc_agent.py review
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Optional

import typer
from rich import print as rprint
from rich.console import Console
from rich.table import Table

# -- project source on sys.path ------------------------------------------------
_SRC = Path(__file__).resolve().parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

app = typer.Typer(
    name="tc_agent",
    help="TriConvey Agent  -  PDF extraction to form fill pipeline.",
    no_args_is_help=True,
    pretty_exceptions_enable=False,
)
console = Console()

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

_SAMPLES = Path(__file__).resolve().parent / "samples"
_YAML_DIR = Path(__file__).resolve().parent / "triconvey-mapping"

def _default_docs() -> list[Path]:
    """Return every PDF currently present in the samples directory."""
    return sorted(
        path for path in _SAMPLES.rglob("*")
        if path.is_file() and path.suffix.lower() == ".pdf"
    )

DEFAULT_OUT = Path("output")


# ---------------------------------------------------------------------------
# Shared import helpers (lazy  -  avoids slow startup when only --help is run)
# ---------------------------------------------------------------------------

def _import_extractors():
    from triconvey_agent.canonical.extractors import (
        extract_building_approval_facts,
        extract_generic_doc_meta_facts,
        extract_vendor_form_facts,
        extract_vic_title_facts,
        extract_water_authority_certificate_facts,
        extract_land_tax_certificate_facts,
        extract_planning_certificate_facts,
        extract_owners_corporation_facts,
    )
    return [
        extract_generic_doc_meta_facts,
        extract_building_approval_facts,
        extract_vendor_form_facts,
        extract_vic_title_facts,
        extract_water_authority_certificate_facts,
        extract_land_tax_certificate_facts,
        extract_planning_certificate_facts,
        extract_owners_corporation_facts,
    ]


def _run_extractors(doc, extractors) -> list:
    facts = []
    for fn in extractors:
        try:
            facts.extend(fn(doc))
        except Exception as exc:
            console.print(f"  [yellow][WARN][/yellow] {fn.__name__}: {exc}")
    return facts


# ---------------------------------------------------------------------------
# COMMAND: extract
# ---------------------------------------------------------------------------

@app.command()
def extract(
    docs: Optional[list[Path]] = typer.Option(
        None, "--docs", "-d",
        help="PDF files to process. Defaults to every PDF in samples/.",
        exists=True, file_okay=True, dir_okay=False, resolve_path=True,
    ),
    out: Path = typer.Option(DEFAULT_OUT, "--out", "-o", help="Output directory."),
):
    """Brain A  -  extract facts from PDF documents into facts.json."""
    from triconvey_agent.canonical.facts.store import FactStoreImpl
    from triconvey_agent.ingest.pdf_loader import load_pdf_document

    doc_paths = list(docs) if docs else _default_docs()
    if not doc_paths:
        console.print(f"[red]No PDF files found in default samples folder:[/red] {_SAMPLES}")
        raise typer.Exit(1)
    out.mkdir(parents=True, exist_ok=True)
    extractors = _import_extractors()

    console.rule("[bold cyan]Brain A  -  Extracting facts[/bold cyan]")
    store = FactStoreImpl()
    total = 0
    for path in doc_paths:
        if not path.exists():
            console.print(f"  [yellow][SKIP][/yellow] {path.name}  -  not found")
            continue
        console.print(f"  Loading [bold]{path.name}[/bold] ...", end=" ")
        try:
            doc = load_pdf_document(path)
            facts = _run_extractors(doc, extractors)
            store.add_many(facts)
            total += len(facts)
            console.print(f"[green]{len(facts)} fact(s)[/green]")
        except Exception as exc:
            console.print(f"[red]ERROR[/red]  -  {exc}")

    (out / "facts.json").write_text(
        json.dumps(store.to_dict(), indent=2, default=str), encoding="utf-8"
    )
    console.print(f"\n[green]facts.json written  -  {total} facts from {len(doc_paths)} document(s)[/green]")
    console.print(f"  Location: {(out / 'facts.json').resolve()}")


# ---------------------------------------------------------------------------
# COMMAND: answer
# ---------------------------------------------------------------------------

@app.command()
def answer(
    out: Path = typer.Option(DEFAULT_OUT, "--out", "-o", help="Directory containing facts.json and output target."),
    yaml_dir: Path = typer.Option(_YAML_DIR, "--yaml-dir", help="YAML tab field definitions (for Brain D preview)."),
):
    """Brain B + C  -  answer questions and apply client policy. Reads facts.json, writes answers.json."""
    from triconvey_agent.canonical.facts.store import FactStoreImpl
    from triconvey_agent.canonical.questions.loader import load_question_registry
    from triconvey_agent.canonical.router import answer_all_questions
    from triconvey_agent.canonical.policy import run_policy_pass

    facts_file = out / "facts.json"
    if not facts_file.exists():
        console.print(f"[red]facts.json not found in {out}. Run 'extract' first.[/red]")
        raise typer.Exit(1)

    console.rule("[bold cyan]Brain B+C  -  Answering questions + Policy pass[/bold cyan]")

    # Rebuild store from facts.json
    store = FactStoreImpl()
    raw = json.loads(facts_file.read_text(encoding="utf-8"))
    _restore_store(store, raw)

    # Brain C  -  policy
    console.print("  Applying client policy ...")
    run_policy_pass(store)

    # Brain B  -  answer
    registry = load_question_registry()
    answers = answer_all_questions(registry.values(), store)

    ready = [a for a in answers.values() if not a.needs_review and a.value is not None]
    review_items = [a for a in answers.values() if a.needs_review]
    console.print(f"  Questions ready:   [green]{len(ready)}/{len(answers)}[/green]")
    console.print(f"  Need review:       [yellow]{len(review_items)}[/yellow]")

    answers_json = {qid: ans.model_dump(mode="json") for qid, ans in answers.items()}
    (out / "answers.json").write_text(
        json.dumps(answers_json, indent=2, default=str), encoding="utf-8"
    )
    console.print(f"\n[green]answers.json written  -  {len(answers)} answers[/green]")
    console.print(f"  Location: {(out / 'answers.json').resolve()}")


# ---------------------------------------------------------------------------
# COMMAND: plan
# ---------------------------------------------------------------------------

@app.command()
def plan(
    out: Path = typer.Option(DEFAULT_OUT, "--out", "-o", help="Directory containing answers.json."),
    yaml_dir: Path = typer.Option(_YAML_DIR, "--yaml-dir", help="YAML tab field definitions."),
):
    """Brain D  -  build TriConvey form action plan from answers.json."""
    from triconvey_agent.canonical.questions.loader import load_question_registry
    from triconvey_agent.canonical.brain_d import build_action_plan
    from triconvey_agent.canonical.schemas import AnswerObject

    answers_file = out / "answers.json"
    if not answers_file.exists():
        console.print(f"[red]answers.json not found in {out}. Run 'answer' first.[/red]")
        raise typer.Exit(1)

    if not yaml_dir.exists():
        console.print(f"[red]YAML tab dir not found: {yaml_dir}[/red]")
        raise typer.Exit(1)

    console.rule("[bold cyan]Brain D  -  Building action plan[/bold cyan]")

    raw_answers = json.loads(answers_file.read_text(encoding="utf-8"))
    answers = {qid: AnswerObject(**data) for qid, data in raw_answers.items()}

    action_plan = build_action_plan(answers, yaml_dir)
    auto = [a for a in action_plan.actions if a.action != "skip"]
    skipped = [a for a in action_plan.actions if a.action == "skip"]

    console.print(f"  Auto-fill actions: [green]{len(auto)}[/green]")
    console.print(f"  Skip/review gates: [yellow]{len(skipped)}[/yellow]")
    console.print(f"  Review gate:       {'[red]YES[/red]' if action_plan.review_gate_required else '[green]NO[/green]'}")

    (out / "action_plan.json").write_text(action_plan.model_dump_json(indent=2), encoding="utf-8")
    console.print(f"\n[green]action_plan.json written  -  {len(action_plan.actions)} actions[/green]")
    console.print(f"  Location: {(out / 'action_plan.json').resolve()}")


# ---------------------------------------------------------------------------
# COMMAND: fill
# ---------------------------------------------------------------------------

@app.command()
def fill(
    out: Path = typer.Option(DEFAULT_OUT, "--out", "-o", help="Directory containing action_plan.json."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Log actions but do not touch the UI."),
    client: Optional[str] = typer.Option(None, "--client", help="Client full name override (auto-extracted from facts.json if omitted)."),
    triconvey_exe: Optional[str] = typer.Option(None, "--exe", help=r"TriConvey exe path override (e.g. C:\Program Files\triConvey\triConvey.exe)."),
    skip_review_gate: bool = typer.Option(False, "--skip-review-gate", help="Skip the review gate prompt and fill all auto-fill actions."),
):
    """Brain E  -  fill TriConvey form from action_plan.json using pywinauto."""
    from triconvey_agent.canonical.schemas import FormActionPlan
    from triconvey_agent.canonical.brain_e import execute_action_plan

    plan_file = out / "action_plan.json"
    if not plan_file.exists():
        console.print(f"[red]action_plan.json not found in {out}. Run 'plan' first.[/red]")
        raise typer.Exit(1)

    # Resolve client name: explicit override > auto-extracted from facts
    client_name = client or _extract_client_name(out)
    if client_name:
        console.print(f"  Client: [bold]{client_name}[/bold]")
    else:
        console.print("  [yellow]Client name not found - TriConvey will connect but won't search.[/yellow]")

    console.rule("[bold cyan]Brain E  -  Filling TriConvey form[/bold cyan]")
    if dry_run:
        console.print("  [yellow]DRY-RUN mode  -  no UI changes will be made[/yellow]")

    action_plan = FormActionPlan.model_validate_json(plan_file.read_text(encoding="utf-8"))
    auto = [a for a in action_plan.actions if a.action != "skip"]
    console.print(f"  Actions to execute: {len(auto)}")

    def _review_callback(review_items):
        if skip_review_gate or dry_run:
            return True  # auto-proceed in dry-run; no UI to block anyway
        return _default_review_prompt(review_items)

    try:
        report = execute_action_plan(
            action_plan,
            client_name=client_name or "",
            dry_run=dry_run,
            triconvey_exe=triconvey_exe,
            review_gate_callback=_review_callback,
            output_dir=out,
        )
    except RuntimeError as exc:
        console.print(f"[red]ERROR: {exc}[/red]")
        raise typer.Exit(1)

    # Save report
    (out / "execution_report.json").write_text(
        report.model_dump_json(indent=2), encoding="utf-8"
    )

    # Summary
    console.print()
    console.rule("[bold]Execution Summary[/bold]")
    console.print(f"  Filled:         [green]{report.total_filled}[/green]")
    console.print(f"  Verified:       [green]{report.total_verified}[/green]")
    console.print(f"  Skipped:        {report.total_skipped}")
    console.print(f"  Pending review: [yellow]{report.total_pending_review}[/yellow]")
    console.print(f"  Failed:         [red]{report.total_failed}[/red]")

    if report.total_failed:
        console.print("\n[red]Failed actions:[/red]")
        for r in report.results:
            if r.status == "failed":
                console.print(f"  - {r.action.field_id}: {r.error}")

    console.print(f"\n[green]execution_report.json written[/green]  -  {(out / 'execution_report.json').resolve()}")
    if report.diagnostics_dir:
        console.print(f"  [cyan]Execution artifacts:[/cyan] {report.diagnostics_dir}")


# ---------------------------------------------------------------------------
# COMMAND: review
# ---------------------------------------------------------------------------

@app.command()
def review(
    out: Path = typer.Option(DEFAULT_OUT, "--out", "-o", help="Directory containing answers.json."),
    tab: Optional[str] = typer.Option(None, "--tab", help="Filter to a specific tab (e.g. 'Sec. 32 (1)')."),
):
    """Show all flagged review items from answers.json."""
    from triconvey_agent.canonical.questions.loader import load_question_registry
    from triconvey_agent.canonical.schemas import AnswerObject

    answers_file = out / "answers.json"
    if not answers_file.exists():
        console.print(f"[red]answers.json not found in {out}. Run 'answer' first.[/red]")
        raise typer.Exit(1)

    raw_answers = json.loads(answers_file.read_text(encoding="utf-8"))
    answers = {qid: AnswerObject(**data) for qid, data in raw_answers.items()}
    registry = load_question_registry()

    review_items = [(qid, ans) for qid, ans in answers.items() if ans.needs_review]
    auto_items = [(qid, ans) for qid, ans in answers.items() if not ans.needs_review and ans.value is not None]

    console.rule("[bold cyan]Review Queue[/bold cyan]")
    console.print(f"  Total questions:  {len(answers)}")
    console.print(f"  Auto-fill ready:  [green]{len(auto_items)}[/green]")
    console.print(f"  Need review:      [yellow]{len(review_items)}[/yellow]")

    # Table of review items
    if review_items:
        console.print()
        t = Table(title="Fields Requiring Human Review", show_lines=True)
        t.add_column("Question ID", style="cyan", no_wrap=True)
        t.add_column("Tab", style="dim")
        t.add_column("Label")
        t.add_column("Reason", style="yellow")
        t.add_column("Current Value", style="dim")

        for qid, ans in review_items:
            q = registry.get(qid)
            q_tab = q.tab if q else "?"
            if tab and q_tab != tab:
                continue
            reason = "; ".join(ans.review_reasons[:2]) if ans.review_reasons else "unknown"
            value_str = repr(ans.value) if ans.value is not None else " - "
            t.add_row(qid, q_tab, ans.question_label, reason, value_str)

        console.print(t)
    else:
        console.print("\n[green]No review items  -  all questions answered automatically.[/green]")

    # Auto-fill summary
    if auto_items:
        console.print()
        t2 = Table(title="Auto-Fill Ready", show_lines=False, box=None)
        t2.add_column("Question ID", style="cyan")
        t2.add_column("Tab", style="dim")
        t2.add_column("Value", style="green")
        for qid, ans in auto_items:
            q = registry.get(qid)
            q_tab = q.tab if q else "?"
            if tab and q_tab != tab:
                continue
            t2.add_row(qid, q_tab, repr(ans.value))
        console.print(t2)


# ---------------------------------------------------------------------------
# COMMAND: ui
# ---------------------------------------------------------------------------

@app.command()
def ui(
    host: str = typer.Option("127.0.0.1", "--host", help="Host interface for the web UI."),
    port: int = typer.Option(8765, "--port", help="Port for the web UI."),
):
    """Launch the backend API used by the review UI and autofill workflow."""
    import uvicorn

    console.rule("[bold cyan]TriConvey Agent Backend API[/bold cyan]")
    console.print(f"  Launching API on [bold]http://{host}:{port}[/bold]")
    uvicorn.run("triconvey_agent.backend.api:app", host=host, port=port, reload=False)


# ---------------------------------------------------------------------------
# COMMAND: run  (full pipeline)
# ---------------------------------------------------------------------------

@app.command()
def run(
    docs: Optional[list[Path]] = typer.Option(
        None, "--docs", "-d",
        help="PDF files to process. Defaults to every PDF in samples/.",
        exists=True, file_okay=True, dir_okay=False, resolve_path=True,
    ),
    out: Path = typer.Option(DEFAULT_OUT, "--out", "-o", help="Output directory."),
    yaml_dir: Path = typer.Option(_YAML_DIR, "--yaml-dir", help="YAML tab field definitions."),
    fill_form: bool = typer.Option(False, "--fill", help="Also run Brain E to fill TriConvey after planning."),
    dry_run: bool = typer.Option(False, "--dry-run", help="(Brain E) Log actions only, no UI changes."),
    triconvey_exe: Optional[str] = typer.Option(None, "--exe", help=r"TriConvey exe path override."),
    skip_review_gate: bool = typer.Option(False, "--skip-review-gate", help="Skip the review gate prompt."),
):
    """Full pipeline: extract -> answer -> policy -> plan [-> fill].

    Equivalent to running: extract + answer + plan [+ fill]
    """
    from triconvey_agent.canonical.facts.store import FactStoreImpl
    from triconvey_agent.canonical.questions.loader import load_question_registry
    from triconvey_agent.canonical.router import answer_all_questions
    from triconvey_agent.canonical.policy import run_policy_pass
    from triconvey_agent.canonical.brain_d import build_action_plan
    from triconvey_agent.ingest.pdf_loader import load_pdf_document

    doc_paths = list(docs) if docs else _default_docs()
    if not doc_paths:
        console.print(f"[red]No PDF files found in default samples folder:[/red] {_SAMPLES}")
        raise typer.Exit(1)
    out.mkdir(parents=True, exist_ok=True)
    extractors = _import_extractors()

    # -- Brain A -------------------------------------------------------------
    console.rule("[bold cyan]Brain A  -  Extracting facts[/bold cyan]")
    store = FactStoreImpl()
    total_facts = 0
    for path in doc_paths:
        if not path.exists():
            console.print(f"  [yellow][SKIP][/yellow] {path.name}  -  not found")
            continue
        console.print(f"  Loading [bold]{path.name}[/bold] ...", end=" ")
        try:
            doc = load_pdf_document(path)
            facts = _run_extractors(doc, extractors)
            store.add_many(facts)
            total_facts += len(facts)
            console.print(f"[green]{len(facts)} fact(s)[/green]")
        except Exception as exc:
            console.print(f"[red]ERROR[/red]  -  {exc}")

    console.print(f"  Total facts extracted: [bold]{total_facts}[/bold]")

    # -- Brain C -------------------------------------------------------------
    console.rule("[bold cyan]Brain C  -  Client policy pass[/bold cyan]")
    try:
        run_policy_pass(store)
        policy_count = store.fact_count() - total_facts
        console.print(f"  Policy facts injected: [green]{policy_count}[/green]")

        water_verify, _ = store.get("policy.verification.water_amount_match")
        if water_verify is not None:
            if water_verify.value is True:
                status_str = "[green]OK  -  amounts match[/green]"
            elif water_verify.value is False:
                status_str = "[red]DISCREPANCY  -  review required[/red]"
            else:
                status_str = "[dim]N/A[/dim]"
            console.print(f"  Water amount check:    {status_str}")
            if water_verify.notes:
                console.print(f"    [dim]{water_verify.notes}[/dim]")
    except Exception as exc:
        console.print(f"  [yellow][WARN][/yellow] Policy pass failed: {exc}")

    # Serialise facts
    (out / "facts.json").write_text(
        json.dumps(store.to_dict(), indent=2, default=str), encoding="utf-8"
    )

    # -- Brain B -------------------------------------------------------------
    console.rule("[bold cyan]Brain B  -  Answering questions[/bold cyan]")
    registry = load_question_registry()
    answers = answer_all_questions(registry.values(), store)

    ready = [a for a in answers.values() if not a.needs_review and a.value is not None]
    review_list = [a for a in answers.values() if a.needs_review]
    console.print(f"  Ready for auto-fill: [green]{len(ready)}/{len(answers)}[/green]")
    console.print(f"  Need human review:   [yellow]{len(review_list)}[/yellow]")

    answers_json = {qid: ans.model_dump(mode="json") for qid, ans in answers.items()}
    (out / "answers.json").write_text(
        json.dumps(answers_json, indent=2, default=str), encoding="utf-8"
    )

    # -- Brain D -------------------------------------------------------------
    if yaml_dir.exists():
        console.rule("[bold cyan]Brain D  -  Building action plan[/bold cyan]")
        action_plan = build_action_plan(answers, yaml_dir)
        auto_actions = [a for a in action_plan.actions if a.action != "skip"]
        skip_actions = [a for a in action_plan.actions if a.action == "skip"]
        console.print(f"  Auto-fill actions:  [green]{len(auto_actions)}[/green]")
        console.print(f"  Skip/review gates:  [yellow]{len(skip_actions)}[/yellow]")
        console.print(f"  Review gate needed: {'[red]YES[/red]' if action_plan.review_gate_required else '[green]NO[/green]'}")

        (out / "action_plan.json").write_text(action_plan.model_dump_json(indent=2), encoding="utf-8")
    else:
        console.print(f"  [yellow][SKIP][/yellow] YAML dir not found: {yaml_dir}  -  action_plan.json not written")
        action_plan = None

    # -- Brain E (optional) --------------------------------------------------
    if fill_form and action_plan is not None:
        console.rule("[bold cyan]Brain E  -  Filling TriConvey form[/bold cyan]")
        if dry_run:
            console.print("  [yellow]DRY-RUN mode  -  no UI changes[/yellow]")
        from triconvey_agent.canonical.brain_e import execute_action_plan

        # Extract client name from the facts we just collected
        client_name = _extract_client_name_from_store(store)
        if client_name:
            console.print(f"  Client: [bold]{client_name}[/bold]")

        def _review_cb(review_items):
            if skip_review_gate or dry_run:
                return True
            return _default_review_prompt(review_items)

        try:
            report = execute_action_plan(
                action_plan,
                client_name=client_name or "",
                dry_run=dry_run,
                triconvey_exe=triconvey_exe,
                review_gate_callback=_review_cb,
                output_dir=out,
            )
            (out / "execution_report.json").write_text(report.model_dump_json(indent=2), encoding="utf-8")
            console.print(f"  Filled:  [green]{report.total_filled}[/green]  |  Failed: [red]{report.total_failed}[/red]  |  Pending review: [yellow]{report.total_pending_review}[/yellow]")
            if report.diagnostics_dir:
                console.print(f"  Execution artifacts: [cyan]{report.diagnostics_dir}[/cyan]")
        except RuntimeError as exc:
            console.print(f"  [red]Brain E error:[/red] {exc}")
    elif fill_form:
        console.print("[yellow]Cannot fill  -  action plan was not built (YAML dir missing).[/yellow]")

    # -- Summary -------------------------------------------------------------
    _write_summary_txt(answers, registry, out)

    console.rule("[bold green]Done[/bold green]")
    console.print(f"  Output directory: [bold]{out.resolve()}[/bold]")
    console.print(f"    facts.json        -  {total_facts} facts")
    console.print(f"    answers.json      -  {len(answers)} answers ({len(ready)} auto, {len(review_list)} review)")
    if action_plan is not None:
        console.print(f"    action_plan.json  -  {len(action_plan.actions)} form actions")
    console.print(f"    summary.txt       -  human-readable checklist")
    if review_list:
        console.print(f"\n  [yellow]Run 'python tc_agent.py review' to see all review items.[/yellow]")
    if action_plan is not None and not fill_form:
        console.print(f"  [cyan]Run 'python tc_agent.py fill' to fill TriConvey.[/cyan]")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _restore_store(store, raw: dict):
    """Re-populate a FactStoreImpl from the to_dict() output."""
    from triconvey_agent.canonical.schemas import Fact
    for path, path_data in raw.get("facts", {}).items():
        facts_list = path_data.get("facts", [])
        for f_data in facts_list:
            try:
                store.add(Fact(**f_data))
            except Exception:
                pass


def _extract_client_name_from_store(store) -> str:
    """Extract client full name directly from a live FactStoreImpl."""
    try:
        first_fact, _ = store.get("vendor.0.first_name")
        last_fact, _ = store.get("vendor.0.last_name")
        first = str(first_fact.value).strip() if first_fact else ""
        last = str(last_fact.value).strip() if last_fact else ""
        if first and last:
            return f"{first} {last}"
        if first:
            return first
        pref_fact, _ = store.get("vendor.0.preferred_name")
        if pref_fact:
            return str(pref_fact.value).strip()
    except Exception:
        pass
    return ""


def _extract_client_name(out: Path) -> str:
    """Build the client's full name from vendor facts in facts.json.

    Looks for vendor.0.first_name + vendor.0.last_name (primary vendor).
    Falls back to vendor.0.preferred_name.
    Returns an empty string if nothing is found.

    facts.json structure: {"facts": {"path": [fact_dict, ...]}, ...}
    """
    facts_file = out / "facts.json"
    if not facts_file.exists():
        return ""
    try:
        data = json.loads(facts_file.read_text(encoding="utf-8"))
        facts_map = data.get("facts", {})  # {path: list[fact_dict]}

        def _get(path: str) -> str:
            # Each entry is a list of fact dicts directly
            entries = facts_map.get(path, [])
            if isinstance(entries, list):
                for f in entries:
                    v = f.get("value") if isinstance(f, dict) else None
                    if v:
                        return str(v).strip()
            elif isinstance(entries, dict):
                # Older format: {facts: [...]}
                for f in entries.get("facts", []):
                    v = f.get("value")
                    if v:
                        return str(v).strip()
            return ""

        first = _get("vendor.0.first_name")
        last = _get("vendor.0.last_name")
        if first and last:
            return f"{first} {last}"
        if first:
            return first
        return _get("vendor.0.preferred_name")
    except Exception:
        pass
    return ""


def _default_review_prompt(review_items) -> bool:
    console.print("\n[bold yellow]REVIEW GATE[/bold yellow]  -  the following need human review:")
    for item in review_items:
        console.print(f"  [{item.question_id}]  payload={item.payload!r}")
    answer = typer.prompt("\nProceed with auto-fill for non-review fields? [y/N]", default="N")
    return answer.strip().lower() == "y"


def _write_summary_txt(answers, registry, out: Path) -> None:
    lines = ["TRICONVEY AGENT - ANSWER SUMMARY", "=" * 60, ""]
    tabs: dict[str, list] = {}
    for qid, ans in answers.items():
        q = registry.get(qid)
        tab = q.tab if q else "Unknown"
        tabs.setdefault(tab, []).append((qid, ans, q))

    for tab, items in sorted(tabs.items()):
        lines.append(f"--- {tab} ---")
        for qid, ans, q in items:
            if ans.needs_review:
                status = "! REVIEW  "
                detail = f"[{', '.join(ans.review_reasons[:1])}]" if ans.review_reasons else ""
            else:
                status = "* AUTO    "
                detail = ""
            value_str = repr(ans.value) if ans.value is not None else "---"
            lines.append(f"  {status}  {ans.question_label:<50}  {value_str}  {detail}")
        lines.append("")

    (out / "summary.txt").write_text("\n".join(lines), encoding="utf-8")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    app()
