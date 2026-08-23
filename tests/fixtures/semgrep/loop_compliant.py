"""The bounded shapes that must NOT be reported.

Fixture data for `tests/test_semgrep_rules.py`, never imported or executed.
A rule that fires on these is worse than no rule: the file header promises
every rule reports zero findings on correct code, and a noisy gate teaches
people to ignore red.
"""

def bounded_by_param(llm, specs, budget: LoopBudget):
    return llm.bind_tools(specs)


def bounded_by_context(llm, specs, ctx: ToolContext):
    bound = llm.bind_tools(specs)

    def plan(state):
        if not ctx.budget.spend_llm_turn():
            return {}
        return {"messages": [bound.invoke(state)]}

    return plan


def row_from_provider(item):
    return FlightCandidate(flight_id=item.flight_id, direction="OUTBOUND")
