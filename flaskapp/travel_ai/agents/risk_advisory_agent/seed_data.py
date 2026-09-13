"""Curated seed content for Risk & Advisory Agent's reference tables.

Covers the 5 destinations the team agreed on for demos and testing —
Singapore (SIN), Berlin (BER), Tokyo (HND/NRT), Barcelona (BCN), and
Washington, D.C. (IAD/DCA) — matching `flaskapp/places.py`'s slugs exactly, so
a lookup by the same city a traveller picked in the intake form always hits.

Every fact here started from real government/consular advisories, safety
guides and event calendars (visa rules, local laws, crime patterns, seasonal
weather, named festivals), researched to keep the content realistic rather
than arbitrary. It is nonetheless written and packaged as **illustrative
reference data**, not a live feed — see `docs/risk_advisory_agent/design.md`
and the `source` default on every table (`database.py`'s
`risk_standing_facts` / `risk_seasonal_windows` / `risk_dated_events`) for why:
this is a synthetic dataset for a coursework system, not a subscription to an
advisory service, and it will drift out of date. `RiskDataProvider` (see
`providers/`) is the seam for replacing it with a live source later without
touching anything that reads from it.

`applies_to` is populated only for `category == "traveler_group_risk"` rows.
Every one of those states a legal or social fact plainly and cites what kind
of fact it is (legal status, general sentiment) rather than characterising a
country or its people — the same attribute-vs-stereotype discipline the
project's own guardrails already enforce elsewhere.

Load with `flaskapp.database.seed_risk_reference_data(path, STANDING_FACTS,
SEASONAL_WINDOWS, DATED_EVENTS)`.
"""

from __future__ import annotations

# --- Standing facts: true regardless of travel dates -----------------------
# Ten categories, held consistent across all five cities so demo coverage
# doesn't depend on which destination happens to get picked:
# visa_entry, local_laws, cultural_norms, currency_customs, cybersecurity,
# crime_safety, scams, political_stability, traveler_group_risk,
# emergency_resources.

STANDING_FACTS: list[dict] = [
    # --- Singapore ---
    {
        "destination_slug": "sg-singapore", "category": "visa_entry", "severity": "low",
        "title": "Visa-free short stay for many nationalities",
        "detail": (
            "Many nationalities may enter visa-free for tourism for up to 90 days. "
            "Passports must be valid at least 6 months beyond arrival with a blank "
            "page for the entry stamp. All travellers must complete the Singapore "
            "Arrival Card (SGAC) online up to 3 days before arrival."
        ),
        "mitigation": "Confirm your nationality's visa-free allowance and complete the SGAC before departure.",
    },
    {
        "destination_slug": "sg-singapore", "category": "local_laws", "severity": "high",
        "title": "Strict laws on chewing gum, vaping, and drugs",
        "detail": (
            "Importing or possessing chewing gum is prohibited; e-cigarettes and "
            "vapes are illegal to possess or import, including for personal use. "
            "Drug offences, even for small personal quantities, carry severe "
            "penalties including long imprisonment, and trafficking can carry the "
            "death penalty."
        ),
        "mitigation": "Do not bring gum, vapes, or any controlled substance into the country.",
    },
    {
        "destination_slug": "sg-singapore", "category": "cultural_norms", "severity": "low",
        "title": "Modest dress at religious sites",
        "detail": (
            "Shoulders and knees should be covered when visiting mosques, Hindu and "
            "Buddhist temples, and similar sites; shoes are removed before entering "
            "many of them."
        ),
        "mitigation": "Carry a light scarf or cover-up when visiting religious sites.",
    },
    {
        "destination_slug": "sg-singapore", "category": "currency_customs", "severity": "medium",
        "title": "Strict biosecurity and customs controls",
        "detail": (
            "Importing gum, vapes, and certain foods, plants, or animal products is "
            "restricted or banned; cash or monetary instruments over SGD 20,000 "
            "must be declared on arrival or departure."
        ),
        "mitigation": "Check the prohibited/controlled goods list before packing and declare large cash amounts.",
    },
    {
        "destination_slug": "sg-singapore", "category": "cybersecurity", "severity": "medium",
        "title": "Unauthorised WiFi use is a criminal offence",
        "detail": (
            "Connecting to another person's WiFi network without authorisation is "
            "an offence under the Computer Misuse Act and can lead to a fine or "
            "imprisonment."
        ),
        "mitigation": "Use only your own mobile data or explicitly public/authorised WiFi networks.",
    },
    {
        "destination_slug": "sg-singapore", "category": "crime_safety", "severity": "low",
        "title": "Very low crime, isolated tourist-area theft",
        "detail": (
            "Singapore has one of the world's lowest crime rates and violent crime "
            "against visitors is rare. Petty theft and pickpocketing occasionally "
            "occur at the airport, tourist attractions, and on public transport."
        ),
        "mitigation": "Keep bags closed and belongings in sight in crowded tourist areas.",
    },
    {
        "destination_slug": "sg-singapore", "category": "scams", "severity": "low",
        "title": "Ticket resale and impersonation scams",
        "detail": (
            "Reported scams include unofficial vendors selling discounted or "
            "counterfeit attraction/event tickets, and callers impersonating "
            "government agencies (e.g. immigration or health authorities) "
            "demanding payment or personal information."
        ),
        "mitigation": "Buy tickets only from official vendors; government agencies do not request payment by phone.",
    },
    {
        "destination_slug": "sg-singapore", "category": "political_stability", "severity": "low",
        "title": "Stable, low protest activity",
        "detail": (
            "Public assemblies require a police permit under the Public Order Act; "
            "unauthorised demonstrations are uncommon and are dispersed quickly "
            "when they occur."
        ),
        "mitigation": None,
    },
    {
        "destination_slug": "sg-singapore", "category": "traveler_group_risk",
        "applies_to": "lgbtq_travellers", "severity": "medium",
        "title": "Legal status of same-sex relationships",
        "detail": (
            "Sexual activity between men is a criminal offence under local law, "
            "though prosecutions of tourists are not a documented pattern; "
            "same-sex relationships have no legal recognition and there are no "
            "anti-discrimination protections for LGBTQ+ people."
        ),
        "mitigation": "Exercise discretion with public displays of affection.",
    },
    {
        "destination_slug": "sg-singapore", "category": "emergency_resources", "severity": None,
        "title": "Emergency numbers",
        "detail": "Police: 999. Ambulance/Fire: 995. Non-emergency police hotline: 1800-255-0000.",
        "mitigation": None,
    },
    {
        "destination_slug": "sg-singapore", "category": "health_general", "severity": "low",
        "title": "No routine vaccinations required; dengue present year-round",
        "detail": (
            "No routine vaccinations are required for most visitors. Medical "
            "infrastructure is excellent. Dengue fever is present year-round, "
            "with case numbers typically rising in warmer, wetter months."
        ),
        "mitigation": "Use mosquito repellent, especially outdoors in early morning and evening.",
    },

    # --- Berlin ---
    {
        "destination_slug": "de-berlin", "category": "visa_entry", "severity": "low",
        "title": "Schengen visa-free short stay",
        "detail": (
            "Citizens of many countries may enter visa-free for stays up to 90 "
            "days within any 180-day period under Schengen rules. EU/EEA citizens "
            "may travel on a national ID card; others need a passport valid for "
            "the intended stay."
        ),
        "mitigation": "Confirm your nationality's Schengen visa requirement and track your 90/180-day allowance.",
    },
    {
        "destination_slug": "de-berlin", "category": "local_laws", "severity": "low",
        "title": "Jaywalking fines and residential noise curfews",
        "detail": (
            "Crossing against a red pedestrian signal (\"Ampel\") can incur an "
            "on-the-spot fine even with no traffic present. Residential noise "
            "curfews (Ruhezeit) restrict loud activity at night and on Sundays."
        ),
        "mitigation": "Cross only at green pedestrian signals and keep noise down at night and on Sundays.",
    },
    {
        "destination_slug": "de-berlin", "category": "cultural_norms", "severity": "low",
        "title": "Sunday and public-holiday retail closures",
        "detail": (
            "Most shops are closed on Sundays and public holidays under German "
            "retail-hours law, with exceptions for bakeries, restaurants, and "
            "shops in major train stations and some tourist zones."
        ),
        "mitigation": "Plan grocery and retail shopping around weekday hours, or use train-station shops on Sundays.",
    },
    {
        "destination_slug": "de-berlin", "category": "currency_customs", "severity": "low",
        "title": "EU cash declaration threshold",
        "detail": "Travellers entering or leaving the EU with EUR 10,000 or more in cash must declare it to customs.",
        "mitigation": "Declare cash above the threshold on arrival or departure.",
    },
    {
        "destination_slug": "de-berlin", "category": "cybersecurity", "severity": "low",
        "title": "Unsecured public WiFi",
        "detail": "Public WiFi in cafes and transit hubs is widely available but largely unsecured.",
        "mitigation": "Avoid banking or entering payment details on open public WiFi networks.",
    },
    {
        "destination_slug": "de-berlin", "category": "crime_safety", "severity": "low",
        "title": "Generally safe, pickpocketing in crowded areas",
        "detail": (
            "Violent crime against visitors is uncommon. Pickpocketing occurs in "
            "crowded tourist areas, on public transport, and at busy Christmas "
            "markets."
        ),
        "mitigation": "Keep valuables secured on public transport and at crowded markets.",
    },
    {
        "destination_slug": "de-berlin", "category": "scams", "severity": "low",
        "title": "Petition and street-game scams",
        "detail": (
            "Reported scams include groups soliciting signatures or donations for "
            "unregistered causes as a distraction for pickpocketing, and street "
            "shell or card games designed to take money from onlookers."
        ),
        "mitigation": "Decline street petitions and any game involving money.",
    },
    {
        "destination_slug": "de-berlin", "category": "political_stability", "severity": "medium",
        "title": "Frequent organised demonstrations",
        "detail": (
            "Berlin regularly hosts large political demonstrations, particularly "
            "in the government quarter (Mitte) and around Brandenburg Gate. Most "
            "are peaceful but can cause road closures and transport disruption."
        ),
        "mitigation": "Check planned demonstration routes, allow extra travel time, and avoid the immediate vicinity of large gatherings.",
    },
    {
        "destination_slug": "de-berlin", "category": "traveler_group_risk",
        "applies_to": "lgbtq_travellers", "severity": "low",
        "title": "Legal status and general acceptance",
        "detail": (
            "Same-sex relationships and marriage are legal nationwide. Berlin has "
            "an active LGBTQ+ community and nightlife scene, particularly around "
            "Schöneberg; isolated incidents of harassment are reported, as in most "
            "large cities."
        ),
        "mitigation": None,
    },
    {
        "destination_slug": "de-berlin", "category": "emergency_resources", "severity": None,
        "title": "Emergency numbers",
        "detail": "Police: 110. Ambulance/Fire: 112 (EU-wide).",
        "mitigation": None,
    },
    {
        "destination_slug": "de-berlin", "category": "health_general", "severity": "low",
        "title": "No routine vaccinations required; high-quality care",
        "detail": (
            "No routine vaccinations are required for most visitors. Germany has "
            "high-quality, widely available medical care."
        ),
        "mitigation": "Carry proof of travel health insurance; EU nationals should bring an EHIC/GHIC card.",
    },
    {
        "destination_slug": "de-berlin", "category": "disruption", "severity": "low",
        "title": "Periodic transit and aviation labour action",
        "detail": (
            "German transport unions periodically call strikes affecting "
            "regional and long-distance rail, and occasionally airport ground "
            "staff or security. These are usually announced days in advance but "
            "can still disrupt travel plans."
        ),
        "mitigation": "Check Deutsche Bahn and airline strike notices in the days before travel and build in buffer time.",
    },

    # --- Tokyo ---
    {
        "destination_slug": "jp-tokyo", "category": "visa_entry", "severity": "low",
        "title": "Visa-free short stay for many nationalities",
        "detail": (
            "Many nationalities may enter visa-free for tourism for up to 90 days. "
            "Some visa-exempt entries additionally require online pre-arrival "
            "registration; requirements are periodically updated."
        ),
        "mitigation": "Check the current visa-exemption and any pre-arrival registration requirement for your nationality before departure.",
    },
    {
        "destination_slug": "jp-tokyo", "category": "local_laws", "severity": "low",
        "title": "Public smoking restrictions",
        "detail": (
            "Smoking on the street outside designated smoking areas is banned in "
            "many central wards (including Chiyoda, Shibuya, and Shinjuku) and can "
            "incur an on-the-spot fine."
        ),
        "mitigation": "Smoke only in designated smoking areas.",
    },
    {
        "destination_slug": "jp-tokyo", "category": "cultural_norms", "severity": "low",
        "title": "Etiquette at religious sites and on public transport",
        "detail": (
            "Loud conversation and phone calls on trains are considered impolite; "
            "eating while walking is uncommon; some shrine and temple buildings "
            "restrict photography; shoes are removed before entering many indoor "
            "spaces."
        ),
        "mitigation": "Observe posted signage at religious sites and follow local passengers' behaviour on public transport.",
    },
    {
        "destination_slug": "jp-tokyo", "category": "currency_customs", "severity": "low",
        "title": "Cash-preference society and currency declaration",
        "detail": (
            "Japan remains largely cash-based outside major chains and stations. "
            "Travellers carrying cash equivalent to more than JPY 1,000,000 must "
            "declare it on arrival or departure."
        ),
        "mitigation": "Carry sufficient cash for smaller vendors and declare large cash amounts.",
    },
    {
        "destination_slug": "jp-tokyo", "category": "cybersecurity", "severity": "low",
        "title": "Limited free public WiFi",
        "detail": "Free public WiFi is less widely available than in some other major cities and coverage can be inconsistent.",
        "mitigation": "Consider a local SIM or portable WiFi router for reliable connectivity.",
    },
    {
        "destination_slug": "jp-tokyo", "category": "crime_safety", "severity": "low",
        "title": "Very low crime, isolated nightlife-district risks",
        "detail": (
            "Tokyo is among the safest major cities globally for violent crime. "
            "Nightlife districts such as Kabukicho and Roppongi have reported "
            "incidents of inflated bar bills, drink spiking, and aggressive "
            "touting."
        ),
        "mitigation": "Avoid touts offering bars or clubs and confirm prices before ordering in nightlife districts.",
    },
    {
        "destination_slug": "jp-tokyo", "category": "scams", "severity": "low",
        "title": "Overcharging in tourist nightlife areas",
        "detail": "Reported cases of unlisted cover charges or inflated bills, concentrated in nightlife districts.",
        "mitigation": "Confirm menu prices and any cover/table charge before sitting down.",
    },
    {
        "destination_slug": "jp-tokyo", "category": "political_stability", "severity": "low",
        "title": "Stable, minimal disruption to travellers",
        "detail": "Public demonstrations are infrequent and typically small-scale; disruption to travellers is rare.",
        "mitigation": None,
    },
    {
        "destination_slug": "jp-tokyo", "category": "traveler_group_risk",
        "applies_to": "lgbtq_travellers", "severity": "low",
        "title": "Legal status and general acceptance",
        "detail": (
            "Same-sex sexual activity is not criminalised. Same-sex marriage is "
            "not recognised nationally, though some municipalities, including "
            "parts of Tokyo, issue local partnership certificates. Public "
            "attitudes are generally tolerant, though LGBTQ+ visibility is lower "
            "than in some Western cities."
        ),
        "mitigation": None,
    },
    {
        "destination_slug": "jp-tokyo", "category": "emergency_resources", "severity": None,
        "title": "Emergency numbers",
        "detail": "Police: 110. Ambulance/Fire: 119.",
        "mitigation": None,
    },
    {
        "destination_slug": "jp-tokyo", "category": "health_general", "severity": "low",
        "title": "No routine vaccinations required; insurance strongly advised",
        "detail": (
            "No routine vaccinations are required for most visitors. Medical "
            "care is high-quality but can be costly without insurance, and "
            "English-language service varies by facility."
        ),
        "mitigation": "Carry travel health insurance and a list of English-speaking clinics in Tokyo.",
    },

    # --- Barcelona ---
    {
        "destination_slug": "es-barcelona", "category": "visa_entry", "severity": "low",
        "title": "Schengen visa-free short stay",
        "detail": (
            "Same Schengen rule as the rest of the zone: many nationalities may "
            "enter visa-free for stays up to 90 days within any 180-day period."
        ),
        "mitigation": "Confirm your nationality's Schengen visa requirement and track your 90/180-day allowance.",
    },
    {
        "destination_slug": "es-barcelona", "category": "local_laws", "severity": "low",
        "title": "Beachwear restrictions away from the beach",
        "detail": (
            "A local ordinance prohibits wearing swimwear or going shirtless "
            "outside the immediate beach and pool area; this is enforced with "
            "fines in central Barcelona."
        ),
        "mitigation": "Change into regular clothing before leaving the beach area.",
    },
    {
        "destination_slug": "es-barcelona", "category": "cultural_norms", "severity": "low",
        "title": "Later meal times",
        "detail": (
            "Lunch typically runs from around 1pm-4pm and dinner from 9pm "
            "onward; many restaurants close mid-afternoon between these hours."
        ),
        "mitigation": "Plan meals around local hours or seek out tourist-oriented restaurants with continuous service.",
    },
    {
        "destination_slug": "es-barcelona", "category": "currency_customs", "severity": "low",
        "title": "EU cash declaration threshold",
        "detail": "Travellers entering or leaving the EU with EUR 10,000 or more in cash must declare it to customs.",
        "mitigation": "Declare cash above the threshold on arrival or departure.",
    },
    {
        "destination_slug": "es-barcelona", "category": "cybersecurity", "severity": "low",
        "title": "Unsecured public WiFi",
        "detail": "Public WiFi around tourist areas and transit hubs is widely available but largely unsecured.",
        "mitigation": "Avoid banking or entering payment details on open public WiFi networks.",
    },
    {
        "destination_slug": "es-barcelona", "category": "crime_safety", "severity": "medium",
        "title": "Pickpocketing concentrated in specific tourist areas",
        "detail": (
            "Petty theft and pickpocketing are concentrated along La Rambla, the "
            "Gothic Quarter, Barceloneta beach, the Sagrada Família surroundings, "
            "and Metro Line 3 between Liceu and Drassanes."
        ),
        "mitigation": "Carry bags in front of you and stay alert to belongings in these specific hotspots.",
    },
    {
        "destination_slug": "es-barcelona", "category": "scams", "severity": "medium",
        "title": "Petition-clipboard and fake-police scams",
        "detail": (
            "Common scams include groups using clipboard petitions as a "
            "distraction for theft, individuals posing as plainclothes police "
            "asking to inspect wallets, and street games designed to separate "
            "onlookers from their money."
        ),
        "mitigation": "Do not hand your wallet or bag to anyone claiming to be police without confirming identification at a police station; decline street petitions and money games.",
    },
    {
        "destination_slug": "es-barcelona", "category": "political_stability", "severity": "medium",
        "title": "Periodic large-scale demonstrations",
        "detail": (
            "Catalonia's independence movement periodically produces large "
            "demonstrations in central Barcelona; separately, anti-overtourism "
            "protests have targeted areas including La Rambla, Plaça de "
            "Catalunya, and the Sagrada Família vicinity."
        ),
        "mitigation": "Check for planned demonstrations before travelling through central areas.",
    },
    {
        "destination_slug": "es-barcelona", "category": "traveler_group_risk",
        "applies_to": "lgbtq_travellers", "severity": "low",
        "title": "Legal status and general acceptance",
        "detail": (
            "Same-sex marriage has been legal in Spain since 2005. Barcelona has "
            "an active LGBTQ+ scene, particularly in the Eixample district "
            "(known locally as the \"Gaixample\")."
        ),
        "mitigation": None,
    },
    {
        "destination_slug": "es-barcelona", "category": "emergency_resources", "severity": None,
        "title": "Emergency numbers",
        "detail": "EU-wide emergency number: 112. Local police (Guàrdia Urbana): 092.",
        "mitigation": None,
    },
    {
        "destination_slug": "es-barcelona", "category": "health_general", "severity": "low",
        "title": "No routine vaccinations required; high standard of care",
        "detail": "No routine vaccinations are required for most visitors. Spain has a high standard of public and private healthcare.",
        "mitigation": "EU nationals should carry an EHIC/GHIC card; others should have private travel insurance.",
    },
    {
        "destination_slug": "es-barcelona", "category": "disruption", "severity": "low",
        "title": "Periodic transit and general strikes",
        "detail": (
            "Spain, including Catalonia, periodically sees transit-worker "
            "strikes (metro, regional rail) and occasional general strikes tied "
            "to political or labour disputes."
        ),
        "mitigation": "Check local transit authority (TMB, Rodalies) strike notices before travel days.",
    },

    # --- Washington, D.C. ---
    {
        "destination_slug": "us-washington", "category": "visa_entry", "severity": "low",
        "title": "ESTA / Visa Waiver Program",
        "detail": (
            "Citizens of Visa Waiver Program countries may enter for up to 90 "
            "days for tourism or business with an approved ESTA authorisation, "
            "which should be requested at least 72 hours before departure."
        ),
        "mitigation": "Apply for ESTA well ahead of departure and confirm it is approved before flying.",
    },
    {
        "destination_slug": "us-washington", "category": "local_laws", "severity": "low",
        "title": "Firearms and open-container restrictions",
        "detail": (
            "Carrying firearms is heavily restricted for visitors; consuming "
            "alcohol in open containers in public outside licensed premises is "
            "illegal."
        ),
        "mitigation": "Do not carry firearms and consume alcohol only on licensed premises or private property.",
    },
    {
        "destination_slug": "us-washington", "category": "cultural_norms", "severity": "low",
        "title": "Tipping expectations",
        "detail": (
            "Tipping 15-20% at sit-down restaurants and bars is a strong social "
            "expectation rather than optional, since service-industry wages "
            "assume tipped income."
        ),
        "mitigation": "Budget for tips at restaurants, bars, and for taxi/rideshare drivers.",
    },
    {
        "destination_slug": "us-washington", "category": "currency_customs", "severity": "low",
        "title": "Cash declaration threshold",
        "detail": "Travellers must declare cash or monetary instruments over USD 10,000 when entering or leaving the United States.",
        "mitigation": "Declare amounts above the threshold on the customs form.",
    },
    {
        "destination_slug": "us-washington", "category": "cybersecurity", "severity": "low",
        "title": "Unsecured public WiFi around the Mall",
        "detail": "Free WiFi at museums and public spaces around the National Mall is widely available but unsecured.",
        "mitigation": "Avoid financial transactions on open public WiFi networks.",
    },
    {
        "destination_slug": "us-washington", "category": "crime_safety", "severity": "medium",
        "title": "Uneven crime distribution by neighbourhood",
        "detail": (
            "Violent crime is concentrated outside the main visitor core. The "
            "National Mall, downtown, Georgetown, Dupont Circle, and Capitol Hill "
            "are well-trafficked and active into the evening, while some "
            "neighbourhoods in eastern and northeastern D.C. warrant added "
            "caution after dark."
        ),
        "mitigation": "Use well-lit, well-trafficked routes at night and check neighbourhood context before walking in unfamiliar areas after dark.",
    },
    {
        "destination_slug": "us-washington", "category": "scams", "severity": "low",
        "title": "Pickpocketing during peak-crowd events",
        "detail": "Pickpocketing incidents rise during high-crowd periods such as the Cherry Blossom Festival, particularly around the National Mall.",
        "mitigation": "Keep bags zipped and in front of you in dense crowds around the Mall.",
    },
    {
        "destination_slug": "us-washington", "category": "political_stability", "severity": "medium",
        "title": "Frequent demonstrations near federal buildings",
        "detail": (
            "Demonstrations near the Capitol, White House, and federal buildings "
            "are common and can occur with little notice; most are peaceful but "
            "can cause street closures and heightened security screening."
        ),
        "mitigation": "Check for planned demonstrations and allow extra time near federal buildings and the National Mall.",
    },
    {
        "destination_slug": "us-washington", "category": "traveler_group_risk",
        "applies_to": "lgbtq_travellers", "severity": "low",
        "title": "Legal status and general acceptance",
        "detail": (
            "Same-sex marriage is legal nationwide. Washington, D.C. has an "
            "active LGBTQ+ community centred on the Dupont Circle and Logan "
            "Circle areas."
        ),
        "mitigation": None,
    },
    {
        "destination_slug": "us-washington", "category": "emergency_resources", "severity": None,
        "title": "Emergency numbers",
        "detail": "Police/Fire/Ambulance: 911.",
        "mitigation": None,
    },
    {
        "destination_slug": "us-washington", "category": "health_general", "severity": "low",
        "title": "No routine vaccinations required; insurance strongly advised",
        "detail": "No routine vaccinations are required for most visitors. Healthcare quality is high, but treatment without insurance can be very costly.",
        "mitigation": "Purchase comprehensive travel medical insurance before arrival, since US healthcare costs for the uninsured are notably high.",
    },
    {
        "destination_slug": "us-washington", "category": "disruption", "severity": "low",
        "title": "Occasional airline and airport labour disputes",
        "detail": (
            "US airlines and airport contractors periodically experience labour "
            "disputes or short-notice job actions that can affect flight "
            "schedules, particularly around major holiday travel periods."
        ),
        "mitigation": "Monitor your airline's operational status in the days before travel during peak periods.",
    },
]

# --- Seasonal windows: recurring, month-bound risk -------------------------

SEASONAL_WINDOWS: list[dict] = [
    {
        "destination_slug": "sg-singapore", "label": "Regional haze season",
        "start_month": 6, "end_month": 10, "severity": "medium",
        "detail": (
            "Transboundary haze from land and forest fires in the region can "
            "periodically degrade air quality, most often July-October; severity "
            "varies year to year."
        ),
        "mitigation": "Check the National Environment Agency's PSI readings and limit prolonged outdoor activity on high-PSI days.",
    },
    {
        "destination_slug": "de-berlin", "label": "Winter storm season",
        "start_month": 12, "end_month": 2, "severity": "low",
        "detail": "Winter storms and snow occasionally disrupt regional rail and flight schedules between December and February.",
        "mitigation": "Build buffer time around winter travel connections and monitor rail/airline status.",
    },
    {
        "destination_slug": "jp-tokyo", "label": "Typhoon season",
        "start_month": 6, "end_month": 10, "severity": "high",
        "detail": (
            "Typhoons can cause widespread, short-notice cancellation of flights "
            "and Shinkansen/rail services, peaking August-September."
        ),
        "mitigation": "Build buffer days around travel dates in this window and monitor Japan Meteorological Agency typhoon advisories.",
    },
    {
        "destination_slug": "jp-tokyo", "label": "Summer heat and humidity",
        "start_month": 7, "end_month": 8, "severity": "medium",
        "detail": "July-August brings high heat and humidity with periodic heat-advisory days; heatstroke risk is elevated for outdoor activity.",
        "mitigation": "Stay hydrated, avoid midday outdoor exertion, and use widely available cooling shelters.",
    },
    {
        "destination_slug": "es-barcelona", "label": "Summer heatwave season",
        "start_month": 7, "end_month": 8, "severity": "medium",
        "detail": "Periodic heatwaves bring high heat and humidity; some attractions reduce hours during the hottest stretches.",
        "mitigation": "Plan outdoor sightseeing for morning or evening and stay hydrated.",
    },
    {
        "destination_slug": "us-washington", "label": "Atlantic hurricane season (remnants)",
        "start_month": 6, "end_month": 11, "severity": "low",
        "detail": (
            "Washington, D.C. is inland but can experience heavy rain and wind "
            "from the remnants of Atlantic tropical storms, occasionally "
            "disrupting flights."
        ),
        "mitigation": "Monitor forecasts for tropical storm remnants when travelling in this window.",
    },
    {
        "destination_slug": "us-washington", "label": "Winter storm season",
        "start_month": 12, "end_month": 2, "severity": "low",
        "detail": "Winter storms periodically disrupt flights and road travel between December and February.",
        "mitigation": "Build buffer time around winter travel dates.",
    },
]

# --- Dated events: specific 2026 date ranges --------------------------------

DATED_EVENTS: list[dict] = [
    {
        "destination_slug": "sg-singapore", "category": "public_holiday",
        "name": "National Day", "start_date": "2026-08-07", "end_date": "2026-08-09",
        "impact": "transport_disruption",
        "detail": "National Day Parade rehearsals and the event itself close roads around Marina Bay and increase crowding on public transport.",
    },
    {
        "destination_slug": "sg-singapore", "category": "local_event",
        "name": "Formula 1 Singapore Grand Prix", "start_date": "2026-10-02", "end_date": "2026-10-04",
        "impact": "price_surge",
        "detail": "Major international event drawing large crowds to the Marina Bay area; hotel rates citywide typically rise and roads around the circuit close for the event.",
    },
    {
        "destination_slug": "de-berlin", "category": "local_event",
        "name": "Christmas markets season (incl. Gendarmenmarkt WeihnachtsZauber)",
        "start_date": "2026-11-23", "end_date": "2026-12-31",
        "impact": "price_surge",
        "detail": "Numerous Christmas markets operate across the city with a visibly increased security presence at major sites; hotel rates rise through the period.",
    },
    {
        "destination_slug": "de-berlin", "category": "local_event",
        "name": "Berlinale (Berlin International Film Festival)",
        "start_date": "2026-02-12", "end_date": "2026-02-22",
        "impact": "price_surge",
        "detail": "Major international film festival draws large crowds and press; hotel availability tightens in central districts.",
    },
    {
        "destination_slug": "jp-tokyo", "category": "local_event",
        "name": "Cherry blossom season (sakura)", "start_date": "2026-03-20", "end_date": "2026-04-05",
        "impact": "price_surge",
        "detail": "Peak bloom draws large crowds to viewing spots such as Ueno Park and Shinjuku Gyoen; hotel rates rise sharply and popular viewing areas become very crowded.",
    },
    {
        # Deliberately overlaps both the typhoon-season and summer-heat
        # seasonal windows above — the case that lets reasoning.py demonstrate
        # connecting more than one already-grounded fact into one warning
        # (Architecture_v2.md §4's worked example), not just restating one.
        "destination_slug": "jp-tokyo", "category": "public_holiday",
        "name": "Obon holiday period", "start_date": "2026-08-13", "end_date": "2026-08-16",
        "impact": "crowding",
        "detail": "Nationwide holiday period with heavy domestic travel; trains and flights are heavily booked and roads congested; many smaller businesses close.",
    },
    {
        "destination_slug": "es-barcelona", "category": "local_event",
        "name": "La Mercè Festival", "start_date": "2026-09-23", "end_date": "2026-09-28",
        "impact": "crowding",
        "detail": "Barcelona's main city festival: five days of free public events including castells (human towers), correfocs (fire runs), and concerts across the city, raising hotel occupancy citywide.",
    },
    {
        "destination_slug": "es-barcelona", "category": "local_event",
        "name": "Primavera Sound", "start_date": "2026-05-27", "end_date": "2026-05-31",
        "impact": "price_surge",
        "detail": "Major international music festival drawing large crowds; hotel rates in the city rise and popular venues and transit become congested.",
    },
    {
        "destination_slug": "us-washington", "category": "local_event",
        "name": "National Cherry Blossom Festival", "start_date": "2026-03-20", "end_date": "2026-04-12",
        "impact": "price_surge",
        "detail": "Major annual festival around the Tidal Basin draws large crowds; hotel rates rise citywide and pickpocketing incidents around the National Mall increase during peak bloom.",
    },
    {
        # Falls inside the hurricane-season-remnants window above — another
        # deliberate overlap, this time pairing a named event with a seasonal
        # weather risk rather than two named events.
        "destination_slug": "us-washington", "category": "public_holiday",
        "name": "Independence Day (Fourth of July)", "start_date": "2026-07-03", "end_date": "2026-07-04",
        "impact": "crowding",
        "detail": "Independence Day fireworks and celebrations draw very large crowds to the National Mall, with heightened security screening and street closures.",
    },
]
