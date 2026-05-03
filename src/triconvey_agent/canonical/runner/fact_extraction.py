from __future__ import annotations

import json
import time
from pathlib import Path

from triconvey_agent.brain_f.cache import prime_cached_pdf_analysis
from triconvey_agent.canonical.doc_router import (
    DOC_TYPE_BUILDING,
    DOC_TYPE_COUNCIL,
    DOC_TYPE_LAND_TAX,
    DOC_TYPE_OC,
    DOC_TYPE_PLANNING,
    DOC_TYPE_TITLE,
    DOC_TYPE_UNKNOWN,
    DOC_TYPE_VENDOR,
    DOC_TYPE_WATER,
    classify_document,
)
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
from triconvey_agent.ingest.pdf_loader import load_document

SAMPLES_DIR = Path(__file__).resolve().parents[4] / "samples"

_SUPPORTED_DOC_SUFFIXES = {".pdf", ".docx", ".doc"}

# Extractors that accept an ai_client keyword argument
_AI_CLIENT_EXTRACTORS = frozenset({
    "extract_council_rates_certificate_facts",
    "extract_water_authority_certificate_facts",
    "extract_owners_corporation_facts",
})

# Doc-type → specific extractor (all also run extract_generic_doc_meta_facts)
_EXTRACTOR_MAP = {
    DOC_TYPE_COUNCIL:   extract_council_rates_certificate_facts,
    DOC_TYPE_WATER:     extract_water_authority_certificate_facts,
    DOC_TYPE_LAND_TAX:  extract_land_tax_certificate_facts,
    DOC_TYPE_OC:        extract_owners_corporation_facts,
    DOC_TYPE_VENDOR:    extract_vendor_form_facts,
    DOC_TYPE_PLANNING:  extract_planning_certificate_facts,
    DOC_TYPE_BUILDING:  extract_building_approval_facts,
    DOC_TYPE_TITLE:     extract_vic_title_facts,
}

# Fall-back list used when routing produces "unknown"
_ALL_SPECIFIC_EXTRACTORS = [
    extract_building_approval_facts,
    extract_vendor_form_facts,
    extract_vic_title_facts,
    extract_water_authority_certificate_facts,
    extract_land_tax_certificate_facts,
    extract_planning_certificate_facts,
    extract_owners_corporation_facts,
    extract_council_rates_certificate_facts,
]


def default_docs() -> list[Path]:
    """Return every PDF or Word document currently present in the samples directory."""
    return sorted(
        path for path in SAMPLES_DIR.rglob("*")
        if path.is_file() and path.suffix.lower() in _SUPPORTED_DOC_SUFFIXES
    )


def _run_extractor(fn, doc, *, ai_client):
    """Run a single extractor, forwarding ai_client when the extractor supports it."""
    if fn.__name__ in _AI_CLIENT_EXTRACTORS and ai_client is not None:
        return fn(doc, ai_client=ai_client)
    return fn(doc)


def run_all_extractors(doc, *, ai_client=None) -> list:
    """Route the document then run only the matching extractor.

    Steps
    -----
    1. Always run extract_generic_doc_meta_facts (metadata applies to all docs).
    2. Classify the document with the doc router (rule-based, AI fallback).
    3. Run the single matching specific extractor.
    4. For unknown/ambiguous docs, run all specific extractors as a safety net
       (each extractor's own trigger guard will reject documents it doesn't own).
    """
    facts = []

    # Generic metadata — always runs
    try:
        facts.extend(extract_generic_doc_meta_facts(doc))
    except Exception as exc:
        print(f"  [WARN] extract_generic_doc_meta_facts raised {type(exc).__name__}: {exc}")

    # Route the document
    route = classify_document(doc, ai_client=ai_client)
    print(f"  +- ROUTER [{doc.filename}]: {route.doc_type}  conf={route.confidence:.2f}  "
          f"via={route.method}  ({route.evidence[:80]})")

    if route.doc_type != DOC_TYPE_UNKNOWN:
        fn = _EXTRACTOR_MAP.get(route.doc_type)
        if fn is not None:
            try:
                facts.extend(_run_extractor(fn, doc, ai_client=ai_client))
            except Exception as exc:
                print(f"  [WARN] {fn.__name__} raised {type(exc).__name__}: {exc}")
    else:
        # Unknown — run all specific extractors as fallback
        print(f"  [ROUTER] Unknown doc type — running all extractors as fallback")
        for fn in _ALL_SPECIFIC_EXTRACTORS:
            try:
                facts.extend(_run_extractor(fn, doc, ai_client=ai_client))
            except Exception as exc:
                print(f"  [WARN] {fn.__name__} raised {type(exc).__name__}: {exc}")

    return facts


def _format_elapsed(seconds: float) -> str:
    return f"{seconds:.2f}s"


def extract_fact_store(
    doc_paths: list[Path],
    out_dir: Path,
    *,
    ai_client=None,
    copy_rules: list[tuple[str, float]] | None = None,
) -> tuple[FactStoreImpl, int]:
    """Run Brain A + Brain C and write `facts.json`.

    ai_client   — optional AIClient forwarded to council-rates LLM extractor.
    copy_rules  — list of (authority_name, annual_amount) tuples from the local
                  copy-rules DB.  When provided, the best fuzzy match for the
                  extracted water authority name is injected at conf=0.99,
                  overriding any document-calculated amount.
    """
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

    # --- Copy-rules DB override for water authority ---
    # Inject AFTER all document extractors so the high-confidence DB fact wins.
    if copy_rules:
        try:
            from triconvey_agent.copy_rules import find_best_copy_rule_match
            from triconvey_agent.canonical.schemas import Fact
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
                                "Overrides any document-calculated amount."
                            ),
                            sources=[],
                        )
                    )
                    print(
                        f"  [Copy Rule] Water authority '{authority_name}' → "
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


# ---------------------------------------------------------------------------
# Rates debug log  — written to  <out_dir>/rates_debug.log
# ---------------------------------------------------------------------------

_RATES_DEBUG_PATHS = [
    ("rates.council.authority_name",           "Council Authority"),
    ("rates.council.annual_amount",            "Council Amount"),
    ("rates.water.authority_name",             "Water Authority"),
    ("rates.water.annual_amount",              "Water Amount"),
    ("rates.land_tax.authority_name",          "Land Tax Authority"),
    ("rates.land_tax.amount",                  "Land Tax Amount"),
    ("rates.owners_corporation.authority_name","OC Authority"),
    ("rates.owners_corporation.annual_amount", "OC Amount"),
]


def _write_rates_debug(store, out_dir: Path) -> None:
    """Write authority + amount fact resolution table to rates_debug.log."""
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

        final_val  = winner.value if winner else "NEEDS REVIEW (no clear winner)"
        final_conf = winner.confidence if winner else 0.0
        final_src  = winner.extractor if winner else "--"
        resolution = conflict.resolution if conflict else "single fact"

        lines.append(f"  FINAL  : {final_val}")
        lines.append(f"  CONF   : {final_conf:.2f}")
        lines.append(f"  SOURCE : {final_src}")
        lines.append(f"  HOW    : {resolution}")
        lines.append("")

        if len(all_facts) == 1:
            f = all_facts[0]
            src_file = f.sources[0].file if f.sources else "unknown"
            lines.append(f"  ONLY CANDIDATE:")
            lines.append(f"    value     = {f.value!r}")
            lines.append(f"    conf      = {f.confidence:.2f}")
            lines.append(f"    extractor = {f.extractor}")
            lines.append(f"    file      = {src_file}")
        else:
            lines.append(f"  ALL CANDIDATES ({len(all_facts)} facts, highest conf first):")
            for f in sorted(all_facts, key=lambda x: -x.confidence):
                is_win = (winner and f.extractor == winner.extractor
                          and str(f.value) == str(winner.value))
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

    # Always print to stdout so it shows in the terminal regardless of
    # log-capture configuration.
    print(output)

    # Also write to file so it's readable even when stdout is captured/redirected.
    log_path = out_dir / "rates_debug.log"
    try:
        log_path.write_text(output, encoding="utf-8")
        print(f"  [Rates Debug] Also written to {log_path}")
    except Exception as exc:
        print(f"  [WARN] Could not write rates_debug.log: {exc}")
