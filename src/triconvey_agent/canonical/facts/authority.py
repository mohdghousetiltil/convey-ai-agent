"""Authority resolution for conflicting Facts.

When the FactStore holds two or more Facts at the same path, the resolver
picks the winner using a per-path-pattern AuthorityRule. The rule lists
extractors in priority order; the first matching extractor wins.

Design goals
------------
1. Per-path-pattern rules — rates.* may have a different authority than
   planning.*, even if both have a vendor_form fact present.
2. Glob matching on both paths and extractor names — keeps the rule table
   small and lets new extractor versions slot in without rule edits.
3. Predictable fallbacks — if no rule applies or no top extractor is
   present, fall back to highest_confidence; if confidences are tied,
   escalate to review.
4. Never silently choose — every winner is paired with a reason string,
   and every conflict is recorded for the audit trail.
"""
from __future__ import annotations

from fnmatch import fnmatch

from triconvey_agent.canonical.schemas import AuthorityRule, Conflict, Fact

# ---------------------------------------------------------------------------
# Default rules — per-path-pattern authority ranking
# ---------------------------------------------------------------------------
#
# These match the legal hierarchy in Victorian conveyancing:
#  - Title register is authoritative for everything on the title
#  - Council/water authorities are authoritative for their own charges
#  - Planning certificates are authoritative for zone/overlay/bushfire
#  - Permits are authoritative for permits, not the vendor's recollection
#  - Vendor form is the fallback (vendor's word) for everything else
#
# Extractor name pattern: "<source>:<doc_type>[*]" — fnmatch glob.
# `source` is one of: rule, ai
# `doc_type` matches the document type the extractor was reading.

DEFAULT_AUTHORITY_RULES: list[AuthorityRule] = [
    # ---- Title ----
    AuthorityRule(
        path_pattern="title.*",
        authoritative_extractors=[
            "rule:vic_title*",
            "ai:doc_extractor:vic_title*",
            "rule:vendor_form*",
        ],
        on_unresolved="review",
        note="Title register outranks vendor disclosures for everything on title.",
    ),
    # ---- Rates: council, water, land tax ----
    AuthorityRule(
        path_pattern="rates.council.*",
        authoritative_extractors=[
            "rule:council_rates_certificate*",
            "ai:doc_extractor:council_rates_certificate*",
            "rule:property_report*",
            "rule:vendor_form*",
        ],
        on_unresolved="review",
    ),
    # Authority NAME — the certificate has the official full name; vendor form
    # may truncate it (e.g. "Yarra Valley" vs "Yarra Valley Water").
    AuthorityRule(
        path_pattern="rates.water.authority_name",
        authoritative_extractors=[
            "rule:water_authority_certificate*",
            "ai:doc_extractor:water_authority_certificate*",
            "rule:vendor_form*",
        ],
        on_unresolved="highest_confidence",
        note="Water cert has the official authority name; vendor form may truncate it.",
    ),
    AuthorityRule(
        path_pattern="rates.water.*",
        authoritative_extractors=[
            # Vendor form is authoritative for annual amounts (it's the vendor's
            # stated annual outgoing). The water cert is quarterly and must be
            # annualised -- treat it as a fallback / verification source only.
            "rule:vendor_form*",
            "ai:doc_extractor:vendor_form*",
            "rule:water_authority_certificate*",
            "ai:doc_extractor:water_authority_certificate*",
            "rule:property_report*",
        ],
        on_unresolved="review",
        note="Vendor form annual amount wins; water cert quarterly x4 is a fallback/verification.",
    ),
    AuthorityRule(
        path_pattern="rates.land_tax.*",
        authoritative_extractors=[
            "rule:land_tax_certificate*",
            "ai:doc_extractor:land_tax_certificate*",
            "rule:vendor_form*",
        ],
        on_unresolved="review",
    ),
    # ---- Services ----
    AuthorityRule(
        path_pattern="services.water.*",
        authoritative_extractors=[
            "rule:water_authority_certificate*",
            "rule:vendor_form*",
        ],
        on_unresolved="highest_confidence",
    ),
    AuthorityRule(
        path_pattern="services.sewerage.*",
        authoritative_extractors=[
            "rule:water_authority_certificate*",
            "rule:vendor_form*",
        ],
        on_unresolved="highest_confidence",
    ),
    AuthorityRule(
        path_pattern="services.*",
        authoritative_extractors=[
            "rule:vendor_form*",
            "ai:doc_extractor:vendor_form*",
            "ai:summarizer*",
        ],
        on_unresolved="highest_confidence",
        note="Vendor knows day-to-day connection state; only sewerage/water defer to authority certs.",
    ),
    # ---- Planning ----
    AuthorityRule(
        path_pattern="planning.*",
        authoritative_extractors=[
            "rule:planning_certificate*",
            "ai:doc_extractor:planning_certificate*",
            "rule:council_certificate*",
            "rule:property_report*",
            "rule:vendor_form*",
        ],
        on_unresolved="review",
        note="Planning/council certificates outrank vendor's recollection of zone/overlay.",
    ),
    # ---- Building permits ----
    AuthorityRule(
        path_pattern="building.*",
        authoritative_extractors=[
            "rule:building_permit*",
            "rule:occupancy_permit*",
            "rule:council_building_approval*",
            "ai:doc_extractor:building_permit*",
            "rule:vendor_form*",
        ],
        on_unresolved="review",
    ),
    # ---- Insurance ----
    AuthorityRule(
        path_pattern="insurance.*",
        authoritative_extractors=[
            "rule:insurance_certificate*",
            "ai:doc_extractor:insurance_certificate*",
            "rule:vendor_form*",
        ],
        on_unresolved="review",
    ),
    # ---- Property identifiers (overlap with vendor form) ----
    AuthorityRule(
        path_pattern="property.*",
        authoritative_extractors=[
            "rule:vic_title*",
            "ai:doc_extractor:vic_title*",
            "rule:property_report*",
            "rule:vendor_form*",
        ],
        on_unresolved="highest_confidence",
        note="Title register is authoritative for lot/plan/volume/folio; vendor form is fallback.",
    ),
    # ---- Vendor identity / contact ----
    AuthorityRule(
        path_pattern="vendor.*",
        authoritative_extractors=[
            "rule:vendor_form*",
            "ai:doc_extractor:vendor_form*",
        ],
        on_unresolved="highest_confidence",
    ),
    # ---- Catch-all ----
    AuthorityRule(
        path_pattern="*",
        authoritative_extractors=[],
        on_unresolved="highest_confidence",
        note="Default: pick highest-confidence fact, escalate if too close to call.",
    ),
]


# Confidence margin below which two leading facts are considered "tied".
TIE_BREAK_MARGIN = 0.05


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _matching_rule(path: str, rules: list[AuthorityRule]) -> AuthorityRule | None:
    """Return the first AuthorityRule whose path_pattern matches `path`.

    Order matters — more specific patterns must come before catch-alls in
    the rules list.
    """
    for rule in rules:
        if fnmatch(path, rule.path_pattern):
            return rule
    return None


def _extractor_rank(extractor: str, ranking: list[str]) -> int:
    """Return index of the first ranking entry that matches `extractor`.

    Lower index = higher authority. Returns len(ranking) (worst) if no
    pattern matches.
    """
    for i, pattern in enumerate(ranking):
        if fnmatch(extractor, pattern):
            return i
    return len(ranking)


# ---------------------------------------------------------------------------
# Public resolver
# ---------------------------------------------------------------------------


def resolve_facts(
    path: str,
    facts: list[Fact],
    rules: list[AuthorityRule] | None = None,
) -> tuple[Fact | None, Conflict | None]:
    """Pick a winning Fact from a list of candidates at the same path.

    Returns
    -------
    (winner, conflict)
        winner   : the chosen Fact, or None if nothing was resolvable
        conflict : a Conflict record if there were multiple candidates,
                   else None. Always populated when len(facts) > 1, even
                   on a clean authority win, so the audit trail can show
                   what was considered.
    """
    if not facts:
        return None, None

    # Single fact — no conflict, no resolver needed
    if len(facts) == 1:
        return facts[0], None

    rules = rules or DEFAULT_AUTHORITY_RULES
    rule = _matching_rule(path, rules)

    # Always build a conflict record when there are 2+ facts
    conflict = Conflict(path=path, facts=list(facts))

    # ---- Authority-based resolution ----
    if rule and rule.authoritative_extractors:
        ranked = sorted(
            facts,
            key=lambda f: (
                _extractor_rank(f.extractor, rule.authoritative_extractors),
                -f.confidence,  # within same authority rank, prefer higher confidence
                -f.extracted_at.timestamp(),  # then prefer most recent
            ),
        )
        top_rank = _extractor_rank(ranked[0].extractor, rule.authoritative_extractors)

        # Did the top fact actually match an authoritative extractor?
        if top_rank < len(rule.authoritative_extractors):
            # Are there other facts at the same top rank? (tie within top tier)
            tied = [
                f for f in facts
                if _extractor_rank(f.extractor, rule.authoritative_extractors) == top_rank
            ]
            if len(tied) == 1:
                conflict.resolution = "authority_wins"
                conflict.winning_fact = tied[0]
                conflict.reason = (
                    f"extractor '{tied[0].extractor}' matched authoritative "
                    f"pattern '{rule.authoritative_extractors[top_rank]}' for path '{path}'"
                )
                return tied[0], conflict
            distinct_values = {repr(f.value) for f in tied}
            if len(distinct_values) == 1:
                winner = sorted(
                    tied,
                    key=lambda f: (-f.confidence, -f.extracted_at.timestamp()),
                )[0]
                conflict.resolution = "authority_wins"
                conflict.winning_fact = winner
                conflict.reason = (
                    f"multiple authoritative facts agreed on the same value for path '{path}'"
                )
                return winner, conflict
            # Tie at the top — fall through to confidence/escalation logic below
            facts = tied

    # ---- Fallback: confidence-based ----
    on_unresolved = rule.on_unresolved if rule else "highest_confidence"

    sorted_by_conf = sorted(facts, key=lambda f: -f.confidence)
    top, *rest = sorted_by_conf

    if on_unresolved == "highest_confidence":
        if rest and (top.confidence - rest[0].confidence) < TIE_BREAK_MARGIN:
            # Too close to call → escalate
            conflict.resolution = "review_required"
            conflict.reason = (
                f"top two facts within {TIE_BREAK_MARGIN} confidence margin "
                f"({top.confidence:.2f} vs {rest[0].confidence:.2f}) — escalating to review"
            )
            return None, conflict
        conflict.resolution = "authority_wins"
        conflict.winning_fact = top
        conflict.reason = f"highest-confidence fallback ({top.confidence:.2f})"
        return top, conflict

    if on_unresolved == "first_seen":
        winner = sorted(facts, key=lambda f: f.extracted_at)[0]
        conflict.resolution = "authority_wins"
        conflict.winning_fact = winner
        conflict.reason = "first_seen fallback"
        return winner, conflict

    # on_unresolved == "review"
    conflict.resolution = "review_required"
    conflict.reason = (
        f"no authoritative extractor matched and rule mandates human review "
        f"for path pattern '{rule.path_pattern}'" if rule else "no rule matched, escalating"
    )
    return None, conflict
