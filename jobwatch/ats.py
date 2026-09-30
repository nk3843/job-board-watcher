"""Fetch open jobs from public job-board APIs and normalize them into Job objects.

Supported systems and what each gives us for the posting date:
  greenhouse       first_published (ISO)        boards-api.greenhouse.io
  lever            createdAt (epoch ms)         api.lever.co
  ashby            publishedAt (ISO)            api.ashbyhq.com
  smartrecruiters  releasedDate (ISO)           api.smartrecruiters.com
  workday          "Posted Today/Yesterday/N Days Ago" (day precision)
"""
import json
import re
from datetime import datetime, timedelta, timezone
from typing import List, Optional
from urllib.parse import parse_qs, quote, unquote, urlparse

from .http import FetchError, Http
from .models import Company, Job


def parse_iso(value) -> Optional[datetime]:
    if not value or not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


# ---------------------------------------------------------------- Greenhouse
def fetch_greenhouse(http: Http, company: Company, **_) -> List[Job]:
    url = f"https://boards-api.greenhouse.io/v1/boards/{quote(company.board)}/jobs"
    data = http.get_json(url)
    if not isinstance(data, dict) or "jobs" not in data:
        raise FetchError(f"Unexpected Greenhouse response for {company.board}")
    jobs = []
    for j in data["jobs"]:
        jobs.append(Job(
            company=company.name, ats="greenhouse", job_id=str(j.get("id")),
            title=(j.get("title") or "").strip(),
            location=((j.get("location") or {}).get("name") or "").strip(),
            url=j.get("absolute_url") or "",
            posted_at=parse_iso(j.get("first_published")),
        ))
    return jobs


# ---------------------------------------------------------------- Lever
def fetch_lever(http: Http, company: Company, **_) -> List[Job]:
    url = f"https://api.lever.co/v0/postings/{quote(company.board)}"
    data = http.get_json(url, params={"mode": "json"})
    if not isinstance(data, list):
        raise FetchError(f"Unexpected Lever response for {company.board}")
    jobs = []
    for j in data:
        cats = j.get("categories") or {}
        locs = cats.get("allLocations") or [cats.get("location")]
        location = "; ".join(l for l in locs if l)
        created = j.get("createdAt")
        posted = datetime.fromtimestamp(created / 1000, tz=timezone.utc) if isinstance(created, (int, float)) else None
        jobs.append(Job(
            company=company.name, ats="lever", job_id=str(j.get("id")),
            title=(j.get("text") or "").strip(), location=location,
            url=j.get("hostedUrl") or "", posted_at=posted, country=j.get("country") or None,
        ))
    return jobs


# ---------------------------------------------------------------- Ashby
def fetch_ashby(http: Http, company: Company, **_) -> List[Job]:
    url = f"https://api.ashbyhq.com/posting-api/job-board/{quote(company.board)}"
    data = http.get_json(url)
    if not isinstance(data, dict) or not isinstance(data.get("jobs"), list):
        raise FetchError(f"Unexpected Ashby response for {company.board}")
    jobs = []
    for j in data["jobs"]:
        if j.get("isListed") is False:
            continue
        locs = [j.get("location")]
        for extra in j.get("secondaryLocations") or []:
            if isinstance(extra, dict):
                locs.append(extra.get("location"))
        location = "; ".join(l for l in locs if l)
        if j.get("isRemote") and "remote" not in location.lower():
            location = f"{location} (Remote)" if location else "Remote"
        country = (((j.get("address") or {}).get("postalAddress") or {}).get("addressCountry")) or None
        jobs.append(Job(
            company=company.name, ats="ashby", job_id=str(j.get("id")),
            title=(j.get("title") or "").strip(), location=location,
            url=j.get("jobUrl") or f"https://jobs.ashbyhq.com/{company.board}/{j.get('id')}",
            posted_at=parse_iso(j.get("publishedAt")), country=country,
        ))
    return jobs


# ---------------------------------------------------------------- SmartRecruiters
def fetch_smartrecruiters(http: Http, company: Company, max_jobs: int = 2000, **_) -> List[Job]:
    url = f"https://api.smartrecruiters.com/v1/companies/{quote(company.board)}/postings"
    jobs, offset = [], 0
    while True:
        data = http.get_json(url, params={"limit": 100, "offset": offset})
        if not isinstance(data, dict):
            raise FetchError(f"Unexpected SmartRecruiters response for {company.board}")
        content = data.get("content") or []
        for j in content:
            loc = j.get("location") or {}
            location = ", ".join(p for p in (loc.get("city"), loc.get("region")) if p)
            if loc.get("remote"):
                location = f"{location} (Remote)" if location else "Remote"
            jobs.append(Job(
                company=company.name, ats="smartrecruiters", job_id=str(j.get("id")),
                title=(j.get("name") or "").strip(), location=location,
                url=f"https://jobs.smartrecruiters.com/{company.board}/{j.get('id')}",
                posted_at=parse_iso(j.get("releasedDate")),
                country=(loc.get("country") or "").upper() or None,
            ))
        offset += len(content)
        if not content or offset >= (data.get("totalFound") or 0) or offset >= max_jobs:
            break
    return jobs


# ---------------------------------------------------------------- Workday
WORKDAY_URL = re.compile(
    r"https?://(?P<host>(?P<tenant>[A-Za-z0-9-]+)\.(?P<wd>[Ww][Dd]\d+)\.[Mm]yworkdayjobs\.com)/"
    r"(?:(?P<locale>[a-z]{2}-[A-Z]{2})/)?(?P<site>[A-Za-z0-9_-]+)"
)


def parse_workday_url(url: str):
    m = WORKDAY_URL.search(url.strip())
    if not m or m.group("site").lower() == "wday":
        raise ValueError(f"Not a Workday careers URL: {url}")
    return m.group("host").lower(), m.group("tenant").lower(), m.group("site")


def parse_workday_posted(text: Optional[str], now: datetime) -> Optional[datetime]:
    if not text:
        return None
    t = text.lower()
    if "today" in t:
        return now
    if "yesterday" in t:
        return now - timedelta(days=1)
    m = re.search(r"(\d+)\+?\s+days?\s+ago", t)
    if m:
        return now - timedelta(days=int(m.group(1)))
    return None


MULTI_LOCATION = re.compile(r"^\d+\s+locations?$", re.I)


def workday_path_location(path: str) -> str:
    """Workday job links carry the primary location: /job/<Location>/<Title>_<ReqId>."""
    m = re.match(r"/job/([^/]+)/", path or "")
    if not m:
        return ""
    return re.sub(r"-+", " ", unquote(m.group(1))).strip()


def workday_location(locations_text: str, path: str) -> str:
    """Use the location from the link when Workday's own text is blank or just 'N Locations'."""
    text = (locations_text or "").strip()
    if text and not MULTI_LOCATION.match(text):
        return text
    primary = workday_path_location(path)
    if not primary:
        return text
    return f"{text} (primary: {primary})" if text else primary


def fetch_workday(http: Http, company: Company, workday_queries=("engineer",), workday_max_pages: int = 5, **_) -> List[Job]:
    host, tenant, site = parse_workday_url(company.board)
    api = f"https://{host}/wday/cxs/{tenant}/{site}/jobs"
    now = datetime.now(timezone.utc)
    jobs, seen = [], set()
    for query in workday_queries:
        for page in range(workday_max_pages):
            data = http.post_json(api, {"appliedFacets": {}, "limit": 20, "offset": page * 20, "searchText": query})
            if not isinstance(data, dict):
                raise FetchError(f"Unexpected Workday response for {company.board}")
            postings = data.get("jobPostings") or []
            for j in postings:
                path = j.get("externalPath")
                if not path or path in seen:
                    continue
                seen.add(path)
                jobs.append(Job(
                    company=company.name, ats="workday", job_id=path,
                    title=(j.get("title") or "").strip(), location=workday_location(j.get("locationsText"), path),
                    url=f"https://{host}/{site}{path}", posted_at=parse_workday_posted(j.get("postedOn"), now),
                ))
            if len(postings) < 20:
                break
    return jobs


FETCHERS = {
    "greenhouse": fetch_greenhouse,
    "lever": fetch_lever,
    "ashby": fetch_ashby,
    "smartrecruiters": fetch_smartrecruiters,
    "workday": fetch_workday,
}


def fetch_company(http: Http, company: Company, **options) -> List[Job]:
    fetcher = FETCHERS.get(company.ats)
    if fetcher is None:
        raise FetchError(f"Unsupported ATS '{company.ats}' for {company.name}")
    return fetcher(http, company, **options)


# ---------------------------------------------------------------- Apple (jobs.apple.com)
# Apple's search page embeds its results as JSON (window.__staticRouterHydrationData).
# Each result has positionId, postingTitle, transformedPostingTitle, postDateInGMT and
# locations[{name, countryName}]. The board value is Apple's location slug, e.g. united-states-USA.
APPLE_HYDRATION = re.compile(r"window\.__staticRouterHydrationData\s*=\s*")


def _js_string_literal(s: str) -> Optional[str]:
    """Return the JS string literal at the start of s (quotes included), or None."""
    if not s or s[0] not in "\"'":
        return None
    quote_char, i, escaped = s[0], 1, False
    while i < len(s):
        c = s[i]
        if escaped:
            escaped = False
        elif c == "\\":
            escaped = True
        elif c == quote_char:
            return s[:i + 1]
        i += 1
    return None


def extract_apple_data(html: str):
    m = APPLE_HYDRATION.search(html or "")
    if not m:
        return None
    rest = html[m.end():].lstrip()
    try:
        if rest.startswith("JSON.parse("):
            literal = _js_string_literal(rest[len("JSON.parse("):].lstrip())
            if literal is None:
                return None
            if literal[0] == "'":
                literal = '"' + literal[1:-1].replace("\\'", "'").replace('"', '\\"') + '"'
            return json.loads(json.loads(literal))
        obj, _ = json.JSONDecoder().raw_decode(rest)
        return obj
    except ValueError:
        return None


def find_apple_results(node) -> list:
    """Largest list of dicts that look like Apple job results, anywhere in the page data."""
    best = []
    stack = [node]
    while stack:
        cur = stack.pop()
        if isinstance(cur, list):
            if cur and all(isinstance(x, dict) for x in cur) and any("positionId" in x and "postingTitle" in x for x in cur):
                if len(cur) > len(best):
                    best = cur
                continue
            stack.extend(cur)
        elif isinstance(cur, dict):
            stack.extend(cur.values())
    return best


def _apple_location(locs) -> str:
    parts = []
    for loc in locs or []:
        if isinstance(loc, str):
            parts.append(loc)
        elif isinstance(loc, dict):
            bits = [b for b in (loc.get("name"), loc.get("countryName")) if b]
            if bits:
                parts.append(", ".join(bits))
    return "; ".join(parts)


def fetch_apple(http: Http, company: Company, apple_max_pages: int = 15, **_) -> List[Job]:
    location = company.board or "united-states-USA"
    jobs, seen = [], set()
    for page in range(1, apple_max_pages + 1):
        url = f"https://jobs.apple.com/en-us/search?location={quote(location)}&sort=newest&page={page}"
        _, html = http.get_text(url)
        data = extract_apple_data(html)
        if data is None:
            if page == 1:
                raise FetchError("Apple's page format changed (no embedded job data found)")
            break
        added = 0
        for r in find_apple_results(data):
            pid = str(r.get("positionId") or r.get("id") or "")
            if not pid or pid in seen:
                continue
            seen.add(pid)
            added += 1
            slug = r.get("transformedPostingTitle") or ""
            jobs.append(Job(
                company=company.name, ats="apple", job_id=pid, title=(r.get("postingTitle") or "").strip(),
                location=_apple_location(r.get("locations")),
                url=f"https://jobs.apple.com/en-us/details/{pid}/{slug}" if slug else f"https://jobs.apple.com/en-us/details/{pid}",
                posted_at=parse_iso(r.get("postDateInGMT")),
            ))
        if added == 0:          # empty page, or Apple repeating the last page
            break
    if not jobs:
        raise FetchError("Apple returned no jobs (the page format may have changed)")
    return jobs


# ---------------------------------------------------------------- Eightfold (Microsoft, Qualcomm, ...)
# Board value: the careers host plus domain (and optional server-side location), e.g.
#   https://apply.careers.microsoft.com/?domain=microsoft.com&location=United%20States
# Eightfold returns at most 10 positions per request.
EIGHTFOLD_PAGE = 10
_EF_TITLE_KEYS = ("name", "title", "positionTitle", "postingTitle")
_EF_DATE_KEYS = ("postedTs", "t_create", "creationTs", "postedDate", "createdTs")


def parse_eightfold_board(board: str):
    u = urlparse(board if "://" in board else "https://" + board)
    q = parse_qs(u.query)
    domain = (q.get("domain") or [""])[0]
    if not u.netloc or not domain:
        raise ValueError(f"Eightfold board needs a host and ?domain=...: {board}")
    return u.netloc.lower(), domain, (q.get("location") or [""])[0]


def _ef_time(value) -> Optional[datetime]:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)) and value > 0:
        return datetime.fromtimestamp(value / 1000 if value > 1e12 else value, tz=timezone.utc)
    if isinstance(value, str) and value.strip():
        v = value.strip()
        return _ef_time(float(v)) if v.replace(".", "", 1).isdigit() else parse_iso(v)
    return None


def _ef_location(p: dict) -> str:
    raw = p.get("locations") or p.get("standardizedLocations") or []
    names = []
    for loc in raw if isinstance(raw, list) else [raw]:
        if isinstance(loc, str):
            names.append(loc)
        elif isinstance(loc, dict):
            names.append(loc.get("name") or loc.get("location") or
                         ", ".join(str(x) for x in (loc.get("city"), loc.get("state"), loc.get("country")) if x))
    if not any(names) and isinstance(p.get("location"), str):
        names = [p["location"]]
    return "; ".join(n for n in names if n)


def _ef_get(http: Http, url: str, params: dict) -> dict:
    try:
        data = http.get_json(url, params=params)
    except FetchError:
        if "sort_by" not in params:
            raise
        data = http.get_json(url, params={k: v for k, v in params.items() if k != "sort_by"})
    if isinstance(data, dict) and "positions" not in data and isinstance(data.get("data"), dict):
        data = data["data"]
    if not isinstance(data, dict) or not isinstance(data.get("positions"), list):
        raise FetchError(f"Unexpected Eightfold response from {url}")
    return data


def _ef_jobs(http, company, endpoint, host, domain, location, queries, max_pages) -> List[Job]:
    jobs, seen = [], set()
    for query in queries:
        for page in range(max_pages):
            params = {"domain": domain, "start": page * EIGHTFOLD_PAGE, "num": EIGHTFOLD_PAGE,
                      "query": query, "sort_by": "timestamp"}
            if location:
                params["location"] = location
            positions = _ef_get(http, endpoint, params)["positions"]
            if positions and not any(any(k in p for k in _EF_TITLE_KEYS) for p in positions if isinstance(p, dict)):
                keys = sorted(positions[0].keys())[:20] if isinstance(positions[0], dict) else []
                raise FetchError(f"Eightfold fields changed; position keys are now {keys}")
            for p in positions:
                if not isinstance(p, dict):
                    continue
                pid = str(p.get("id") or p.get("positionId") or "")
                if not pid or pid in seen:
                    continue
                seen.add(pid)
                title = next((p[k] for k in _EF_TITLE_KEYS if p.get(k)), "")
                url = p.get("canonicalPositionUrl") or p.get("positionUrl") or p.get("url") or ""
                if url.startswith("/"):
                    url = f"https://{host}{url}"
                posted = next((t for t in (_ef_time(p.get(k)) for k in _EF_DATE_KEYS) if t), None)
                jobs.append(Job(company=company.name, ats="eightfold", job_id=pid, title=str(title).strip(),
                                location=_ef_location(p), url=url or f"https://{host}/careers/job/{pid}",
                                posted_at=posted))
            if len(positions) < EIGHTFOLD_PAGE:
                break
    return jobs


def fetch_eightfold(http: Http, company: Company, workday_queries=("engineer",), eightfold_max_pages: int = 5, **_) -> List[Job]:
    host, domain, location = parse_eightfold_board(company.board)
    last_error = None
    # Newer career sites use /api/pcsx/search; older ones /api/apply/v2/jobs.
    for endpoint in (f"https://{host}/api/pcsx/search", f"https://{host}/api/apply/v2/jobs"):
        try:
            jobs = _ef_jobs(http, company, endpoint, host, domain, location, workday_queries, eightfold_max_pages)
        except FetchError as e:
            last_error = e
            continue
        if jobs:
            return jobs
        last_error = FetchError(f"Eightfold returned no jobs from {endpoint} (check domain/location)")
    raise last_error


FETCHERS["apple"] = fetch_apple
FETCHERS["eightfold"] = fetch_eightfold
