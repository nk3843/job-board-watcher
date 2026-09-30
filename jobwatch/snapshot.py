"""Daily snapshot of every open job that passes the filters, for trend analysis.

history.csv only records postings the first time they are reported. A snapshot records everything that is open
right now, so comparing days shows what opened, what closed, and how long roles stay up.

One gzipped CSV per UTC day: data/snapshots/YYYY-MM-DD.csv.gz. Later runs on the same day overwrite it, but keep
the earlier rows of any company that failed this time, so a flaky board doesn't look like mass job closures.
"""
import csv
import gzip
import io
import os
from datetime import datetime
from typing import Iterable, List

from .digest import TRACK_LABELS
from .models import Job

FIELDS = ["snapshot_date", "company", "list", "priority", "ats", "job_id", "job_key", "title",
          "tracks", "location", "country", "posted_at", "url"]


def _row(j: Job, day: str) -> dict:
    return {"snapshot_date": day, "company": j.company, "list": j.list, "priority": j.priority,
            "ats": j.ats, "job_id": j.job_id, "job_key": j.key, "title": j.title,
            "tracks": ", ".join(TRACK_LABELS.get(t, t) for t in j.tracks), "location": j.location,
            "country": j.country or "", "posted_at": j.posted_at.isoformat() if j.posted_at else "", "url": j.url}


def read_snapshot(path: str) -> List[dict]:
    if not os.path.exists(path):
        return []
    with gzip.open(path, "rt", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def write_snapshot(folder: str, jobs: Iterable[Job], failed_companies: Iterable[str], now: datetime) -> str:
    """Write today's snapshot and return its path."""
    day = now.date().isoformat()
    path = os.path.join(folder, f"{day}.csv.gz")
    failed = set(failed_companies)
    rows = [r for r in read_snapshot(path) if r["company"] in failed]   # keep what we can't refresh
    rows += [_row(j, day) for j in jobs]
    rows.sort(key=lambda r: (r["company"], r["job_key"]))

    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=FIELDS, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    os.makedirs(folder, exist_ok=True)
    tmp = path + ".tmp"
    # mtime=0 makes the file byte-identical when nothing changed, so git sees no change.
    with open(tmp, "wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as gz:
        gz.write(buf.getvalue().encode("utf-8"))
    os.replace(tmp, path)
    return path
