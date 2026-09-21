"""One combination of flights, a hotel and a transfer that fits the budget.

A traveller states a total and, until now, nothing spent it: the plan listed
flights and hotels in price tiers without ever adding them up.

Pure — a request and findings in, a recommendation out. No model, no IO, so the
arithmetic a traveller will act on is testable without a graph.

The search is EXHAUSTIVE rather than greedy. A finding holds at most a handful
of options, so enumerating is affordable, and a cheapest-first pick misses the
better answer: a dearer flight paired with a much cheaper hotel can land closer
to budget than the cheapest of each.
"""

from __future__ import annotations

from itertools import product

from flaskapp.travel_ai.schemas import (
    AgentFinding, Option, PlanRecommendation, TravelRequest,
)


def _spendable(options: list[Option], currency: str, category: str) -> list[Option]:
    """Priced options of one category, in the request's own currency.

    Currency is a hard gate rather than a conversion: summing 100 USD with
    500 SGD produces a number that looks like an answer and is not one.
    """
    return [
        option for option in options
        if option.category == category
        and option.estimated_cost is not None
        and option.estimated_cost > 0
        and (option.currency or currency) == currency
    ]


def _leg(flights: list[Option], direction: str) -> list[Option]:
    return [
        flight for flight in flights
        if flight.schedule is not None and flight.schedule.direction == direction
    ]


def recommend_package(
    request: TravelRequest, findings: list[AgentFinding]
) -> PlanRecommendation:
    """The highest-costing combination that does not exceed the budget."""
    currency = request.currency
    options = [option for finding in findings for option in finding.options]
    flights = _spendable(options, currency, "flight")
    hotels = _spendable(options, currency, "hotel")
    transfers = _spendable(options, currency, "transport")

    outbound = _leg(flights, "OUTBOUND")
    inbound = _leg(flights, "RETURN")

    missing = [
        name for name, group in
        (("outbound flight", outbound), ("return flight", inbound), ("hotel", hotels))
        if not group
    ]
    if missing:
        # A package without a return flight is not a trip, so a partial answer
        # would be worse than none.
        return PlanRecommendation(
            currency=currency,
            note=f"No recommendation: no priced {', '.join(missing)} was found.",
        )

    # None is a real choice: a city may have no seeded transfer, and a package
    # of two flights and a hotel is still a package.
    transfer_choices: list[Option | None] = [None, *transfers]

    best: tuple[float, list[Option]] | None = None
    cheapest = None
    for out, back, stay, ride in product(outbound, inbound, hotels, transfer_choices):
        # Otherwise the plan says fly to Haneda and take the Narita Express.
        if ride is not None and out.airport and ride.airport and ride.airport != out.airport:
            continue
        chosen = [out, back, stay, *([ride] if ride else [])]
        total = sum(item.estimated_cost for item in chosen)
        if cheapest is None or total < cheapest:
            cheapest = total
        if total <= request.budget and (best is None or total > best[0]):
            best = (total, chosen)

    if best is None:
        shortfall = (cheapest or 0) - request.budget
        return PlanRecommendation(
            currency=currency,
            note=(
                f"No combination fits {request.budget:g} {currency}. The cheapest "
                f"flights, hotel and transfer found come to {cheapest:g} {currency} "
                f"— {shortfall:g} {currency} over."
            ),
        )

    total, chosen = best
    return PlanRecommendation(
        items=chosen,
        total=total,
        currency=currency,
        remaining=round(request.budget - total, 2),
        note=_transfer_note(chosen, transfers, chosen[0].airport),
    )


def _transfer_note(chosen: list[Option], transfers: list[Option], airport: str) -> str:
    """Why the recommendation has no airport transfer, when it has none.

    Three different reasons, and saying the wrong one misleads. Observed live: a
    35 SGD transfer existed and fitted the airport, but adding it took the total
    past the budget — and the note claimed none was available.
    """
    if any(item.category == "transport" for item in chosen):
        return ""
    if not transfers:
        return "No verified airport transfer was available for this destination."
    if airport and not any(t.airport == airport for t in transfers if t.airport):
        return (
            f"No verified airport transfer was available for {airport}, "
            "the airport this flight arrives at."
        )
    return (
        "An airport transfer was available but did not fit the budget alongside "
        "these flights and hotel."
    )
