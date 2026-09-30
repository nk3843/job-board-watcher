#!/usr/bin/env python3
"""Check every company in companies.yaml and report job postings that are new since the last run.

Usage:
  python watch.py                 # normal run: saves state, emails if SMTP_* env vars are set
  python watch.py --dry-run       # print the digest only; don't save state or send email
  python watch.py --only Stripe   # check one company (repeatable)
"""
import argparse
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import yaml

from jobwatch.ats import fetch_company
from jobwatch.digest import build_html, build_markdown, subject, write_csv
from jobwatch.filters import LocationFilter, TitleFilter
from jobwatch.http import Http
from jobwatch.labels import Labeler
from jobwatch.models import Company
from jobwatch.notify import email_configured, send_email
from jobwatch.snapshot import write_snapshot
from jobwatch.state import State


def load_config(path):
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _read_entries(path):
    with open(path, encoding="utf-8") as f:
        return (yaml.safe_load(f) or {}).get("companies") or []


def load_companies(path, extra_path=None):
    """companies.yaml, plus companies_extra.yaml entries whose name isn't already in companies.yaml
    (a company you set to skip there stays skipped)."""
    if not os.path.exists(path):
        sys.exit(f"{path} not found. Run `python discover.py` first to find each company's job board.")
    entries = _read_entries(path)
    if extra_path and os.path.exists(extra_path):
        known = {c.get("name") for c in entries}
        entries += [c for c in _read_entries(extra_path) if c.get("name") not in known]
    out = []
    for c in entries:
        if c.get("skip") or not c.get("ats") or not c.get("board"):
            continue
        out.append(Company(name=c["name"], ats=c["ats"], board=str(c["board"]),
                           list=c.get("list") or "Main", priority=c.get("priority") or ""))
    return out


def fetch_all(http, companies, cfg, fetch=fetch_company):
    options = {"workday_queries": cfg.get("workday_queries") or ["engineer"],
               "workday_max_pages": int(cfg.get("workday_max_pages") or 5),
               "eightfold_max_pages": int(cfg.get("eightfold_max_pages") or 5),
               "apple_max_pages": int(cfg.get("apple_max_pages") or 15)}

    def one(company):
        try:
            return company.name, fetch(http, company, **options)
        except Exception as e:  # one broken board must not stop the run
            return company.name, e

    with ThreadPoolExecutor(max_workers=int(cfg.get("workers") or 8)) as pool:
        return dict(pool.map(one, companies))


def find_new(companies, results, cfg, state, now, open_jobs=None):
    """Apply filters, update state, and return (new_jobs, failures, stats).
    If open_jobs is a list, every job that passes the filters is appended to it (for the daily snapshot)."""
    today = now.date().isoformat()
    title_filter = TitleFilter(cfg)
    labeler = Labeler(cfg)
    location_filter = LocationFilter(cfg.get("extra_non_us") or [])
    bootstrap_cutoff = now - timedelta(hours=float(cfg.get("lookback_hours") or 24))
    stale_cutoff = now - timedelta(days=float(cfg.get("max_age_days") or 7))
    us_only = cfg.get("us_only", True)

    new, failures, matching = [], [], 0
    for company in companies:
        result = results.get(company.name)
        if isinstance(result, Exception) or result is None:
            failures.append((company.name, str(result) if result else "not checked"))
            continue
        first_time = not state.company_known(company.key)
        for job in result:
            if us_only and not location_filter.is_us(job.location, job.country, job.title):
                continue
            tracks = title_filter.classify(job.title)
            if not tracks:
                continue
            job.tracks, job.list, job.priority = tracks, company.list, company.priority
            job.resume, job.highlight = labeler.resume(job.title), labeler.highlight(job.location)
            matching += 1
            if open_jobs is not None:
                open_jobs.append(job)
            if state.seen(job.key):
                state.touch(job.key, today)
                continue
            state.touch(job.key, today)
            if first_time:
                if job.posted_at and job.posted_at >= bootstrap_cutoff:
                    new.append(job)
            elif job.posted_at is None or job.posted_at >= stale_cutoff:
                new.append(job)
        state.mark_company(company.key, today)
    return new, failures, {"companies": len(companies), "matching": matching}


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--config", default="config.yaml")
    p.add_argument("--companies", default="companies.yaml")
    p.add_argument("--extra", default="companies_extra.yaml", help="extra companies (ignored if already in --companies)")
    p.add_argument("--state", default="state/seen.json")
    p.add_argument("--out", default="output")
    p.add_argument("--snapshots", default="data/snapshots", help="folder for the daily snapshot of all open jobs")
    p.add_argument("--only", action="append", help="check only this company (repeatable)")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args(argv)

    cfg = load_config(args.config)
    companies = load_companies(args.companies, args.extra)
    if args.only:
        wanted = {n.lower() for n in args.only}
        companies = [c for c in companies if c.name.lower() in wanted]
    if not companies:
        sys.exit("No companies to check.")

    now = datetime.now(timezone.utc)
    day = now.date().isoformat()
    state = State(args.state)
    print(f"Checking {len(companies)} companies...", flush=True)
    results = fetch_all(Http(), companies, cfg)
    open_jobs = []
    new, failures, stats = find_new(companies, results, cfg, state, now, open_jobs)

    labels = {"highlight_title": cfg.get("highlight_title") or "Remote",
              "highlight_summary": cfg.get("highlight_summary") or "remote"}
    md = build_markdown(new, failures, stats, day, **labels)
    print(md)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write(md)
    if args.dry_run:
        print("(dry run: state not saved, no email sent)")
        return 0

    os.makedirs(args.out, exist_ok=True)
    with open(os.path.join(args.out, "latest.md"), "w", encoding="utf-8") as f:
        f.write(md)
    write_csv(os.path.join(args.out, "latest.csv"), new, day)
    write_csv(os.path.join(args.out, "history.csv"), new, day, append=True)
    if not args.only:  # a partial run would make every other company look closed
        write_snapshot(args.snapshots, open_jobs, [name for name, _ in failures], now)
    state.prune(day, int(cfg.get("forget_after_days") or 60))
    state.save()

    if new or cfg.get("email_when_empty"):
        if email_configured():
            send_email(subject(new, day, labels["highlight_summary"]), md, build_html(new, failures, stats, day, **labels))
            print("Email sent.")
        else:
            print("Email not configured (set SMTP_USER and SMTP_PASSWORD); skipping.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
