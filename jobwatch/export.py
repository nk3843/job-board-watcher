"""Copy daily snapshots to the public dashboard repo, keeping only columns that are safe to publish.

Only the columns in PUBLIC_FIELDS are written; anything else (priority, list, and any column added later)
stays private by default. Everything published is already public on the companies' own job boards.

Usage: python -m jobwatch.export data/snapshots ../dashboard/data/snapshots
"""
import csv
import gzip
import io
import os
import sys

from .snapshot import read_snapshot

PUBLIC_FIELDS = ["snapshot_date", "company", "ats", "job_key", "title", "tracks", "location", "country",
                 "posted_at", "url"]


def public_bytes(rows) -> bytes:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=PUBLIC_FIELDS, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    out = io.BytesIO()
    with gzip.GzipFile(fileobj=out, mode="wb", mtime=0) as gz:
        gz.write(buf.getvalue().encode("utf-8"))
    return out.getvalue()


def export_snapshots(src: str, dst: str) -> int:
    """Write a public copy of every snapshot in src to dst; returns how many files changed."""
    os.makedirs(dst, exist_ok=True)
    changed = 0
    for name in sorted(os.listdir(src)):
        if not name.endswith(".csv.gz"):
            continue
        data = public_bytes(read_snapshot(os.path.join(src, name)))
        target = os.path.join(dst, name)
        if os.path.exists(target):
            with open(target, "rb") as f:
                if f.read() == data:
                    continue
        with open(target, "wb") as f:
            f.write(data)
        changed += 1
    return changed


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    print(f"{export_snapshots(sys.argv[1], sys.argv[2])} snapshot file(s) updated in {sys.argv[2]}")
