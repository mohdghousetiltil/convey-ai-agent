"""Brain C — Policy Overlay.

Injects two kinds of facts into the FactStore after Brain A runs:

  defaults.py   — always-fixed items (always True / always same text)
                  e.g. "Is in the attached copies of title documents" = True
  computed.py   — derived/calculated facts
                  e.g. annualisation verification, attachment list text

Public API
----------
    run_policy_pass(store)
        Runs all policy injections in the correct order.
"""
from triconvey_agent.canonical.policy.defaults import inject_default_policy_facts
from triconvey_agent.canonical.policy.computed import inject_computed_facts

__all__ = ["inject_default_policy_facts", "inject_computed_facts", "run_policy_pass"]


def run_policy_pass(store) -> None:  # type: ignore[type-arg]
    """Inject all policy facts into the FactStore.

    Call after all Brain A extractors have run and before Brain B.
    """
    inject_default_policy_facts(store)
    inject_computed_facts(store)
