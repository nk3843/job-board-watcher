import csv
import gzip
import os
import tempfile
import unittest

from jobwatch.export import PUBLIC_FIELDS, export_snapshots
from jobwatch.models import Job
from jobwatch.snapshot import write_snapshot
from tests.test_watch import NOW


class TestExport(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.src = os.path.join(self.tmp.name, "private")
        self.dst = os.path.join(self.tmp.name, "public")
        j = Job(company="Acme", ats="greenhouse", job_id="1", title="Data Engineer", location="Seattle, WA",
                url="https://x/1", tracks=("data",), list="Secondary", priority="High", resume="Data Engineer",
                highlight=True)
        write_snapshot(self.src, [j], [], NOW)

    def tearDown(self):
        self.tmp.cleanup()

    def read_public(self):
        with gzip.open(os.path.join(self.dst, "2026-09-29.csv.gz"), "rt", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            return reader.fieldnames, list(reader)

    def test_only_allowlisted_columns_are_published(self):
        export_snapshots(self.src, self.dst)
        header, rows = self.read_public()
        self.assertEqual(header, PUBLIC_FIELDS)
        for private in ("priority", "list", "resume", "highlight"):
            self.assertNotIn(private, header)
        self.assertNotIn("High", rows[0].values())
        self.assertNotIn("Secondary", rows[0].values())
        self.assertEqual(rows[0]["title"], "Data Engineer")

    def test_unchanged_files_are_not_rewritten(self):
        self.assertEqual(export_snapshots(self.src, self.dst), 1)
        self.assertEqual(export_snapshots(self.src, self.dst), 0)


if __name__ == "__main__":
    unittest.main()
