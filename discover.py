#!/usr/bin/env python3
"""Find which job board (Greenhouse, Lever, Ashby, SmartRecruiters, Workday) each company uses.

Reads companies_source.csv (name, list, priority, careers_url) and writes:
  companies.yaml             companies the daily script can watch   <- review entries marked `check: true`
  needs_manual_alerts.csv    companies on other systems             <- set up alerts on their careers sites
  discover_report.md         summary

How it works, per company:
  1. Open the careers page and look for links to a supported job board.
  2. If none is found, try likely board names on each system's public API and keep a match only when
     the board's own company name matches (Greenhouse, SmartRecruiters) or the name is an exact slug
     match (Lever, Ashby; these are marked `check: true` so you can confirm them).

Usage:
  python discover.py              # only companies not already in companies.yaml
  python discover.py --refresh    # redo everything
"""
import argparse
import csv
import os
import re
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote, unquote

import yaml

from jobwatch.ats import WORKDAY_URL
from jobwatch.http import FetchError, Http

ATS_ORDER = ["greenhouse", "ashby", "lever", "smartrecruiters", "workday"]

PAGE_PATTERNS = [
    ("greenhouse", re.compile(r"(?:boards|job-boards)\.greenhouse\.io/(?:embed/job_(?:board|app)(?:/js)?\?for=)?([A-Za-z0-9_-]+)", re.I)),
    ("greenhouse", re.compile(r"boards-api\.greenhouse\.io/v1/boards/([A-Za-z0-9_-]+)", re.I)),
    ("ashby", re.compile(r"jobs\.ashbyhq\.com/([A-Za-z0-9_.%-]+)", re.I)),
    ("ashby", re.compile(r"api\.ashbyhq\.com/posting-api/job-board/([A-Za-z0-9_.%-]+)", re.I)),
    ("lever", re.compile(r"jobs\.lever\.co/([A-Za-z0-9_.-]+)", re.I)),
    ("smartrecruiters", re.compile(r"(?:jobs|careers)\.smartrecruiters\.com/([A-Za-z0-9_-]+)", re.I)),
]
IGNORE_IDS = {"embed", "js", "api", "v1", "jobs", "job", "careers", "search", "wday", "static", "assets", "images", "css"}

# Extra board names worth trying when the company name doesn't map cleanly to a slug.
HINTS = {
    "Anysphere (Cursor)": ["cursor", "anysphere"],
    "Block (Square, Cash App)": ["block", "squareup"],
    "CoreWeave (incl. Weights & Biases)": ["coreweave"],
    "Scale AI": ["scaleai"],
    "Character.AI": ["character", "characterai"],
    "incident.io": ["incident", "incidentio"],
    "Hudson River Trading": ["hudsonrivertrading", "wehrtyou"],
    "The New York Times": ["thenewyorktimes", "nytimes"],
    "D. E. Shaw": ["deshaw"],
    "TikTok / ByteDance (US)": ["tiktok", "bytedance"],
}

STOP = {"inc", "corp", "corporation", "co", "company", "the", "llc", "ltd", "group", "holdings",
        "technologies", "technology", "labs", "systems", "us", "usa", "incl", "and"}


def name_tokens(s: str):
    s = re.sub(r"\(.*?\)", " ", (s or "").lower()).replace("&", " and ")
    return [t for t in re.findall(r"[a-z0-9]+", s) if t not in STOP]


def _similar_one(a: str, b: str) -> bool:
    ta, tb = name_tokens(a), name_tokens(b)
    if not ta or not tb:
        return False
    if "".join(ta) == "".join(tb):
        return True
    sa, sb = set(ta), set(tb)
    inter = sa & sb
    return len("".join(inter)) >= 3 and len(inter) / len(sa | sb) >= 0.6


def name_variants(name: str):
    """'Anysphere (Cursor)' -> ['Anysphere (Cursor)', 'Cursor']; 'Amazon / AWS' -> [..., 'Amazon', 'AWS']."""
    variants = [name]
    for paren in re.findall(r"\((.*?)\)", name):
        if not paren.lower().startswith("incl"):
            variants += [p.strip() for p in paren.split(",") if p.strip()]
    base = re.sub(r"\(.*?\)", "", name)
    if " / " in base:
        variants += [p.strip() for p in base.split(" / ") if p.strip()]
    return variants


def similar(board_name: str, company_name: str) -> bool:
    return any(_similar_one(board_name, v) for v in name_variants(company_name))


def slug_candidates(name: str):
    """Returns (strict, loose). Strict slugs may be tried on any board; loose ones only where the
    board reports its company name, so a wrong match can be rejected."""
    base = re.sub(r"\(.*?\)", "", name).split(" / ")[0].strip()
    lower = base.lower().replace("&", "and")
    strict = [re.sub(r"[^a-z0-9]", "", lower), re.sub(r"[^a-z0-9]+", "-", lower).strip("-")]
    for paren in re.findall(r"\((.*?)\)", name):
        if not paren.lower().startswith("incl"):
            strict += [re.sub(r"[^a-z0-9]", "", p.lower()) for p in paren.split(",")]
    strict += HINTS.get(name, [])
    words = re.findall(r"[a-z0-9]+", lower)
    loose = [words[0]] if len(words) > 1 and len(words[0]) >= 4 else []
    seen, out_strict = set(), []
    for s in strict:
        if s and s not in seen:
            seen.add(s)
            out_strict.append(s)
    return out_strict, [s for s in loose if s not in seen]


def extract_from_page(final_url: str, html: str):
    """Most frequently linked supported board on the page, as (ats, board) or None."""
    text = f"{final_url}\n{html}"
    counts = Counter()
    for ats, rx in PAGE_PATTERNS:
        for m in rx.finditer(text):
            ident = unquote(m.group(1)).strip().rstrip(".")
            if ident and ident.lower() not in IGNORE_IDS:
                counts[(ats, ident)] += 1
    for m in WORKDAY_URL.finditer(text):
        site = m.group("site")
        if site.lower() in IGNORE_IDS:
            continue
        counts[("workday", f"https://{m.group('host').lower()}/{site}")] += 1
    if not counts:
        return None
    return max(counts.items(), key=lambda kv: (kv[1], -ATS_ORDER.index(kv[0][0])))[0]


def check_board(http: Http, ats: str, board: str, company_name: str, strict: bool):
    """Validate a board. Returns a dict with details, or None if it doesn't exist / doesn't match."""
    try:
        if ats == "greenhouse":
            data = http.get_json(f"https://boards-api.greenhouse.io/v1/boards/{quote(board)}")
            board_name = data.get("name", "") if isinstance(data, dict) else ""
            if strict and not similar(board_name, company_name):
                return None
            return {"board_name": board_name}
        if ats == "ashby":
            data = http.get_json(f"https://api.ashbyhq.com/posting-api/job-board/{quote(board)}")
            if not isinstance(data, dict) or not isinstance(data.get("jobs"), list):
                return None
            return {"open_jobs": len(data["jobs"])}
        if ats == "lever":
            data = http.get_json(f"https://api.lever.co/v0/postings/{quote(board)}", params={"mode": "json", "limit": 5})
            return {"open_jobs_sample": len(data)} if isinstance(data, list) else None
        if ats == "smartrecruiters":
            data = http.get_json(f"https://api.smartrecruiters.com/v1/companies/{quote(board)}/postings", params={"limit": 5})
            if not isinstance(data, dict):
                return None
            content = data.get("content") or []
            board_name = ((content[0].get("company") or {}).get("name", "")) if content else ""
            if strict and not (board_name and similar(board_name, company_name)):
                return None
            return {"board_name": board_name, "open_jobs": data.get("totalFound")}
        if ats == "workday":
            m = WORKDAY_URL.search(board)
            host, tenant, site = m.group("host").lower(), m.group("tenant").lower(), m.group("site")
            data = http.post_json(f"https://{host}/wday/cxs/{tenant}/{site}/jobs",
                                  {"appliedFacets": {}, "limit": 1, "offset": 0, "searchText": ""})
            return {"open_jobs": data.get("total")} if isinstance(data, dict) and "jobPostings" in data else None
    except FetchError:
        return None
    return None


def discover_one(http: Http, row: dict):
    name, careers = row["name"], (row.get("careers_url") or "").strip()
    base = {"name": name, "list": row.get("list") or "Main", "priority": row.get("priority") or ""}

    if careers and "google.com/search" not in careers:
        try:
            final_url, page = http.get_text(careers)
            found = extract_from_page(final_url, page)
        except FetchError:
            found = None
        if found:
            ats, board = found
            info = check_board(http, ats, board, name, strict=False)
            if info is not None:
                return {**base, "ats": ats, "board": board, "found_via": "careers page", "check": False, **info}

    strict_slugs, loose_slugs = slug_candidates(name)
    for ats in ["greenhouse", "ashby", "lever", "smartrecruiters"]:
        name_checked = ats in ("greenhouse", "smartrecruiters")
        slugs = strict_slugs + (loose_slugs if name_checked else [])
        for slug in slugs:
            info = check_board(http, ats, slug, name, strict=True)
            if info is not None:
                return {**base, "ats": ats, "board": slug, "found_via": "name probe",
                        "check": not name_checked or slug in loose_slugs, **info}
    return None


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--source", default="companies_source.csv")
    p.add_argument("--out", default="companies.yaml")
    p.add_argument("--manual", default="needs_manual_alerts.csv")
    p.add_argument("--report", default="discover_report.md")
    p.add_argument("--refresh", action="store_true", help="re-discover companies already in companies.yaml")
    p.add_argument("--workers", type=int, default=8)
    args = p.parse_args(argv)

    with open(args.source, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    existing = []
    if os.path.exists(args.out) and not args.refresh:
        with open(args.out, encoding="utf-8") as f:
            existing = (yaml.safe_load(f) or {}).get("companies") or []
    done = {c["name"] for c in existing}
    todo = [r for r in rows if r["name"] not in done]
    print(f"{len(done)} companies already mapped; discovering {len(todo)}...", flush=True)

    http = Http()
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        results = list(pool.map(lambda r: (r, discover_one(http, r)), todo))

    found = [res for _, res in results if res]
    missing = [row for row, res in results if not res]
    order = {r["name"]: i for i, r in enumerate(rows)}
    companies = sorted(existing + found, key=lambda c: order.get(c["name"], 10**6))

    with open(args.out, "w", encoding="utf-8") as f:
        f.write("# Generated by discover.py. Review entries with `check: true`; set `skip: true` to stop watching one.\n")
        yaml.safe_dump({"companies": companies}, f, sort_keys=False, allow_unicode=True, width=200)
    with open(args.manual, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["name", "list", "priority", "careers_url"], extrasaction="ignore")
        w.writeheader()
        w.writerows(sorted(missing, key=lambda r: (r.get("priority") != "High", r.get("list") != "Main", r["name"])))

    by_ats = Counter(c["ats"] for c in companies)
    to_check = [c for c in companies if c.get("check")]
    lines = ["# Discovery report", "",
             f"- Watched automatically: **{len(companies)}** of {len(rows)} companies",
             "- By system: " + ", ".join(f"{k} {v}" for k, v in by_ats.most_common()),
             f"- Need careers-site alerts instead: **{len(missing)}** (see {args.manual})",
             f"- Matches to double-check: **{len(to_check)}**", ""]
    if to_check:
        lines += ["## Please confirm these matches", "",
                  "Open the board link; if it is a different company, set `skip: true` for it in companies.yaml.", ""]
        for c in to_check:
            link = {"greenhouse": f"https://job-boards.greenhouse.io/{c['board']}",
                    "ashby": f"https://jobs.ashbyhq.com/{c['board']}",
                    "lever": f"https://jobs.lever.co/{c['board']}",
                    "smartrecruiters": f"https://jobs.smartrecruiters.com/{c['board']}"}.get(c["ats"], c["board"])
            lines.append(f"- {c['name']} → {c['ats']} `{c['board']}` — {link}")
    with open(args.report, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print("\n".join(lines))
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
