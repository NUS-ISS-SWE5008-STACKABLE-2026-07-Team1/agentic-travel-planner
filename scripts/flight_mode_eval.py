"""Measure the two Flight Agent modes against a real model.

Answers the two questions left open by the agentic refactor, both of which need a
live model and neither of which a unit test can settle:

1. **Latency** — `FLIGHT_AGENT_MODE=agentic` is off by default because the flight
   node sits on a fan-out branch joined by a barrier edge, so the whole travel
   plan waits for the slowest specialist. This measures how much slower the loop
   actually is before anyone makes it the default.

2. **Grounding** — the agent currently builds every `Option` in code from
   `proposal.candidates`, so a fabricated flight is structurally impossible rather
   than merely checked for. The alternative would let the model choose which
   flights to feature, validated against `InventoryCache.seen_ids`. This measures
   whether the safe design costs anything: if the model's preferred flights are
   the ones the deterministic ranking already chose, there is no trade to make.

Usage:

    python scripts/flight_mode_eval.py                 # both modes, 2 repeats
    python scripts/flight_mode_eval.py --repeats 3
    python scripts/flight_mode_eval.py --report docs/flight_agent/mode-eval.md

Hits a real model and costs money — it is a script, not a test, and CI never runs
it. `pytest.ini`'s `live` marker exists for the same reason.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from datetime import date
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from flaskapp.config import Config, get_llm_settings  # noqa: E402
from flaskapp.travel_ai.a2a import request_message  # noqa: E402
from flaskapp.travel_ai.agents.flight_agent.agent import NAME, create_node  # noqa: E402
from flaskapp.travel_ai.agents.flight_agent.domain import propose_flights  # noqa: E402
from flaskapp.travel_ai.agents.flight_agent.seed_data import SEED_FLIGHT_INVENTORY  # noqa: E402
from flaskapp.travel_ai.llm import build_llm  # noqa: E402
from flaskapp.travel_ai.schemas import TravelRequest  # noqa: E402
from flaskapp.travel_ai.tracing import AuditTracer  # noqa: E402

KNOWN_IDS = {item.flight_id for item in SEED_FLIGHT_INVENTORY}

BASE = dict(
    origin="Singapore", destination="Japan",
    departure_date=date(2026, 9, 1), return_date=date(2026, 9, 5),
    travellers=1, traveller_ages=[34], traveller_genders=["female"],
    traveller_accessibility_needs=[[]], budget=4000, currency="SGD",
    preferences=[], accessibility_needs=[],
)

# Chosen to cover the shapes that behave differently, not to be exhaustive: a
# plain grounded trip, a trip whose dates fall between stocked dates (the case
# the loop exists to improve), a preference that nothing satisfies, a
# second-hub trip, an accessibility need, and an uncovered route.
SCENARIOS = [
    ("plain", {}),
    ("awkward_dates", {
        "departure_date": date(2026, 9, 12), "return_date": date(2026, 9, 16),
    }),
    ("direct_only", {"preferences": ["direct flights"]}),
    ("wheelchair", {
        "traveller_accessibility_needs": [["wheelchair assistance"]],
        "accessibility_needs": ["Traveler 1: wheelchair assistance"],
    }),
    ("london_hub", {
        "origin": "United Kingdom", "destination": "France",
        "origin_city": "London", "destination_city": "Paris",
        "departure_date": date(2026, 9, 8), "return_date": date(2026, 9, 11),
        "currency": "GBP",
    }),
    ("uncovered_route", {"destination": "Brazil"}),
]


def _state(overrides: dict) -> dict:
    request = TravelRequest(**{**BASE, **overrides})
    correlation_id = uuid4()
    return {
        "request_id": str(correlation_id),
        "request": request.model_dump(mode="json"),
        "findings": [],
        "messages": [request_message(
            correlation_id=correlation_id, sender="orchestrator_agent", recipient=NAME,
            payload_type="TravelRequest", payload=request,
        )],
    }


def _events(tracer: AuditTracer) -> list[dict]:
    return [
        json.loads(line) for line in tracer.path.read_text().splitlines() if line.strip()
    ]


def run_once(llm, mode: str, overrides: dict, trace_dir: Path) -> dict:
    """One scenario through the node in one mode, with everything worth counting."""
    tracer = AuditTracer(trace_dir, f"{mode}-{uuid4().hex[:8]}")
    node = create_node(llm, tracer, config={
        "FLIGHT_AGENT_MODE": mode, "FLIGHT_INVENTORY_SOURCE": "seed",
    })
    state = _state(overrides)

    started = time.monotonic()
    try:
        finding = node(state)["findings"][0]
        failed = None
    except Exception as exc:  # noqa: BLE001 - a failed run is a result, not a crash
        return {
            "mode": mode, "elapsed_ms": int((time.monotonic() - started) * 1000),
            "failed": type(exc).__name__, "options": 0, "cited": [],
        }
    elapsed_ms = int((time.monotonic() - started) * 1000)

    events = _events(tracer)
    by_event = [e["event"] for e in events]
    loop = next(
        (e["details"] for e in events if e["event"] == "agent_loop_completed"), {}
    )
    option_ids = [option.name.split(" ")[0] for option in finding.options]

    return {
        "mode": mode,
        "elapsed_ms": elapsed_ms,
        "failed": failed,
        "options": len(finding.options),
        "option_ids": option_ids,
        "fabricated": [oid for oid in option_ids if oid not in KNOWN_IDS],
        "confidence": finding.confidence,
        "tool_calls": loop.get("tool_calls", 0),
        "llm_turns": loop.get("llm_turns", by_event.count("agent_llm_attempt_failed") + 1),
        "provider_calls": loop.get("provider_calls", 1),
        "searches": loop.get("searches", 1),
        "fell_back": "agent_fallback" in by_event,
        "rejections": by_event.count("agent_tool_rejected"),
        "budget_exhausted": "agent_budget_exhausted" in by_event,
        "warnings": finding.warnings,
    }


def spike_grounding(llm, trace_dir: Path, repeats: int = 1, delay: float = 1.0) -> dict:
    """Does the safe grounding design cost anything?

    Today code picks which flights appear; the model only explains them. The
    alternative would let the model pick, validated against
    `InventoryCache.seen_ids`. That trade is only worth making if the model would
    have chosen differently — and better.

    Three things measured, per the refactor plan:

    1. **Agreement** — how often the model's own `highlighted_flight_ids` are
       already exactly what the deterministic ranking chose. High agreement means
       the safe design costs nothing and the question is settled.
    2. **Ranked-out citations** — ids the model cites that a provider genuinely
       returned but the ranking dropped. These are the cases the widened gate
       exists to permit, and the ones a model-selects design would surface.
    3. **Fabrications** — ids cited that no provider ever returned. A single one
       is an argument against ever letting the model select.

    Runs the loop directly rather than through the node, because
    `highlighted_flight_ids` lives on `FlightAgentResponse` and the node maps it
    away into a shared `AgentFinding`.
    """
    from flaskapp.travel_ai.agents.flight_agent import tools
    from flaskapp.travel_ai.agents.flight_agent.adapter import to_flight_request
    from flaskapp.travel_ai.agents.flight_agent.agentic import run_agentic_flight_agent
    from flaskapp.travel_ai.agents.flight_agent.providers.seed import SeedInventoryProvider
    from flaskapp.travel_ai.agents.loop import LoopBudget

    observations = []
    for name, overrides in SCENARIOS:
        for _ in range(repeats):
            travel_request = TravelRequest(**{**BASE, **overrides})
            adapted = to_flight_request(travel_request)
            ctx = tools.ToolContext.for_request(
                adapted.request, SeedInventoryProvider(), LoopBudget().start()
            )
            tracer = AuditTracer(trace_dir, f"spike-{uuid4().hex[:8]}")
            try:
                proposal, response = run_agentic_flight_agent(ctx, llm, tracer=tracer)
            except Exception as exc:  # noqa: BLE001
                observations.append({"scenario": name, "failed": type(exc).__name__})
                time.sleep(delay)
                continue

            shortlisted = [c.flight_id for c in proposal.candidates]
            cited = list(response.highlighted_flight_ids)
            observations.append({
                "scenario": name,
                "cited": cited,
                "shortlisted": shortlisted,
                "agrees": set(cited) <= set(shortlisted),
                "ranked_out": [
                    fid for fid in cited
                    if fid not in shortlisted and fid in ctx.cache.seen_ids
                ],
                "fabricated": [fid for fid in cited if fid not in ctx.cache.seen_ids],
            })
            time.sleep(delay)

    scored = [o for o in observations if not o.get("failed")]
    with_citations = [o for o in scored if o["cited"]]
    return {
        "runs": len(observations),
        "failed": sum(1 for o in observations if o.get("failed")),
        "runs_citing_flights": len(with_citations),
        "agreed_with_ranking": sum(1 for o in with_citations if o["agrees"]),
        "ranked_out_citations": sum(len(o["ranked_out"]) for o in scored),
        "fabricated_citations": sum(len(o["fabricated"]) for o in scored),
        "detail": observations,
    }


def summarise(runs: list[dict]) -> dict:
    ok = [r for r in runs if not r.get("failed")]
    times = sorted(r["elapsed_ms"] for r in ok)
    if not times:
        return {"runs": len(runs), "failed": len(runs)}
    return {
        "runs": len(runs),
        "failed": len(runs) - len(ok),
        "median_ms": int(statistics.median(times)),
        "mean_ms": int(statistics.fmean(times)),
        "max_ms": times[-1],
        "options_found": sum(r["options"] for r in ok),
        "runs_with_options": sum(1 for r in ok if r["options"]),
        "fabricated": sum(len(r.get("fabricated", [])) for r in ok),
        "tool_calls": sum(r.get("tool_calls", 0) for r in ok),
        "searches": sum(r.get("searches", 0) for r in ok),
        "rejections": sum(r.get("rejections", 0) for r in ok),
    }


def grounding_check(runs: list[dict]) -> dict:
    """Did any run put a flight in `options` that no provider ever returned?

    This is the claim the whole design rests on. It is checked here against a real
    model rather than a stub, because a stub can only fabricate what a test told
    it to.
    """
    fabricated = [
        (r["mode"], oid) for r in runs for oid in r.get("fabricated", [])
    ]
    return {
        "runs_checked": len(runs),
        "options_checked": sum(r.get("options", 0) for r in runs),
        "fabricated_options": fabricated,
        "clean": not fabricated,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repeats", type=int, default=2)
    parser.add_argument("--modes", default="structured,agentic")
    parser.add_argument("--report", type=Path, default=None)
    parser.add_argument("--delay", type=float, default=1.0,
                        help="seconds between calls; the provider rate-limits")
    parser.add_argument("--spike", action="store_true",
                        help="also run the grounding spike")
    args = parser.parse_args()

    settings, error = get_llm_settings(vars(Config))
    if error:
        print(f"LLM not configured: {error}")
        return 1
    llm = build_llm(**settings)
    print(f"model: {settings.get('provider')}/{settings.get('model')}\n")

    trace_dir = ROOT / "instance" / "traces" / "mode-eval"
    trace_dir.mkdir(parents=True, exist_ok=True)
    modes = [m.strip() for m in args.modes.split(",") if m.strip()]

    runs: list[dict] = []
    for name, overrides in SCENARIOS:
        for mode in modes:
            for repeat in range(args.repeats):
                result = run_once(llm, mode, overrides, trace_dir)
                result["scenario"] = name
                runs.append(result)
                time.sleep(args.delay)
                status = result.get("failed") or (
                    f"{result['options']} options, {result['elapsed_ms']}ms"
                )
                print(f"  {name:16} {mode:11} #{repeat + 1}  {status}")

    print()
    summaries = {mode: summarise([r for r in runs if r["mode"] == mode]) for mode in modes}
    for mode, summary in summaries.items():
        print(f"{mode}: {summary}")

    grounding = grounding_check(runs)
    print(f"\ngrounding: {grounding}")

    spike = None
    if args.spike:
        print("running the grounding spike...")
        spike = spike_grounding(llm, trace_dir, repeats=1, delay=args.delay)
        print({k: v for k, v in spike.items() if k != "detail"})

    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(
            _render(settings, runs, summaries, grounding, spike), encoding="utf-8"
        )
        print(f"\nwrote {args.report}")
    return 0


def _render(settings: dict, runs: list[dict], summaries: dict, grounding: dict,
            spike: dict | None = None) -> str:
    lines = [
        "# Flight Agent — mode evaluation",
        "",
        f"Generated by `scripts/flight_mode_eval.py` against "
        f"`{settings.get('provider')}/{settings.get('model')}` on the seed provider.",
        "",
        "## Latency",
        "",
        "| mode | runs | median | mean | slowest | runs with options |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for mode, s in summaries.items():
        lines.append(
            f"| {mode} | {s['runs']} | {s['median_ms']}ms | {s['mean_ms']}ms | "
            f"{s['max_ms']}ms | {s['runs_with_options']}/{s['runs']} |"
        )

    lines += ["", "## Per scenario", "",
              "| scenario | mode | median ms | options | searches |",
              "| --- | --- | --- | --- | --- |"]
    for name, _ in SCENARIOS:
        for mode in summaries:
            subset = [r for r in runs if r["scenario"] == name and r["mode"] == mode
                      and not r.get("failed")]
            if not subset:
                continue
            median = int(statistics.median([r["elapsed_ms"] for r in subset]))
            options = max(r["options"] for r in subset)
            searches = max(r.get("searches", 1) for r in subset)
            lines.append(f"| {name} | {mode} | {median} | {options} | {searches} |")

    lines += [
        "", "## Grounding", "",
        f"- options checked: **{grounding['options_checked']}**",
        f"- fabricated (an option naming a flight no provider returned): "
        f"**{len(grounding['fabricated_options'])}**",
        "",
        "Every option is built by `agent._candidate_to_option` from"
        " `proposal.candidates`, so a fabricated flight is structurally absent"
        " rather than filtered out. This measures that claim against a real model.",
        "",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
