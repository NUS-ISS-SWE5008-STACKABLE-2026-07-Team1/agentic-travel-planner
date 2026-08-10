"""Static reference data for known destination risks.

Curated by hand rather than pulled from a live advisory feed: this is an
NUS-ISS practice-module scope, and a small curated dataset lets every risk
fact this agent states trace back to one auditable entry here instead of an
unverifiable live source. `guardrails.py` enforces that the agent never
states a risk for a country absent from this file, and that reasoning.py's
severity classification matches what is recorded here.

Coverage is intentionally limited to the countries Flight Agent has seed
inventory for (see `agents/flight_agent/airports.py:SEED_BACKED_COUNTRIES`),
so the whole pipeline is exercisable end-to-end. Any other destination
legitimately has no profile here -- that is the truthful "no reference data"
answer, the same pattern Flight Agent uses for unmapped countries.

None of this is a substitute for official advisories. Every profile's
`source` field says so, and `SYSTEM_POLICY` already requires the agent to
tell travellers to verify with official providers before departure.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Literal

AdvisoryLevel = Literal["normal", "increased_caution", "reconsider_travel", "do_not_travel"]
Severity = Literal["low", "medium", "high"]


@dataclass(frozen=True)
class SeasonalRisk:
    """A recurring, month-bound risk window (e.g. typhoon season)."""

    label: str
    start_month: int  # 1-12, inclusive
    end_month: int  # 1-12, inclusive; may be < start_month to wrap across year-end
    severity: Severity
    detail: str

    def covers(self, month: int) -> bool:
        if self.start_month <= self.end_month:
            return self.start_month <= month <= self.end_month
        return month >= self.start_month or month <= self.end_month


@dataclass(frozen=True)
class CountryRiskProfile:
    """Everything this agent is allowed to say about one destination."""

    country: str
    visa_note: str
    safety_advisory_level: AdvisoryLevel
    safety_note: str
    health_note: str
    seasonal_risks: tuple[SeasonalRisk, ...] = field(default_factory=tuple)
    source: str = "Internally curated reference dataset; verify with official authorities before travel."


COUNTRY_RISK_PROFILES: dict[str, CountryRiskProfile] = {
    "Japan": CountryRiskProfile(
        country="Japan",
        visa_note=(
            "Many nationalities receive short-stay visa-free or visa-on-arrival entry; "
            "requirements vary by passport. Verify with Japan's nearest embassy/consulate."
        ),
        safety_advisory_level="normal",
        safety_note="Generally low crime; standard earthquake preparedness applies nationwide.",
        health_note="No routine travel vaccinations required for most visitors.",
        seasonal_risks=(
            SeasonalRisk(
                label="Typhoon season",
                start_month=8,
                end_month=10,
                severity="medium",
                detail="Tropical storms can disrupt flights and rail schedules, peaking Aug-Oct.",
            ),
        ),
    ),
    "United Kingdom": CountryRiskProfile(
        country="United Kingdom",
        visa_note=(
            "Many nationalities receive short-stay visa-free entry (ETA may be required); "
            "requirements vary by passport. Verify with the UK's nearest embassy/consulate."
        ),
        safety_advisory_level="normal",
        safety_note="Generally low risk; standard urban-safety precautions apply in major cities.",
        health_note="No routine travel vaccinations required for most visitors.",
        seasonal_risks=(
            SeasonalRisk(
                label="Winter storm season",
                start_month=12,
                end_month=2,
                severity="low",
                detail="Winter storms occasionally disrupt rail and flight schedules.",
            ),
        ),
    ),
    "Australia": CountryRiskProfile(
        country="Australia",
        visa_note=(
            "Most visitors require an eVisitor or ETA visa arranged before arrival; "
            "requirements vary by passport. Verify with Australia's nearest embassy/consulate."
        ),
        safety_advisory_level="normal",
        safety_note="Generally low crime; sun/heat exposure and marine safety warrant normal caution.",
        health_note="No routine travel vaccinations required for most visitors.",
        seasonal_risks=(
            SeasonalRisk(
                label="Bushfire season",
                start_month=11,
                end_month=3,
                severity="medium",
                detail="Bushfires can affect regional travel and air quality, peaking Nov-Mar.",
            ),
            SeasonalRisk(
                label="Northern cyclone season",
                start_month=11,
                end_month=4,
                severity="medium",
                detail="Tropical cyclones can affect northern Australia (e.g. Queensland, NT) Nov-Apr.",
            ),
        ),
    ),
    "Thailand": CountryRiskProfile(
        country="Thailand",
        visa_note=(
            "Many nationalities receive short-stay visa-exempt or visa-on-arrival entry; "
            "requirements vary by passport. Verify with Thailand's nearest embassy/consulate."
        ),
        safety_advisory_level="increased_caution",
        safety_note=(
            "Generally safe for tourists; exercise increased caution in southernmost border "
            "provinces due to periodic unrest."
        ),
        health_note="Mosquito-borne illness precautions (e.g. dengue) are commonly recommended.",
        seasonal_risks=(
            SeasonalRisk(
                label="Monsoon / flood season",
                start_month=6,
                end_month=10,
                severity="medium",
                detail="Heavy rain and localized flooding can disrupt travel, peaking Jun-Oct.",
            ),
        ),
    ),
    "Singapore": CountryRiskProfile(
        country="Singapore",
        visa_note=(
            "Many nationalities receive short-stay visa-free entry; requirements vary by "
            "passport. Verify with Singapore's nearest embassy/consulate."
        ),
        safety_advisory_level="normal",
        safety_note="Very low crime; standard precautions apply.",
        health_note="No routine travel vaccinations required for most visitors.",
        seasonal_risks=(
            SeasonalRisk(
                label="Regional haze season",
                start_month=6,
                end_month=10,
                severity="low",
                detail="Transboundary haze from regional land/forest fires can periodically affect air quality.",
            ),
        ),
    ),
}


def get_profile(country: str | None) -> CountryRiskProfile | None:
    """The risk profile for `country`, or None when this agent has no reference data for it.

    None is a normal outcome, not an error -- callers must treat it as "no
    verified information available" rather than letting the model guess.
    """
    if not country:
        return None
    return COUNTRY_RISK_PROFILES.get(country.strip())


def active_seasonal_risks(
    profile: CountryRiskProfile, departure_date: date, return_date: date
) -> list[SeasonalRisk]:
    """Seasonal risks whose window overlaps any month of the trip."""
    months: set[int] = set()
    month_cursor = departure_date.month
    year_cursor = departure_date.year
    while (year_cursor, month_cursor) <= (return_date.year, return_date.month):
        months.add(month_cursor)
        month_cursor += 1
        if month_cursor > 12:
            month_cursor = 1
            year_cursor += 1
    return [risk for risk in profile.seasonal_risks if any(risk.covers(m) for m in months)]
