"""Live demo: does the LLM's acknowledgment choice actually differ by traveller context?

This is the real proof update.md's multi-gap follow-up item calls for — every test in
tests/test_flight_multi_gap_context.py mocks the LLM's response (we author what "the
LLM" says), which can only prove the mechanism handles either valid choice correctly.
It cannot prove the LLM itself reasons about context. This script runs the identical
dual-gap scenario (see that file for the fixture — imported from there, not duplicated)
through a REAL model twice, once per traveller context, and prints both outputs side by
side so the difference (or lack of one) is visible directly, not just described.

Needs a provider configured in .env/.env.secrets (see .env.example). Not runnable in this environment (no
network/key assumed) — this is scaffolding, ready to run the moment a key exists.

Usage:
    python scripts/demo_multi_gap_acknowledgment.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts._llm import build_demo_llm, use_utf8_stdout
from flaskapp.travel_ai.agents.flight_agent.reasoning import run_flight_agent
from tests.test_flight_multi_gap_context import (
    DUAL_GAP_INVENTORY,
    family_request,
    solo_business_request,
)


def _print_acknowledgment_status(response) -> None:
    """Unambiguous status line, independent of which of the two possible
    result shapes `response` is in:

    - A acknowledgment was applied: `acknowledgment_applied` is what call #1 proposed
      and code actually acted on. `proposed_acknowledgment` at this point is a
      DIFFERENT field — call #2's own fresh suggestion on the post-acknowledgment
      result, which was NEVER applied (bounded to one acknowledgment per run).
      Printing both under the same unqualified label reads as a mismatch;
      it isn't one — see update.md 2026-07-20 for the full trace that found
      this the first time this script actually ran.
    - No acknowledgment was applied: `proposed_acknowledgment` (if present) is
      whatever call #1 proposed that code rejected as not matching a real
      gap, or there was nothing to propose in the first place.
    """
    applied = response.acknowledgment_applied
    proposed = response.proposed_acknowledgment
    if applied:
        print(f"acknowledgment_applied: {applied.field} ({applied.reason})")
        if proposed:
            print(
                f"further gap noted next round (NOT applied - bounded to one "
                f"acknowledgment per run): {proposed.field} ({proposed.reason})"
            )
    elif proposed:
        print(
            f"proposed_acknowledgment (NOT applied - code found no matching gap, "
            f"ignored): {proposed.field} ({proposed.reason})"
        )
    else:
        print("acknowledgment: none proposed or applied")


def _run(label: str, request, llm):
    proposal, response = run_flight_agent(request, DUAL_GAP_INVENTORY, llm)
    print(f"\n{'=' * 60}\n{label}\n{'=' * 60}")
    print(f"party: {request.trip_context.party}")
    print(f"rationale: {response.rationale}")
    _print_acknowledgment_status(response)
    print(f"escalate: {response.escalate}")
    return response


def main() -> None:
    use_utf8_stdout()
    llm, reason = build_demo_llm()
    if llm is None:
        print(f"No usable model configured ({reason}) - this script needs a real "
              "model to run. It's ready; configure a provider in .env/.env.secrets "
              "and re-run.")
        return

    family = _run("FAMILY (2 adults, 2 children)", family_request(), llm)
    solo = _run("SOLO BUSINESS TRAVELLER (1 adult)", solo_business_request(), llm)

    print(f"\n{'=' * 60}\nCOMPARISON\n{'=' * 60}")
    same_choice = (
        family.acknowledgment_applied
        and solo.acknowledgment_applied
        and family.acknowledgment_applied.field == solo.acknowledgment_applied.field
    )
    if same_choice:
        print(
            "Same acknowledgment chosen for both contexts - either the model isn't "
            "weighing party context here, or both are legitimately equally valid for "
            "this scenario. Worth a closer look / a sharper fixture, not proof either way."
        )
    else:
        print(
            "DIFFERENT acknowledgment chosen per context - direct evidence the LLM's "
            "choice is context-sensitive, not a fixed rule wearing a prompt."
        )


if __name__ == "__main__":
    main()
