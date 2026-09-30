import os
import tempfile
import unittest
from datetime import timedelta

import yaml

from jobwatch.models import Company
from jobwatch.snapshot import read_snapshot, write_snapshot
from jobwatch.state import State
from tests.test_watch import NOW, job
from watch import find_new


class TestSnapshot(unittest.TestCase):
    def setUp(self):
        with open("config.example.yaml", encoding="utf-8") as f:
            self.cfg = yaml.safe_load(f)
        self.tmp = tempfile.TemporaryDirectory()
        self.folder = os.path.join(self.tmp.name, "snapshots")
        self.state = State(os.path.join(self.tmp.name, "seen.json"))
        self.companies = [Company("Acme", "greenhouse", "acme"), Company("Beta", "greenhouse", "beta")]

    def tearDown(self):
        self.tmp.cleanup()

    def run_once(self, results, now=NOW):
        open_jobs = []
        _, failures, _ = find_new(self.companies, results, self.cfg, self.state, now, open_jobs)
        return write_snapshot(self.folder, open_jobs, [name for name, _ in failures], now)

    def test_includes_every_matching_job_not_just_new_ones(self):
        results = {"Acme": [job(1, age_hours=1), job(2, age_hours=200), job(3, title="Account Executive")],
                   "Beta": [job(4, company="Beta", title="Data Engineer")]}
        path = self.run_once(results)
        rows = read_snapshot(path)
        self.assertTrue(path.endswith("2026-09-29.csv.gz"))
        self.assertEqual([r["job_key"] for r in rows],
                         ["greenhouse:Acme:1", "greenhouse:Acme:2", "greenhouse:Beta:4"])  # sales role filtered out
        self.assertEqual(rows[2]["tracks"], "Data")

    def test_failed_company_keeps_earlier_rows_from_same_day(self):
        self.run_once({"Acme": [job(1)], "Beta": [job(4, company="Beta")]})
        later = NOW + timedelta(hours=4)
        rows = read_snapshot(self.run_once({"Acme": [job(1), job(2)], "Beta": RuntimeError("HTTP 503")}, later))
        self.assertEqual(sorted(r["job_key"] for r in rows),
                         ["greenhouse:Acme:1", "greenhouse:Acme:2", "greenhouse:Beta:4"])

    def test_closed_jobs_drop_out_on_rerun(self):
        self.run_once({"Acme": [job(1), job(2)], "Beta": []})
        rows = read_snapshot(self.run_once({"Acme": [job(1)], "Beta": []}, NOW + timedelta(hours=4)))
        self.assertEqual([r["job_key"] for r in rows], ["greenhouse:Acme:1"])

    def test_unchanged_run_writes_identical_bytes(self):
        results = {"Acme": [job(1)], "Beta": []}
        path = self.run_once(results)
        with open(path, "rb") as f:
            first = f.read()
        self.run_once(results, NOW + timedelta(hours=4))  # a later run that finds the same jobs
        with open(path, "rb") as f:
            self.assertEqual(f.read(), first)


if __name__ == "__main__":
    unittest.main()
