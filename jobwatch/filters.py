"""Title and location filters. Keyword lists live in config.yaml so they are easy to tune."""
import re
import unicodedata
from typing import Iterable, Optional, Tuple

# --- Location evidence, strongest first -------------------------------------------------------
# A: clearly the US
US_STATES = [
    "alabama", "alaska", "arizona", "arkansas", "california", "colorado", "connecticut", "delaware",
    "florida", "georgia", "hawaii", "idaho", "illinois", "indiana", "iowa", "kansas", "kentucky",
    "louisiana", "maine", "maryland", "massachusetts", "michigan", "minnesota", "mississippi",
    "missouri", "montana", "nebraska", "nevada", "new hampshire", "new jersey", "new mexico",
    "new york", "north carolina", "north dakota", "ohio", "oklahoma", "oregon", "pennsylvania",
    "rhode island", "south carolina", "south dakota", "tennessee", "texas", "utah", "vermont",
    "virginia", "washington", "west virginia", "wisconsin", "wyoming", "district of columbia",
]
US_TERMS = ["united states", "united states of america", "usa", "us"]

# B: clearly another country or region
NON_US_COUNTRIES = [
    "india", "canada", "united kingdom", "uk", "england", "scotland", "wales", "northern ireland",
    "ireland", "germany", "france", "netherlands", "belgium", "luxembourg", "switzerland", "austria",
    "italy", "spain", "portugal", "poland", "czech republic", "czechia", "slovakia", "hungary", "romania",
    "bulgaria", "serbia", "croatia", "greece", "turkey", "turkiye", "ukraine", "lithuania", "latvia",
    "estonia", "sweden", "norway", "denmark", "finland", "iceland", "israel", "uae", "united arab emirates",
    "saudi arabia", "qatar", "egypt", "kenya", "nigeria", "south africa", "morocco", "singapore",
    "malaysia", "indonesia", "thailand", "vietnam", "philippines", "japan", "china", "hong kong",
    "taiwan", "korea", "south korea", "australia", "new zealand", "brazil", "mexico", "argentina",
    "chile", "colombia", "peru", "costa rica", "uruguay", "ontario", "quebec", "british columbia",
    "alberta", "manitoba", "saskatchewan", "nova scotia", "emea", "apac", "latam", "europe", "asia", "africa",
]
COUNTRY_CODES = ("IND|GBR|CAN|DEU|FRA|IRL|NLD|BEL|CHE|AUT|ITA|ESP|PRT|POL|CZE|HUN|ROU|BGR|SRB|UKR|SWE|NOR|"
                 "DNK|FIN|ISR|ARE|SGP|MYS|PHL|VNM|JPN|CHN|HKG|TWN|KOR|AUS|NZL|BRA|MEX|ARG|CHL|COL|PER|CRI|KEN|ZAF|TLV")
CA_PROVINCES = "ON|BC|QC|AB|MB|SK|NS|NB|NL|PE"

# C: points to the US (cities, state codes, North America)
US_CITIES = [
    "san francisco", "bay area", "silicon valley", "nyc", "new york city", "seattle", "boston", "austin",
    "chicago", "los angeles", "san diego", "san jose", "atlanta", "denver", "dallas", "houston", "miami",
    "philadelphia", "pittsburgh", "portland", "mclean", "plano", "irving", "charlotte", "raleigh", "durham",
    "menlo park", "palo alto", "mountain view", "sunnyvale", "santa clara", "redwood city", "san mateo",
    "redmond", "bellevue", "kirkland", "minneapolis", "detroit", "columbus", "nashville", "salt lake city",
    "tampa", "st. louis", "saint louis", "brooklyn", "manhattan", "jersey city", "hoboken", "richmond",
    "north america", "namer", "amer", "americas",
]
STATE_ABBR = ("AL|AK|AZ|AR|CA|CO|CT|DE|FL|GA|HI|ID|IL|IN|IA|KS|KY|LA|ME|MD|MA|MI|MN|MS|MO|MT|NE|NV|NH|"
              "NJ|NM|NY|NC|ND|OH|OK|OR|PA|RI|SC|SD|TN|TX|UT|VT|VA|WA|WV|WI|WY|DC")

# D: points to another country (cities)
NON_US_CITIES = [
    "london", "manchester", "edinburgh", "glasgow", "belfast", "cardiff", "leeds", "bristol", "nottingham",
    "knutsford", "northampton", "toronto", "vancouver", "montreal", "ottawa", "calgary", "bangalore",
    "bengaluru", "hyderabad", "pune", "chennai", "mumbai", "delhi", "new delhi", "gurgaon", "gurugram",
    "noida", "kolkata", "ahmedabad", "berlin", "munich", "hamburg", "frankfurt", "paris", "amsterdam",
    "warsaw", "krakow", "wroclaw", "prague", "budapest", "bucharest", "cluj", "sofia", "belgrade", "madrid",
    "barcelona", "lisbon", "porto", "milan", "zurich", "geneva", "stockholm", "copenhagen", "oslo",
    "helsinki", "tel aviv", "haifa", "dubai", "abu dhabi", "nairobi", "lagos", "cape town", "johannesburg",
    "cairo", "kuala lumpur", "jakarta", "bangkok", "manila", "ho chi minh", "hanoi", "tokyo", "osaka",
    "seoul", "beijing", "shanghai", "shenzhen", "taipei", "sydney", "melbourne", "auckland", "sao paulo",
    "mexico city", "guadalajara", "monterrey", "buenos aires", "bogota", "medellin", "lima", "dublin",
    # added after the dashboard's checks found leaks (2026-09-30); only names that aren't also notable US places
    "malmo", "gothenburg", "reykjavik", "brussels", "antwerp", "budapest", "vienna", "brno", "tallinn", "vilnius",
    "riga", "rotterdam", "eindhoven", "the hague", "stuttgart", "cologne", "dusseldorf", "leipzig", "lyon",
    "toulouse", "basel", "lausanne", "turin", "bologna", "farringdon", "indore", "kochi", "cochin",
    "bhubaneswar", "coimbatore", "thiruvananthapuram", "trivandrum", "jaipur", "chandigarh", "nagpur",
    "vadodara", "mysuru", "visakhapatnam", "hsinchu", "suzhou", "hangzhou", "chengdu", "penang", "cebu",
    "curitiba", "campinas", "florianopolis", "belo horizonte", "porto alegre", "montevideo", "herzliya",
    "jerusalem", "edmonton", "winnipeg", "kitchener",
]
# Foreign cities that share a name with a US city often listed with a state code ("Dublin, OH", "Vancouver, WA").
# For these, a state code still means the US.
DUAL_NAME_CITIES = ["dublin", "vancouver", "toronto", "london", "paris", "manchester", "bristol", "leeds", "belfast",
                    "lima", "glasgow", "edinburgh", "berlin", "hamburg"]

# Titles with German/French-style gender markers are European postings.
GENDER_MARKER_RE = re.compile(r"\((?:all genders|m/w/d|m/f/d|w/m/d|f/m/d|m/f/x|h/f|f/h|m/w/x)\)", re.I)

US_COUNTRY = {"US", "USA", "UNITED STATES", "UNITED STATES OF AMERICA"}


def _words_re(words):
    alts = "|".join(re.escape(w) for w in sorted(set(words), key=len, reverse=True))
    return re.compile(r"(?<![a-z0-9])(" + alts + r")(?![a-z0-9])")


A_RE = _words_re(US_STATES + US_TERMS)
B_RE = _words_re(NON_US_COUNTRIES)
C_RE = _words_re(US_CITIES)
D_RE = _words_re(NON_US_CITIES)
DUAL_RE = _words_re(DUAL_NAME_CITIES)
CODE_RE = re.compile(r"(?<![A-Za-z])(" + COUNTRY_CODES + r")(?![A-Za-z])")
STATE_ABBR_RE = re.compile(r",\s*(" + STATE_ABBR + r")\b")
PROVINCE_RE = re.compile(r",\s*(" + CA_PROVINCES + r")\b")


def _fold_accents(text: str) -> str:
    """"São Paulo" -> "Sao Paulo", "Malmö" -> "Malmo", so place lists can be plain ASCII."""
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))


def _normalize(text: str) -> str:
    """Lower-cased, accent-free text with a few common abbreviations expanded."""
    t = _fold_accents(text or "")
    t = re.sub(r",\s*(" + CA_PROVINCES + r"),\s*CA\b", r", \1, Canada", t)   # "Toronto, ON, CA"
    t = re.sub(r",\s*Eng\b", ", England", t)                                  # "Nottingham, Eng"
    t = t.lower().replace("u.s.a.", "usa").replace("u.s.", "us")
    return t


class LocationFilter:
    """Decides whether a job is (or may be) in the US.

    Order of evidence: US country/state names win; a named foreign country loses unless a US city or
    state code is also listed (multi-location jobs); then US cities; then the job board's country field;
    then foreign cities. Text with no clues at all ("Remote", "5 Locations") counts as US unless the
    title names another country.
    """

    def __init__(self, extra_non_us: Iterable[str] = ()):
        extra = [w.strip().lower() for w in extra_non_us or [] if w and w.strip()]
        self.extra_re = _words_re(extra) if extra else None

    def _foreign(self, t: str, raw: str) -> bool:
        return bool(B_RE.search(t) or CODE_RE.search(raw) or PROVINCE_RE.search(raw))

    def _foreign_city(self, t: str) -> bool:
        return bool(D_RE.search(t) or (self.extra_re and self.extra_re.search(t)))

    def is_us(self, location: str, country: Optional[str] = None, title: str = "") -> bool:
        t = _normalize(location)
        raw_cased = re.sub(r",\s*(" + CA_PROVINCES + r"),\s*CA\b", r", \1, Canada", location or "")
        us_city = bool(C_RE.search(t) or STATE_ABBR_RE.search(raw_cased))
        if A_RE.search(t):
            return True
        if self._foreign(t, raw_cased):
            return us_city
        if us_city:
            # ", DE" / ", OR" can be a country code after a foreign city ("Munich, DE", "Budapest, OR"); a
            # state code only wins when it isn't next to a known foreign city or a US city is named too.
            return bool(C_RE.search(t) or DUAL_RE.search(t)) or not self._foreign_city(t)
        if country:
            return country.strip().upper() in US_COUNTRY
        if self._foreign_city(t):
            return False
        # No location clues: fall back to the title.
        tt = _normalize(title)
        if GENDER_MARKER_RE.search(title or "") or B_RE.search(tt) or D_RE.search(tt) or CODE_RE.search(title or ""):
            return False
        return True


_default = LocationFilter()


def is_us_location(location: str, country: Optional[str] = None, title: str = "") -> bool:
    return _default.is_us(location, country, title)


# --- Titles ---------------------------------------------------------------------------------------
def compile_keywords(words: Iterable[str]) -> Optional[re.Pattern]:
    words = [w.strip().lower() for w in words if w and w.strip()]
    if not words:
        return None
    alts = "|".join(re.escape(w) for w in sorted(set(words), key=len, reverse=True))
    return re.compile(r"(?<![a-z0-9])(" + alts + r")(?![a-z0-9])")


class TitleFilter:
    def __init__(self, cfg: dict):
        self.tracks = {name: compile_keywords(words) for name, words in (cfg.get("tracks") or {}).items()}
        self.exclude = compile_keywords(cfg.get("exclude") or [])
        self.require = compile_keywords(cfg.get("require_any") or [])

    def classify(self, title: str) -> Tuple[str, ...]:
        t = (title or "").lower()
        if self.exclude and self.exclude.search(t):
            return ()
        if self.require and not self.require.search(t):
            return ()
        return tuple(name for name, rx in self.tracks.items() if rx and rx.search(t))
