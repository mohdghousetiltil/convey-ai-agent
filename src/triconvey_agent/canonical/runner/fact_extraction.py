from __future__ import annotations

import json
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
from triconvey_agent.ingest.pdf_loader import load_pdf_document

EXTRACTORS = [
    extract_generic_doc_meta_facts,
    extract_building_approval_facts,
    extract_vendor_form_facts,
    extract_vic_title_facts,
    extract_water_authority_certificate_facts,
    extract_land_tax_certificate_facts,
    extract_planning_certificate_facts,
    extract_owners_corporation_facts,
    extract_council_rates_certificate_facts,
]

SAMPLES_DIR = Path(__file__).resolve().parents[4] / "samples"


def default_docs() -> list[Path]:
    """Return every PDF currently present in the samples directory."""
    return sorted(
        path for path in SAMPLES_DIR.rglob("*")
        if path.is_file() and path.suffix.lower() == ".pdf"
    )


def run_all_extractors(doc) -> list:
    facts = []
    for fn in EXTRACTORS:
        try:
            emitted = fn(doc)
            facts.extend(emitted)
        except Exception as exc:
            print(f"  [WARN] {fn.__name__} raised {type(exc).__name__}: {exc}")
    return facts


def _format_elapsed(seconds: float) -> str:
    return f"{seconds:.2f}s"


def extract_fact_store(doc_paths: list[Path], out_dir: Path) -> tuple[FactStoreImpl, int]:
    """Run Brain A + Brain C and write `facts.json`."""
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
            doc = load_pdf_document(path)
            prime_cached_pdf_analysis(path, doc)
            facts = run_all_extractors(doc)
            store.add_many(facts)
            total_facts += len(facts)
            print(f"{len(facts)} fact(s) [{_format_elapsed(time.perf_counter() - doc_started)}]")
        except Exception as exc:
            print(f"ERROR - {type(exc).__name__}: {exc}")

    print(f"  Total facts in store: {total_facts}")
    print(f"  [Time] Brain A total: {_format_elapsed(time.perf_counter() - brain_a_started)}")

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
    return store, total_facts
