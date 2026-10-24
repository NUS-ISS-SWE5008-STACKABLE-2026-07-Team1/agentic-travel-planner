"""Cities travellers can fly from and to, and the airports that serve them.

Why this sits at `flaskapp/` level rather than inside `travel_ai/agents/flight_agent/`:
it is a **shared contract**. Flight Agent resolves a city to airport codes,
Hotel & Transport keys its property inventory on the same cities, and the intake
form builds its `<select>` options from them. One dataset, one place, so the
three cannot drift apart.

## The slug is the key

Every city is keyed by a stable slug (`jp-tokyo`), never by its display name.
The name is data that may be corrected — "Osaka" could become "Osaka (Kansai)",
a diacritic could be fixed — and anything holding a foreign key to a display
string breaks when that happens. Downstream databases join on `slug`. It is an
opaque identifier: do not parse it, and do not derive it from the name, or the
stability it exists to provide is lost.

## What counts as a city here

Only cities with a scheduled-service airport that has an IATA code. That is the
natural boundary for a flight planner and it means the city list and the airport
map are the *same* dataset instead of two things to keep in sync. It is a
curated working subset — roughly 230 cities across 61 countries — not a complete
reference dataset. Two consequences, both deliberate and both visible to callers:

1. **A country in `countries.py` may have no cities here.** `cities_for` returns
   an empty tuple, the form offers nothing to pick, and the adapter reports it
   as unresolved. That is the honest "we cannot route this" answer rather than a
   wrong airport.
2. **A city may list several airports.** `airports` is ordered with the primary
   gateway first, and callers that can only use one (display, a legacy scalar
   field) should take `airports[0]`. Callers that search inventory should use
   the whole tuple — that is the point of collecting a city at all.

Airport codes are IATA. Where a metro area has several (Tokyo, London, New York)
they are listed together because a traveller choosing "Tokyo" means the city,
not Narita specifically.

Country keys MUST match `flaskapp/countries.py` exactly — that tuple is the
form's source of truth, and `tests/test_places.py` asserts the match so a typo
here fails the build rather than silently hiding a country from the city select.

Note on coverage: Hong Kong is listed as a city of China (`cn-hong-kong`), which
is how the team decided to represent it. `countries.py` has no separate "Hong
Kong" entry, so this is also what makes its existing HKG flight inventory
reachable from the form at all.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class City:
    """One selectable city and the airports that serve it.

    Frozen because this is reference data shared across three agents — a caller
    mutating a city in place would corrupt it for everyone in-process.
    """

    slug: str
    name: str
    country: str
    airports: tuple[str, ...]

    @property
    def primary_airport(self) -> str:
        """The main gateway. Use when exactly one code is required."""
        return self.airports[0]

    @property
    def label(self) -> str:
        """Display form for a dropdown: `Tokyo (HND/NRT)`."""
        return f"{self.name} ({'/'.join(self.airports)})"


# slug -> (display name, country, airports ordered primary-first)
#
# Grouped by country, countries alphabetical, cities alphabetical within a
# country so the rendered <select> is predictable. Slug prefixes are ISO 3166-1
# alpha-2 lowercase; they are part of the stable key, so never renumber them.
_RAW: dict[str, tuple[str, str, tuple[str, ...]]] = {
    # --- Argentina ---
    "ar-buenos-aires": ("Buenos Aires", "Argentina", ("EZE", "AEP")),
    "ar-cordoba": ("Córdoba", "Argentina", ("COR",)),
    "ar-mendoza": ("Mendoza", "Argentina", ("MDZ",)),
    # --- Australia ---
    "au-adelaide": ("Adelaide", "Australia", ("ADL",)),
    "au-brisbane": ("Brisbane", "Australia", ("BNE",)),
    "au-cairns": ("Cairns", "Australia", ("CNS",)),
    "au-canberra": ("Canberra", "Australia", ("CBR",)),
    "au-darwin": ("Darwin", "Australia", ("DRW",)),
    "au-gold-coast": ("Gold Coast", "Australia", ("OOL",)),
    "au-hobart": ("Hobart", "Australia", ("HBA",)),
    "au-melbourne": ("Melbourne", "Australia", ("MEL",)),
    "au-perth": ("Perth", "Australia", ("PER",)),
    "au-sydney": ("Sydney", "Australia", ("SYD",)),
    # --- Austria ---
    "at-innsbruck": ("Innsbruck", "Austria", ("INN",)),
    "at-salzburg": ("Salzburg", "Austria", ("SZG",)),
    "at-vienna": ("Vienna", "Austria", ("VIE",)),
    # --- Bangladesh ---
    "bd-chittagong": ("Chittagong", "Bangladesh", ("CGP",)),
    "bd-dhaka": ("Dhaka", "Bangladesh", ("DAC",)),
    # --- Belgium ---
    "be-antwerp": ("Antwerp", "Belgium", ("ANR",)),
    "be-brussels": ("Brussels", "Belgium", ("BRU", "CRL")),
    # --- Brazil ---
    "br-brasilia": ("Brasília", "Brazil", ("BSB",)),
    "br-rio-de-janeiro": ("Rio de Janeiro", "Brazil", ("GIG", "SDU")),
    "br-salvador": ("Salvador", "Brazil", ("SSA",)),
    "br-sao-paulo": ("São Paulo", "Brazil", ("GRU", "CGH")),
    # --- Brunei ---
    "bn-bandar-seri-begawan": ("Bandar Seri Begawan", "Brunei", ("BWN",)),
    # --- Cambodia ---
    "kh-phnom-penh": ("Phnom Penh", "Cambodia", ("PNH",)),
    "kh-siem-reap": ("Siem Reap", "Cambodia", ("SAI",)),
    "kh-sihanoukville": ("Sihanoukville", "Cambodia", ("KOS",)),
    # --- Canada ---
    "ca-calgary": ("Calgary", "Canada", ("YYC",)),
    "ca-montreal": ("Montreal", "Canada", ("YUL",)),
    "ca-ottawa": ("Ottawa", "Canada", ("YOW",)),
    "ca-toronto": ("Toronto", "Canada", ("YYZ",)),
    "ca-vancouver": ("Vancouver", "Canada", ("YVR",)),
    # --- Chile ---
    "cl-santiago": ("Santiago", "Chile", ("SCL",)),
    # --- China ---
    "cn-beijing": ("Beijing", "China", ("PEK", "PKX")),
    "cn-chengdu": ("Chengdu", "China", ("TFU", "CTU")),
    "cn-chongqing": ("Chongqing", "China", ("CKG",)),
    "cn-guangzhou": ("Guangzhou", "China", ("CAN",)),
    "cn-hangzhou": ("Hangzhou", "China", ("HGH",)),
    "cn-hong-kong": ("Hong Kong", "China", ("HKG",)),
    "cn-kunming": ("Kunming", "China", ("KMG",)),
    "cn-qingdao": ("Qingdao", "China", ("TAO",)),
    "cn-shanghai": ("Shanghai", "China", ("PVG", "SHA")),
    "cn-shenzhen": ("Shenzhen", "China", ("SZX",)),
    "cn-xian": ("Xi'an", "China", ("XIY",)),
    "cn-xiamen": ("Xiamen", "China", ("XMN",)),
    # --- Croatia ---
    "hr-dubrovnik": ("Dubrovnik", "Croatia", ("DBV",)),
    "hr-split": ("Split", "Croatia", ("SPU",)),
    "hr-zagreb": ("Zagreb", "Croatia", ("ZAG",)),
    # --- Czechia ---
    "cz-brno": ("Brno", "Czechia", ("BRQ",)),
    "cz-prague": ("Prague", "Czechia", ("PRG",)),
    # --- Denmark ---
    "dk-aarhus": ("Aarhus", "Denmark", ("AAR",)),
    "dk-billund": ("Billund", "Denmark", ("BLL",)),
    "dk-copenhagen": ("Copenhagen", "Denmark", ("CPH",)),
    # --- Egypt ---
    "eg-cairo": ("Cairo", "Egypt", ("CAI",)),
    "eg-hurghada": ("Hurghada", "Egypt", ("HRG",)),
    "eg-luxor": ("Luxor", "Egypt", ("LXR",)),
    "eg-sharm-el-sheikh": ("Sharm El Sheikh", "Egypt", ("SSH",)),
    # --- Finland ---
    "fi-helsinki": ("Helsinki", "Finland", ("HEL",)),
    "fi-rovaniemi": ("Rovaniemi", "Finland", ("RVN",)),
    "fi-tampere": ("Tampere", "Finland", ("TMP",)),
    # --- France ---
    "fr-bordeaux": ("Bordeaux", "France", ("BOD",)),
    "fr-lyon": ("Lyon", "France", ("LYS",)),
    "fr-marseille": ("Marseille", "France", ("MRS",)),
    "fr-nantes": ("Nantes", "France", ("NTE",)),
    "fr-nice": ("Nice", "France", ("NCE",)),
    "fr-paris": ("Paris", "France", ("CDG", "ORY", "BVA")),
    "fr-toulouse": ("Toulouse", "France", ("TLS",)),
    # --- Germany ---
    "de-berlin": ("Berlin", "Germany", ("BER",)),
    "de-cologne": ("Cologne", "Germany", ("CGN",)),
    "de-dusseldorf": ("Düsseldorf", "Germany", ("DUS",)),
    "de-frankfurt": ("Frankfurt", "Germany", ("FRA",)),
    "de-hamburg": ("Hamburg", "Germany", ("HAM",)),
    "de-munich": ("Munich", "Germany", ("MUC",)),
    "de-stuttgart": ("Stuttgart", "Germany", ("STR",)),
    # --- Greece ---
    "gr-athens": ("Athens", "Greece", ("ATH",)),
    "gr-corfu": ("Corfu", "Greece", ("CFU",)),
    "gr-heraklion": ("Heraklion", "Greece", ("HER",)),
    "gr-mykonos": ("Mykonos", "Greece", ("JMK",)),
    "gr-rhodes": ("Rhodes", "Greece", ("RHO",)),
    "gr-santorini": ("Santorini", "Greece", ("JTR",)),
    "gr-thessaloniki": ("Thessaloniki", "Greece", ("SKG",)),
    # --- Hungary ---
    "hu-budapest": ("Budapest", "Hungary", ("BUD",)),
    # --- Iceland ---
    "is-reykjavik": ("Reykjavik", "Iceland", ("KEF",)),
    # --- India ---
    "in-ahmedabad": ("Ahmedabad", "India", ("AMD",)),
    "in-bengaluru": ("Bengaluru", "India", ("BLR",)),
    "in-chennai": ("Chennai", "India", ("MAA",)),
    "in-delhi": ("Delhi", "India", ("DEL",)),
    "in-goa": ("Goa", "India", ("GOI",)),
    "in-hyderabad": ("Hyderabad", "India", ("HYD",)),
    "in-jaipur": ("Jaipur", "India", ("JAI",)),
    "in-kochi": ("Kochi", "India", ("COK",)),
    "in-kolkata": ("Kolkata", "India", ("CCU",)),
    "in-mumbai": ("Mumbai", "India", ("BOM",)),
    # --- Indonesia ---
    "id-bali": ("Bali (Denpasar)", "Indonesia", ("DPS",)),
    "id-batam": ("Batam", "Indonesia", ("BTH",)),
    "id-jakarta": ("Jakarta", "Indonesia", ("CGK",)),
    "id-makassar": ("Makassar", "Indonesia", ("UPG",)),
    "id-medan": ("Medan", "Indonesia", ("KNO",)),
    "id-surabaya": ("Surabaya", "Indonesia", ("SUB",)),
    "id-yogyakarta": ("Yogyakarta", "Indonesia", ("YIA",)),
    # --- Ireland ---
    "ie-cork": ("Cork", "Ireland", ("ORK",)),
    "ie-dublin": ("Dublin", "Ireland", ("DUB",)),
    "ie-shannon": ("Shannon", "Ireland", ("SNN",)),
    # --- Israel ---
    "il-tel-aviv": ("Tel Aviv", "Israel", ("TLV",)),
    # --- Italy ---
    "it-bologna": ("Bologna", "Italy", ("BLQ",)),
    "it-catania": ("Catania", "Italy", ("CTA",)),
    "it-florence": ("Florence", "Italy", ("FLR",)),
    "it-milan": ("Milan", "Italy", ("MXP", "LIN", "BGY")),
    "it-naples": ("Naples", "Italy", ("NAP",)),
    "it-pisa": ("Pisa", "Italy", ("PSA",)),
    "it-rome": ("Rome", "Italy", ("FCO", "CIA")),
    "it-venice": ("Venice", "Italy", ("VCE",)),
    # --- Japan ---
    "jp-fukuoka": ("Fukuoka", "Japan", ("FUK",)),
    "jp-hiroshima": ("Hiroshima", "Japan", ("HIJ",)),
    "jp-nagoya": ("Nagoya", "Japan", ("NGO",)),
    "jp-okinawa": ("Okinawa", "Japan", ("OKA",)),
    "jp-osaka": ("Osaka", "Japan", ("KIX", "ITM")),
    "jp-sapporo": ("Sapporo", "Japan", ("CTS",)),
    "jp-sendai": ("Sendai", "Japan", ("SDJ",)),
    "jp-tokyo": ("Tokyo", "Japan", ("NRT", "HND")),
    # --- Jordan ---
    "jo-amman": ("Amman", "Jordan", ("AMM",)),
    "jo-aqaba": ("Aqaba", "Jordan", ("AQJ",)),
    # --- Kenya ---
    "ke-mombasa": ("Mombasa", "Kenya", ("MBA",)),
    "ke-nairobi": ("Nairobi", "Kenya", ("NBO",)),
    # --- Korea, South ---
    "kr-busan": ("Busan", "Korea, South", ("PUS",)),
    "kr-daegu": ("Daegu", "Korea, South", ("TAE",)),
    "kr-jeju": ("Jeju", "Korea, South", ("CJU",)),
    "kr-seoul": ("Seoul", "Korea, South", ("ICN", "GMP")),
    # --- Laos ---
    "la-luang-prabang": ("Luang Prabang", "Laos", ("LPQ",)),
    "la-vientiane": ("Vientiane", "Laos", ("VTE",)),
    # --- Malaysia ---
    "my-johor-bahru": ("Johor Bahru", "Malaysia", ("JHB",)),
    "my-kota-kinabalu": ("Kota Kinabalu", "Malaysia", ("BKI",)),
    "my-kuala-lumpur": ("Kuala Lumpur", "Malaysia", ("KUL",)),
    "my-kuching": ("Kuching", "Malaysia", ("KCH",)),
    "my-langkawi": ("Langkawi", "Malaysia", ("LGK",)),
    "my-penang": ("Penang", "Malaysia", ("PEN",)),
    # --- Maldives ---
    "mv-male": ("Malé", "Maldives", ("MLE",)),
    # --- Mexico ---
    "mx-cancun": ("Cancún", "Mexico", ("CUN",)),
    "mx-guadalajara": ("Guadalajara", "Mexico", ("GDL",)),
    "mx-los-cabos": ("Los Cabos", "Mexico", ("SJD",)),
    "mx-mexico-city": ("Mexico City", "Mexico", ("MEX",)),
    "mx-monterrey": ("Monterrey", "Mexico", ("MTY",)),
    # --- Morocco ---
    "ma-casablanca": ("Casablanca", "Morocco", ("CMN",)),
    "ma-fez": ("Fez", "Morocco", ("FEZ",)),
    "ma-marrakesh": ("Marrakesh", "Morocco", ("RAK",)),
    "ma-tangier": ("Tangier", "Morocco", ("TNG",)),
    # --- Myanmar ---
    "mm-mandalay": ("Mandalay", "Myanmar", ("MDL",)),
    "mm-yangon": ("Yangon", "Myanmar", ("RGN",)),
    # --- Nepal ---
    "np-kathmandu": ("Kathmandu", "Nepal", ("KTM",)),
    "np-pokhara": ("Pokhara", "Nepal", ("PKR",)),
    # --- Netherlands ---
    "nl-amsterdam": ("Amsterdam", "Netherlands", ("AMS",)),
    "nl-eindhoven": ("Eindhoven", "Netherlands", ("EIN",)),
    "nl-rotterdam": ("Rotterdam", "Netherlands", ("RTM",)),
    # --- New Zealand ---
    "nz-auckland": ("Auckland", "New Zealand", ("AKL",)),
    "nz-christchurch": ("Christchurch", "New Zealand", ("CHC",)),
    "nz-queenstown": ("Queenstown", "New Zealand", ("ZQN",)),
    "nz-wellington": ("Wellington", "New Zealand", ("WLG",)),
    # --- Norway ---
    "no-bergen": ("Bergen", "Norway", ("BGO",)),
    "no-oslo": ("Oslo", "Norway", ("OSL",)),
    "no-stavanger": ("Stavanger", "Norway", ("SVG",)),
    "no-tromso": ("Tromsø", "Norway", ("TOS",)),
    # --- Pakistan ---
    "pk-islamabad": ("Islamabad", "Pakistan", ("ISB",)),
    "pk-karachi": ("Karachi", "Pakistan", ("KHI",)),
    "pk-lahore": ("Lahore", "Pakistan", ("LHE",)),
    # --- Peru ---
    "pe-cusco": ("Cusco", "Peru", ("CUZ",)),
    "pe-lima": ("Lima", "Peru", ("LIM",)),
    # --- Philippines ---
    "ph-boracay": ("Boracay (Caticlan)", "Philippines", ("MPH",)),
    "ph-cebu": ("Cebu", "Philippines", ("CEB",)),
    "ph-clark": ("Clark", "Philippines", ("CRK",)),
    "ph-davao": ("Davao", "Philippines", ("DVO",)),
    "ph-manila": ("Manila", "Philippines", ("MNL",)),
    "ph-puerto-princesa": ("Puerto Princesa", "Philippines", ("PPS",)),
    # --- Poland ---
    "pl-gdansk": ("Gdańsk", "Poland", ("GDN",)),
    "pl-krakow": ("Kraków", "Poland", ("KRK",)),
    "pl-warsaw": ("Warsaw", "Poland", ("WAW", "WMI")),
    "pl-wroclaw": ("Wrocław", "Poland", ("WRO",)),
    # --- Portugal ---
    "pt-faro": ("Faro", "Portugal", ("FAO",)),
    "pt-funchal": ("Funchal", "Portugal", ("FNC",)),
    "pt-lisbon": ("Lisbon", "Portugal", ("LIS",)),
    "pt-porto": ("Porto", "Portugal", ("OPO",)),
    # --- Qatar ---
    "qa-doha": ("Doha", "Qatar", ("DOH",)),
    # --- Russia ---
    "ru-moscow": ("Moscow", "Russia", ("SVO", "DME", "VKO")),
    "ru-saint-petersburg": ("Saint Petersburg", "Russia", ("LED",)),
    # --- Saudi Arabia ---
    "sa-dammam": ("Dammam", "Saudi Arabia", ("DMM",)),
    "sa-jeddah": ("Jeddah", "Saudi Arabia", ("JED",)),
    "sa-medina": ("Medina", "Saudi Arabia", ("MED",)),
    "sa-riyadh": ("Riyadh", "Saudi Arabia", ("RUH",)),
    # --- Singapore ---
    "sg-singapore": ("Singapore", "Singapore", ("SIN",)),
    # --- South Africa ---
    "za-cape-town": ("Cape Town", "South Africa", ("CPT",)),
    "za-durban": ("Durban", "South Africa", ("DUR",)),
    "za-johannesburg": ("Johannesburg", "South Africa", ("JNB",)),
    # --- Spain ---
    "es-alicante": ("Alicante", "Spain", ("ALC",)),
    "es-barcelona": ("Barcelona", "Spain", ("BCN",)),
    "es-bilbao": ("Bilbao", "Spain", ("BIO",)),
    "es-madrid": ("Madrid", "Spain", ("MAD",)),
    "es-malaga": ("Malaga", "Spain", ("AGP",)),
    "es-palma": ("Palma de Mallorca", "Spain", ("PMI",)),
    "es-seville": ("Seville", "Spain", ("SVQ",)),
    "es-valencia": ("Valencia", "Spain", ("VLC",)),
    # --- Sri Lanka ---
    "lk-colombo": ("Colombo", "Sri Lanka", ("CMB",)),
    # --- Sweden ---
    "se-gothenburg": ("Gothenburg", "Sweden", ("GOT",)),
    "se-malmo": ("Malmö", "Sweden", ("MMX",)),
    "se-stockholm": ("Stockholm", "Sweden", ("ARN", "BMA")),
    # --- Switzerland ---
    "ch-basel": ("Basel", "Switzerland", ("BSL",)),
    "ch-bern": ("Bern", "Switzerland", ("BRN",)),
    "ch-geneva": ("Geneva", "Switzerland", ("GVA",)),
    "ch-zurich": ("Zurich", "Switzerland", ("ZRH",)),
    # --- Taiwan ---
    "tw-kaohsiung": ("Kaohsiung", "Taiwan", ("KHH",)),
    "tw-taichung": ("Taichung", "Taiwan", ("RMQ",)),
    "tw-taipei": ("Taipei", "Taiwan", ("TPE", "TSA")),
    # --- Thailand ---
    "th-bangkok": ("Bangkok", "Thailand", ("BKK", "DMK")),
    "th-chiang-mai": ("Chiang Mai", "Thailand", ("CNX",)),
    "th-hat-yai": ("Hat Yai", "Thailand", ("HDY",)),
    "th-koh-samui": ("Koh Samui", "Thailand", ("USM",)),
    "th-krabi": ("Krabi", "Thailand", ("KBV",)),
    "th-pattaya": ("Pattaya (U-Tapao)", "Thailand", ("UTP",)),
    "th-phuket": ("Phuket", "Thailand", ("HKT",)),
    # --- Türkiye ---
    "tr-ankara": ("Ankara", "Türkiye", ("ESB",)),
    "tr-antalya": ("Antalya", "Türkiye", ("AYT",)),
    "tr-istanbul": ("Istanbul", "Türkiye", ("IST", "SAW")),
    "tr-izmir": ("Izmir", "Türkiye", ("ADB",)),
    # --- United Arab Emirates ---
    "ae-abu-dhabi": ("Abu Dhabi", "United Arab Emirates", ("AUH",)),
    "ae-dubai": ("Dubai", "United Arab Emirates", ("DXB", "DWC")),
    "ae-sharjah": ("Sharjah", "United Arab Emirates", ("SHJ",)),
    # --- United Kingdom ---
    "gb-belfast": ("Belfast", "United Kingdom", ("BFS",)),
    "gb-birmingham": ("Birmingham", "United Kingdom", ("BHX",)),
    "gb-bristol": ("Bristol", "United Kingdom", ("BRS",)),
    "gb-edinburgh": ("Edinburgh", "United Kingdom", ("EDI",)),
    "gb-glasgow": ("Glasgow", "United Kingdom", ("GLA",)),
    "gb-london": ("London", "United Kingdom", ("LHR", "LGW", "STN", "LTN")),
    "gb-manchester": ("Manchester", "United Kingdom", ("MAN",)),
    "gb-newcastle": ("Newcastle", "United Kingdom", ("NCL",)),
    # --- United States ---
    "us-atlanta": ("Atlanta", "United States", ("ATL",)),
    "us-boston": ("Boston", "United States", ("BOS",)),
    "us-chicago": ("Chicago", "United States", ("ORD", "MDW")),
    "us-dallas": ("Dallas", "United States", ("DFW",)),
    "us-denver": ("Denver", "United States", ("DEN",)),
    "us-honolulu": ("Honolulu", "United States", ("HNL",)),
    "us-houston": ("Houston", "United States", ("IAH",)),
    "us-las-vegas": ("Las Vegas", "United States", ("LAS",)),
    "us-los-angeles": ("Los Angeles", "United States", ("LAX",)),
    "us-miami": ("Miami", "United States", ("MIA",)),
    "us-new-york": ("New York", "United States", ("JFK", "EWR", "LGA")),
    "us-orlando": ("Orlando", "United States", ("MCO",)),
    "us-san-diego": ("San Diego", "United States", ("SAN",)),
    "us-san-francisco": ("San Francisco", "United States", ("SFO",)),
    "us-seattle": ("Seattle", "United States", ("SEA",)),
    "us-washington": ("Washington, D.C.", "United States", ("IAD", "DCA")),
    # --- Vietnam ---
    "vn-da-nang": ("Da Nang", "Vietnam", ("DAD",)),
    "vn-hanoi": ("Hanoi", "Vietnam", ("HAN",)),
    "vn-ho-chi-minh-city": ("Ho Chi Minh City", "Vietnam", ("SGN",)),
    "vn-nha-trang": ("Nha Trang", "Vietnam", ("CXR",)),
    "vn-phu-quoc": ("Phu Quoc", "Vietnam", ("PQC",)),
}

CITIES: dict[str, City] = {
    slug: City(slug=slug, name=name, country=country, airports=airports)
    for slug, (name, country, airports) in _RAW.items()
}

# The city assumed when a request names a country but no city — the busiest
# international gateway, stated explicitly rather than inferred, because
# "first alphabetically" would pick Adelaide for Australia and Antwerp for
# Belgium. Callers that fall back to this MUST disclose the assumption to the
# traveller; see `airports.resolve_route`, which returns it as a note.
#
# This is the one place the old one-airport-per-country behaviour survives, and
# it is now a documented fallback for country-only API callers rather than the
# only thing the system can express.
PRIMARY_CITY: dict[str, str] = {
    "Argentina": "ar-buenos-aires",
    "Australia": "au-sydney",
    "Austria": "at-vienna",
    "Bangladesh": "bd-dhaka",
    "Belgium": "be-brussels",
    "Brazil": "br-sao-paulo",
    "Brunei": "bn-bandar-seri-begawan",
    "Cambodia": "kh-phnom-penh",
    "Canada": "ca-toronto",
    "Chile": "cl-santiago",
    "China": "cn-beijing",
    "Croatia": "hr-zagreb",
    "Czechia": "cz-prague",
    "Denmark": "dk-copenhagen",
    "Egypt": "eg-cairo",
    "Finland": "fi-helsinki",
    "France": "fr-paris",
    "Germany": "de-frankfurt",
    "Greece": "gr-athens",
    "Hungary": "hu-budapest",
    "Iceland": "is-reykjavik",
    "India": "in-delhi",
    "Indonesia": "id-jakarta",
    "Ireland": "ie-dublin",
    "Israel": "il-tel-aviv",
    "Italy": "it-rome",
    "Japan": "jp-tokyo",
    "Jordan": "jo-amman",
    "Kenya": "ke-nairobi",
    "Korea, South": "kr-seoul",
    "Laos": "la-vientiane",
    "Malaysia": "my-kuala-lumpur",
    "Maldives": "mv-male",
    "Mexico": "mx-mexico-city",
    "Morocco": "ma-casablanca",
    "Myanmar": "mm-yangon",
    "Nepal": "np-kathmandu",
    "Netherlands": "nl-amsterdam",
    "New Zealand": "nz-auckland",
    "Norway": "no-oslo",
    "Pakistan": "pk-karachi",
    "Peru": "pe-lima",
    "Philippines": "ph-manila",
    "Poland": "pl-warsaw",
    "Portugal": "pt-lisbon",
    "Qatar": "qa-doha",
    "Russia": "ru-moscow",
    "Saudi Arabia": "sa-riyadh",
    "Singapore": "sg-singapore",
    "South Africa": "za-johannesburg",
    "Spain": "es-madrid",
    "Sri Lanka": "lk-colombo",
    "Sweden": "se-stockholm",
    "Switzerland": "ch-zurich",
    "Taiwan": "tw-taipei",
    "Thailand": "th-bangkok",
    "Türkiye": "tr-istanbul",
    "United Arab Emirates": "ae-dubai",
    "United Kingdom": "gb-london",
    "United States": "us-new-york",
    "Vietnam": "vn-ho-chi-minh-city",
}

# country -> cities, built once at import. Insertion order in `_RAW` is already
# alphabetical by city within each country, so the form's <select> needs no
# further sorting.
def _index_by_country() -> dict[str, tuple[City, ...]]:
    grouped: dict[str, list[City]] = {}
    for city in CITIES.values():
        grouped.setdefault(city.country, []).append(city)
    return {country: tuple(cities) for country, cities in grouped.items()}


_BY_COUNTRY: dict[str, tuple[City, ...]] = _index_by_country()

# Every airport code known to this dataset. Used to sanity-check inventory
# against the places a traveller can actually select.
KNOWN_AIRPORTS: frozenset[str] = frozenset(
    code for city in CITIES.values() for code in city.airports
)


def countries_with_cities() -> tuple[str, ...]:
    """Countries that have at least one selectable city, alphabetically."""
    return tuple(sorted(_BY_COUNTRY))


def cities_for(country: str | None) -> tuple[City, ...]:
    """Selectable cities in `country`, or an empty tuple.

    Empty is a normal outcome, not an error: a country in `countries.py` with
    no mapped cities yet returns nothing, and the caller reports that as an
    unroutable request rather than guessing at an airport.
    """
    if not country:
        return ()
    return _BY_COUNTRY.get(country.strip(), ())


def primary_city(country: str | None) -> City | None:
    """The assumed city when a request names only a country.

    Returns None for a country with no mapped cities — the caller reports that
    as unroutable rather than substituting a neighbour.
    """
    if not country:
        return None
    return CITIES.get(PRIMARY_CITY.get(country.strip(), ""))


def city_by_slug(slug: str | None) -> City | None:
    """Look up a city by its stable key. The lookup downstream databases use."""
    if not slug:
        return None
    return CITIES.get(slug.strip().lower())


def city_by_name(name: str | None) -> City | None:
    """Resolve a city from its name alone, and with it the country it is in.

    The reverse of `find_city`, which needs the country to search within. Intake
    needs this direction because a traveller writes "I want to go to Tokyo" and
    has thereby named the country too — asking them for it is asking a question
    the dataset can already answer.

    Returns None when the name matches MORE than one city, not the first hit.
    Every name in today's dataset is unique, so this guard costs nothing now;
    it exists because the dataset grows and city names genuinely collide
    (Springfield, San Jose, Tripoli). Resolving an ambiguous name by position
    would work today and silently pick the wrong country later, where returning
    None simply means intake asks for the country as it always did.
    """
    if not name:
        return None
    target = name.strip().casefold()
    matches = [city for city in CITIES.values() if city.name.strip().casefold() == target]
    return matches[0] if len(matches) == 1 else None


def find_city(country: str | None, name: str | None) -> City | None:
    """Resolve a display name within a country, case-insensitively.

    Needed because the intake form posts the name a human picked, not the slug.
    Prefer `city_by_slug` anywhere a stable identifier is available.
    """
    if not name:
        return None
    target = name.strip().casefold()
    for city in cities_for(country):
        if city.name.casefold() == target:
            return city
    return None


def airports_for(country: str | None, city_name: str | None) -> tuple[str, ...]:
    """Airports serving a city, primary first; empty when it cannot be resolved.

    This is the function flight inventory search should call. Returning every
    airport rather than just the primary is the whole point of asking for a
    city: a traveller who picks Tokyo should see Haneda fares as well as Narita.
    """
    city = find_city(country, city_name)
    return city.airports if city else ()


def city_options() -> dict[str, list[dict[str, str]]]:
    """The country -> cities map, JSON-serialisable, for the intake form.

    Shape is `{"Japan": [{"slug": "jp-tokyo", "name": "Tokyo",
    "label": "Tokyo (NRT/HND)"}, ...]}` — the template embeds this so the
    dependent <select> needs no extra HTTP round trip.
    """
    return {
        country: [
            {"slug": city.slug, "name": city.name, "label": city.label}
            for city in cities
        ]
        for country, cities in _BY_COUNTRY.items()
    }
