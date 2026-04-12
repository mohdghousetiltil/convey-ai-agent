## Phase plan
Phase 0 — Scope and extraction contract

Define exactly what the system must return, before writing extraction logic.

Deliverables:

JSON schema
source priority rules
conflict rules
skip rules
confidence rules

Key rules from your documents:

Vendor form is the main source for personal/vendor answers, trust, rates, services, material facts, planning/building answers.
Sec 32 YAML tabs define the target question structure and checkbox/edit fields.
VIC title PDFs are the authoritative source for volume/folio, security no, produced date, land description, encumbrances.
Planning/property reports are the main source for bushfire, overlays, utilities context, parcel details.
## Phase 1 — Ingestion layer

Read:

PDFs
YAML tab files
optional OCR fallback later only if needed

Deliverables:

document loader
parsed document model
source metadata attached to every extracted value
## Phase 2 — Question catalog

Build a normalized question inventory from the Sec 32 YAML tabs and vendor form.

Deliverables:

canonical field registry
synonym mapping
question classification:
required_if_found
optional
derived
skip
unanswered_allowed

Examples:

“property”, “home”, “land”, “premises” should normalize to related concepts, not exact text-only matching.
“mobile”, “contact number”, “phone number” should normalize to contact_phone.
## Phase 3 — Deterministic extraction

Start with high-confidence extraction before using AI.

Deliverables:

regex/rule extractors
checkbox logic
structured table extraction
title-search extractor
vendor-form extractor

This phase should extract:

vendor identity
trust info
property details
yes/no answers
VIC title fields
rates/taxes/charges
services connected
planning/building fields
## Phase 4 — AI-assisted semantic extraction

Use AI only where deterministic extraction is weak:

synonyms
free-text answers
question-to-evidence matching
conflict explanation
fill likely canonical fields from messy wording

Important: AI should not directly “invent” answers. It should map evidence to fields and return confidence plus citations.

## Phase 5 — Conflict resolution and confidence scoring

This is critical because your sources already show mismatches.

Example:

vendor form says council is Indigo and council rates are $3,621.97.
Sec 32 tab 1 contains Ballarat City Council and other amounts.

Deliverables:

conflict detector
source precedence rules
manual review queue
confidence thresholds
## Phase 6 — Final JSON output

Return clean, auditable JSON.

Deliverables:

final merged JSON
evidence trail
unanswered fields
conflicts block
optionally a ready-to-fill Sec 32 answer map
## Phase 7 — Validation and test suite

Build regression tests using real files like the ones you uploaded.

Deliverables:

unit tests for field extractors
golden JSON fixtures
conflict tests
synonym tests
missing-answer tests