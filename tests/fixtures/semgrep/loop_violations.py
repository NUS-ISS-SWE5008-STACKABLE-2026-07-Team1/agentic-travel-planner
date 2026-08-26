"""Deliberate violations of the tool-loop semgrep rules.

Fixture data for `tests/test_semgrep_rules.py`, never imported or executed.
Every function here must be reported by `.semgrep/llm-agent.yml`; if one
stops being reported, the rule guarding it has gone inert.
"""

def unbounded_loop(llm, specs):
    bound = llm.bind_tools(specs)
    return bound.invoke([])


def row_from_model(response):
    return FlightCandidate(
        flight_id=response.flight_id, direction="OUTBOUND",
    )


def row_from_model_subscript(result):
    return FlightInventoryItem(flight_id=result["flight_id"], carrier="SQ")
