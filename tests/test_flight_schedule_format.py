"""Airline-style formatting of a grounded flight's schedule.

Server-side because the next-day case is the one that matters: a flight leaving
17:15 and arriving 01:06 is not a nineteen-hour mistake, and a renderer that
silently drops the day change says it is.
"""

from flaskapp.travel_ai.schemas import OptionSchedule
from flaskapp.travel_ai.sections import format_schedule


def schedule(depart, arrive, reference="JL6006", dest="HND", stops=0):
    return OptionSchedule(
        reference=reference, depart=depart, arrive=arrive, dest_code=dest, stops=stops
    )


def test_the_date_reads_as_a_traveller_writes_it():
    out = format_schedule(schedule("2026-10-02T09:45+08:00", "2026-10-02T17:31+09:00"))
    assert out["date"] == "02 Oct 2026"


def test_times_are_24_hour():
    out = format_schedule(schedule("2026-10-02T09:45+08:00", "2026-10-02T17:31+09:00"))
    assert out["depart"] == "09:45"
    assert out["arrive"] == "17:31"


def test_a_same_day_flight_has_no_day_marker():
    out = format_schedule(schedule("2026-10-02T09:45+08:00", "2026-10-02T17:31+09:00"))
    assert out["day_offset"] == ""


def test_an_overnight_arrival_is_marked():
    """The real case: departs 10 Oct 17:15 +08:00, lands 11 Oct 01:06 +09:00."""
    out = format_schedule(schedule("2026-10-10T17:15+08:00", "2026-10-11T01:06+09:00"))
    assert out["day_offset"] == "+1"
    assert out["depart"] == "17:15"
    assert out["arrive"] == "01:06"


def test_a_two_day_itinerary_counts_the_days():
    out = format_schedule(schedule("2026-10-10T23:50+08:00", "2026-10-12T06:20+01:00"))
    assert out["day_offset"] == "+2"


def test_local_wall_clock_is_never_converted():
    """The offsets differ because these are clocks in two different places.
    Converting to one zone would show a time no boarding pass agrees with."""
    out = format_schedule(schedule("2026-10-02T23:30+08:00", "2026-10-03T05:10+01:00"))
    assert out["depart"] == "23:30"
    assert out["arrive"] == "05:10"


def test_the_reference_is_carried_through():
    out = format_schedule(schedule("2026-10-02T09:45+08:00", "2026-10-02T17:31+09:00"))
    assert out["reference"] == "JL6006"


def test_an_unparseable_value_does_not_raise():
    """A malformed timestamp must degrade the card, never fail the plan."""
    assert format_schedule(schedule("not-a-timestamp", "also-not")) == {}


# --- the builder fills it -----------------------------------------------------


def test_the_grounded_flight_builder_fills_the_schedule():
    """The values were already being written into a description sentence."""
    from flaskapp.travel_ai.agents.flight_agent.agent import _candidate_to_option
    from flaskapp.travel_ai.agents.flight_agent.schemas import FlightCandidate

    candidate = FlightCandidate(
        flight_id="JL6006-20261010", direction="OUTBOUND",
        dep_ts="2026-10-10T17:15+08:00", arr_ts="2026-10-11T01:06+09:00",
        dest_airport="HND", stops=0, price=900.0, seats=4,
        wheelchair_assist_available=None, step_free_boarding=None,
    )
    option = _candidate_to_option(candidate, "SGD", "seed inventory")
    assert option.schedule is not None
    assert option.schedule.reference == "JL6006-20261010"
    assert option.schedule.dest_code == "HND"
    assert format_schedule(option.schedule)["day_offset"] == "+1"


def test_an_option_without_a_schedule_still_validates():
    """The fallback path has no flight number and no timestamps to give."""
    from flaskapp.travel_ai.schemas import Option

    assert Option(name="Turkish via Istanbul", description="d").schedule is None


def test_the_builder_sets_the_airport_and_direction():
    """Without these the recommendation cannot tell an outbound from a return,
    nor pair a flight with the transfer serving its airport — it reported "no
    priced outbound flight" on a run that had six."""
    from flaskapp.travel_ai.agents.flight_agent.agent import _candidate_to_option
    from flaskapp.travel_ai.agents.flight_agent.schemas import FlightCandidate

    candidate = FlightCandidate(
        flight_id="JL6006-20261010", direction="OUTBOUND",
        dep_ts="2026-10-10T17:15+08:00", arr_ts="2026-10-11T01:06+09:00",
        dest_airport="HND", stops=0, price=900.0, seats=4,
        wheelchair_assist_available=None, step_free_boarding=None,
    )
    option = _candidate_to_option(candidate, "SGD", "seed inventory")
    assert option.airport == "HND"
    assert option.schedule.direction == "OUTBOUND"


def test_the_reference_is_the_flight_number_without_the_date():
    """`flight_id` embeds the departure date — TR4011-20261015 — and the card
    already shows "15 Oct 2026" on its own line."""
    out = format_schedule(schedule(
        "2026-10-15T23:30+09:00", "2026-10-16T07:04+08:00", reference="TR4011-20261015",
    ))
    assert out["reference"] == "TR4011"
    assert out["date"] == "15 Oct 2026"


def test_a_reference_without_a_date_suffix_is_left_alone():
    out = format_schedule(schedule(
        "2026-10-15T23:30+09:00", "2026-10-16T07:04+08:00", reference="SQ322",
    ))
    assert out["reference"] == "SQ322"


def test_a_grounded_flight_description_carries_no_raw_timestamps():
    """The card shows the schedule; repeating it as ISO prose underneath is the
    thing the card was built to replace."""
    from flaskapp.travel_ai.agents.flight_agent.agent import _candidate_to_option
    from flaskapp.travel_ai.agents.flight_agent.schemas import FlightCandidate

    candidate = FlightCandidate(
        flight_id="TR4011-20261015", direction="RETURN",
        dep_ts="2026-10-15T23:30+09:00", arr_ts="2026-10-16T07:04+08:00",
        dest_airport="SIN", stops=1, price=127.0, seats=4,
        wheelchair_assist_available=None, step_free_boarding=None,
    )
    description = _candidate_to_option(candidate, "SGD", "seed inventory").description
    assert "2026-10-15T23:30" not in description
    assert "T23:30+09:00" not in description
    assert "SIN" in description, "the destination is still worth saying"
    assert "1 stop" in description
