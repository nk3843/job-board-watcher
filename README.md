# job-board-watcher

Checks the public job boards of hundreds of companies, keeps the roles that match your keywords, and emails
you only the postings you haven't seen before. It runs on a schedule with GitHub Actions, for free.

It powers the [Job Market Dashboard](https://github.com/nk3843/job-market-dashboard)
([live](https://nk-job-market.streamlit.app/)), which charts the ~4,400 roles it collects every weekday.

## What it does

```
companies.yaml ──► fetch every board in parallel ──► filter ──► compare with state/seen.json ──► digest
 (~220 companies)   Greenhouse, Lever, Ashby,        US only,     report each posting once       email (HTML),
                    SmartRecruiters, Workday,        title        forget ones gone for 60 days   Markdown, CSV,
                    Apple, Eightfold                 keywords                                   daily snapshot
```

- **Seven hiring systems**, each with its own quirks, normalized into one `Job` model ([`jobwatch/ats.py`](jobwatch/ats.py)).
- **Reports each posting once.** The first check of a company reports only the last 24 hours, so adding a
  company doesn't flood you with its backlog; after that, anything new since the last run.
- **One broken board never stops a run.** Companies are checked in a thread pool with retries and backoff;
  failures are listed at the bottom of the digest instead of aborting.
- **Digest built for skimming:** your highlighted locations first, high-priority companies next, identical
  titles at one company collapsed into one line ("5 openings … also 2 3 4 5"), and optionally which resume
  version fits each role.
- **Finds each company's board for you:** [`discover.py`](discover.py) reads careers pages and probes each
  system's API, and lists companies it can't cover so you can set up alerts on their sites instead.
- **66 tests** run against recorded API responses, no network needed.

## Interesting problems

**Is this job in the US?** Location text is free-form: `US-CA-Santa Clara`, `Santa Clara, CALIFORNIA`,
`IND CHNN 32 A&B 3FL STE C-D`, `Toronto, ON, CA` (Canada, not California), `Munich, DE` (Germany, not
Delaware), `Dublin, OH` (Ohio, not Ireland). [`jobwatch/filters.py`](jobwatch/filters.py) weighs evidence from
strongest to weakest (US country and state names, then foreign countries and country codes, then US cities and
state codes, then the board's country field, then foreign cities, then the job title) and folds accents so
`São Paulo` matches `sao paulo`. The test cases are real postings that slipped through
([`tests/test_filters.py`](tests/test_filters.py)).

**Workday.** No posting timestamps (only "Posted 3 Days Ago"), 20 results per page, search-only access,
and the location text is often just "4 Locations"; the primary location is recovered from the job URL.

**Repeatable output.** Each run's data snapshot is written deterministically (sorted rows, fixed gzip
header), so a run that finds nothing new creates no git change.

## Quick start

```bash
python -m venv .venv && .venv/bin/pip install -r requirements.txt
cp config.example.yaml config.yaml                 # your keywords, locations, optional resume labels
cp companies.example.yaml companies.yaml           # or: python discover.py (see below)
.venv/bin/python watch.py --dry-run                # print the digest; save nothing, send nothing
```

To find boards for your own list, put it in `companies_source.csv` (see
[`companies_source.example.csv`](companies_source.example.csv)) and run `python discover.py`.

A normal run (`python watch.py`) saves what it has reported to `state/seen.json`, writes `output/latest.md`,
`output/latest.csv`, `output/history.csv` and `data/snapshots/YYYY-MM-DD.csv.gz`, and emails the digest when
`SMTP_USER` and `SMTP_PASSWORD` are set (Gmail by default; `SMTP_HOST`/`SMTP_PORT` for other providers,
`EMAIL_TO` for a different recipient).

## Run it every day on GitHub Actions

Keep your `config.yaml`, `companies.yaml`, and saved state in a **private** repo, and let its workflow check out
this code at a pinned release. [`docs/scheduled-run.yml`](docs/scheduled-run.yml) is a ready-to-copy
workflow. Pinning means a change here can't break your runs until you choose to move to a new version.

## Configuration

Everything is in `config.yaml`; [`config.example.yaml`](config.example.yaml) documents each setting.

| Setting | What it controls |
|---|---|
| `tracks`, `require_any`, `exclude` | Which titles count, and which role type(s) each gets. Whole-word, case-insensitive. |
| `us_only`, `extra_non_us` | Keep US roles only; add place names that should count as non-US. |
| `lookback_hours`, `max_age_days`, `forget_after_days` | What counts as new on the first and later runs, and when to forget a closed posting. |
| `highlight_locations`, `highlight_title`, `highlight_summary` | Which postings are listed first, and how that section is labeled. Default: remote. |
| `resume_rules`, `default_resume` | Optional label on each posting saying which resume version to send. |
| `workday_queries`, `workday_max_pages`, `workers` | Search terms for Workday boards, paging limits, parallelism. |

## Tests

```bash
python -m unittest discover -s tests -t .
```

## License

[MIT](LICENSE)
