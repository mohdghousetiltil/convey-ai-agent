from __future__ import annotations

import json
import os
import time
from pathlib import Path

from triconvey_agent.brain_f.cache import prime_cached_pdf_analysis
from triconvey_agent.canonical.extractors import (
    extract_building_approval_facts,
    extract_council_rates_certificate_facts,
    extract_generic_doc_meta_facts,
    extract_land_tax_certificate_facts,
    extract_owners_corporation_facts,
    extract_planning_certificate_facts,
    extract_vendor_form_facts,
    extract_vic_title_facts,
    extract_water_authority_certificate_facts,
)
from triconvey_agent.canonical.facts.store import FactStoreImpl
from triconvey_agent.canonical.policy import run_policy_pass
from triconvey_agent.canonical.runner.doc_router import classify_document_for_rates
from triconvey_agent.ingest.pdf_loader import load_document

SAMPLES_DIR = Path(__file__).resolve().parents[4] / "samples"
_SUPPORTED_DOC_SUFFIXES = {".pdf", ".docx", ".doc"}
_SKIP_FILENAME_PATTERNS = (
    "certificate of currency",
    "insurance certificate",
    "customer_notice",
    "customer notice",
    "notice_unlocked",
)

BASE_EXTRACTORS = [
    extract_generic_doc_meta_facts,
    extract_building_approval_facts,
    extract_vendor_form_facts,
    extract_vic_title_facts,
    extract_land_tax_certificate_facts,
    extract_planning_certificate_facts,
]

ENABLE_OMNI = os.getenv("TRICONVEY_ENABLE_OMNI", "1").lower() not in {"0", "false", "no", "off"}


def default_docs() -> list[Path]:
    return sorted(
        path for path in SAMPLES_DIR.rglob("*")
        if path.is_file() and path.suffix.lower() in _SUPPORTED_DOC_SUFFIXES
    )


def run_all_extractors(doc, *, ai_client=None) -> list:
    facts = []

    for fn in BASE_EXTRACTORS:
        try:
            facts.extend(fn(doc))
        except Exception as exc:
            print(f"  [WARN] {fn.__name__} raised {type(exc).__name__}: {exc}")

    kind = classify_document_for_rates(doc)
    print(f"  [Router] {doc.filename} -> {kind}")

    effective_ai_client = ai_client if ENABLE_OMNI else None
    if kind == "land_certificate":
        routed_extractors = [extract_council_rates_certificate_facts]
    elif kind == "water_certificate":
        routed_extractors = [extract_water_authority_certificate_facts]
    elif kind == "owners_corporation":
        routed_extractors = [extract_owners_corporation_facts]
    else:
        routed_extractors = []

    for fn in routed_extractors:
        try:
            if effective_ai_client is not None:
                facts.extend(fn(doc, ai_client=effective_ai_client))
            else:
                facts.extend(fn(doc))
        except Exception as exc:
            print(f"  [WARN] {fn.__name__} raised {type(exc).__name__}: {exc}")

    return facts


def _format_elapsed(seconds: float) -> str:
    return f"{seconds:.2f}s"


def _should_skip_path(path: Path) -> bool:
    lower_name = path.name.lower()
    return any(token in lower_name for token in _SKIP_FILENAME_PATTERNS)


def extract_fact_store(
    doc_paths: list[Path],
    out_dir: Path,
    *,
    ai_client=None,
    copy_rules: list[tuple[str, float]] | None = None,
) -> tuple[FactStoreImpl, int]:
    out_dir.mkdir(parents=True, exist_ok=True)
    overall_started = time.perf_counter()

    print("\n=== Brain A - Extracting facts ===")
    brain_a_started = time.perf_counter()
    store = FactStoreImpl()
    total_facts = 0

    for path in doc_paths:
        if not path.exists():
            print(f"  [SKIP] {path.name} - file not found")
            continue
        if _should_skip_path(path):
            print(f"  [SKIP] {path.name} - non-section32 supporting file")
            continue

        doc_started = time.perf_counter()
        print(f"  Loading {path.name} ...", end=" ", flush=True)
        try:
            doc = load_document(path)
            prime_cached_pdf_analysis(path, doc)
            facts = run_all_extractors(doc, ai_client=ai_client)
            store.add_many(facts)
            total_facts += len(facts)
            print(f"{len(facts)} fact(s) [{_format_elapsed(time.perf_counter() - doc_started)}]")
        except Exception as exc:
            print(f"ERROR - {type(exc).__name__}: {exc}")

    print(f"  Total facts in store: {total_facts}")
    print(f"  [Time] Brain A total: {_format_elapsed(time.perf_counter() - brain_a_started)}")

    if copy_rules:
        try:
            from triconvey_agent.copy_rules import find_best_copy_rule_match
            from triconvey_agent.canonical.schemas import Fact
            import fnmatch

            water_amount_facts = store.get_all("rates.water.annual_amount")
            has_water_doc_amount = any(
                fnmatch.fnmatch(f.extractor, "rule:water_authority_certificate*")
                or fnmatch.fnmatch(f.extractor, "ai:doc_extractor:water_authority_certificate*")
                for f in water_amount_facts
            )

            # Hierarchy:
            # 1) Water authority document amount
            # 2) Copy-rule DB amount (only when 1 is missing)
            # 3) Vendor-form fallback handled by authority resolver rules
            if has_water_doc_amount:
                print("  [Copy Rule] Skipped for water amount: certificate amount already extracted.")
            else:
                authority_fact, _ = store.get("rates.water.authority_name")
                authority_name = (
                    str(authority_fact.value).strip()
                    if authority_fact and authority_fact.value else ""
                )
                if authority_name:
                    match = find_best_copy_rule_match(authority_name, copy_rules)
                    if match is not None:
                        store.add(
                            Fact(
                                path="rates.water.annual_amount",
                                value=f"${match.annual_amount:,.2f}",
                                confidence=0.99,
                                extractor="copy_rule:water_authority",
                                notes=(
                                    f"DB copy rule: '{match.authority_name}' "
                                    f"(matched_on={match.matched_on}, score={match.score:.3f}). "
                                    "Applied because no water certificate amount was extracted."
                                ),
                                sources=[],
                            )
                        )
                        print(
                            f"  [Copy Rule] Water authority '{authority_name}' -> "
                            f"${match.annual_amount:,.2f} (score={match.score:.3f})"
                        )
        except Exception as exc:
            print(f"  [WARN] Copy-rule water injection failed: {exc}")

    print("\n=== Brain C - Policy pass ===")
    brain_c_started = time.perf_counter()
    try:
        run_policy_pass(store)
        policy_facts = store.fact_count() - total_facts
        print(f"  Policy facts injected: {policy_facts}")
        water_verify, _ = store.get("policy.verification.water_amount_match")
        if water_verify is not None:
            status = "OK" if water_verify.value is True else (
                "DISCREPANCY - REVIEW" if water_verify.value is False else "N/A"
            )
            print(f"  Water amount check:    {status}")
            if water_verify.notes:
                print(f"    {water_verify.notes}")
    except Exception as exc:
        print(f"  [WARN] Policy pass failed: {type(exc).__name__}: {exc}")
    print(f"  [Time] Brain C total: {_format_elapsed(time.perf_counter() - brain_c_started)}")

    (out_dir / "facts.json").write_text(
        json.dumps(store.to_dict(), indent=2, default=str), encoding="utf-8"
    )
    print(f"  [Time] Brain A + C total: {_format_elapsed(time.perf_counter() - overall_started)}")

    _write_rates_debug(store, out_dir)
    return store, total_facts


_RATES_DEBUG_PATHS = [
    ("rates.council.authority_name", "Council Authority"),
    ("rates.council.annual_amount", "Council Amount"),
    ("rates.water.authority_name", "Water Authority"),
    ("rates.water.annual_amount", "Water Amount"),
    ("rates.land_tax.authority_name", "Land Tax Authority"),
    ("rates.land_tax.amount", "Land Tax Amount"),
    ("rates.owners_corporation.authority_name", "OC Authority"),
    ("rates.owners_corporation.annual_amount", "OC Amount"),
]


def _write_rates_debug(store, out_dir: Path) -> None:
    lines: list[str] = []
    lines.append("=" * 70)
    lines.append("RATES DEBUG  --  Authority & Amount Resolution")
    lines.append("=" * 70)
    lines.append("")

    for path, label in _RATES_DEBUG_PATHS:
        all_facts = store.get_all(path)
        winner, conflict = store.get(path)
        lines.append("-" * 70)
        lines.append(f"  PATH : {path}")
        lines.append(f"  LABEL: {label}")

        if not all_facts:
            lines.append("  RESULT : [NO FACTS -- nothing extracted from any document]")
            lines.append("")
            continue

        final_val = winner.value if winner else "NEEDS REVIEW (no clear winner)"
        final_conf = winner.confidence if winner else 0.0
        final_src = winner.extractor if winner else "--"
        resolution = conflict.resolution if conflict else "single fact"
        lines.append(f"  FINAL  : {final_val}")
        lines.append(f"  CONF   : {final_conf:.2f}")
        lines.append(f"  SOURCE : {final_src}")
        lines.append(f"  HOW    : {resolution}")
        lines.append("")

        if len(all_facts) == 1:
            f = all_facts[0]
            src_file = f.sources[0].file if f.sources else "unknown"
            lines.append("  ONLY CANDIDATE:")
            lines.append(f"    value     = {f.value!r}")
            lines.append(f"    conf      = {f.confidence:.2f}")
            lines.append(f"    extractor = {f.extractor}")
            lines.append(f"    file      = {src_file}")
        else:
            lines.append(f"  ALL CANDIDATES ({len(all_facts)} facts, highest conf first):")
            for f in sorted(all_facts, key=lambda x: -x.confidence):
                is_win = winner and f.extractor == winner.extractor and str(f.value) == str(winner.value)
                mark = "  [WINNER]" if is_win else "          "
                src_file = f.sources[0].file if f.sources else "unknown"
                lines.append(f"  {mark}")
                lines.append(f"    value     = {f.value!r}")
                lines.append(f"    conf      = {f.confidence:.2f}")
                lines.append(f"    extractor = {f.extractor}")
                lines.append(f"    file      = {src_file}")
                if f.notes:
                    lines.append(f"    notes     = {f.notes[:120]}")
        lines.append("")

    lines.append("=" * 70)
    lines.append("END RATES DEBUG")
    lines.append("=" * 70)
    output = "\n".join(lines)
    print(output)

    log_path = out_dir / "rates_debug.log"
    try:
        log_path.write_text(output, encoding="utf-8")
        print(f"  [Rates Debug] Also written to {log_path}")
    except Exception as exc:
        print(f"  [WARN] Could not write rates_debug.log: {exc}")
