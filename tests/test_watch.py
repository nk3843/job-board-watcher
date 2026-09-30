import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

import yaml

from jobwatch.digest import build_html, build_markdown, subject, write_csv
from jobwatch.models import Company, Job
from jobwatch.state import State
from watch import fetch_all, find_new

NOW = datetime(2026, 9, 29, 14, tzinfo=timezone.utc)


def job(i, title="Senior Backend Engineer", loc="Seattle, WA", age_hours=None, company="Acme"):
    posted = NOW - timedelta(hours=age_hours) if age_hours is not None else None
    return Job(company=company, ats="greenhouse", job_id=str(i), title=title, location=loc, url=f"https://x/{i}", posted_at=posted)


class TestFindNew(unittest.TestCase):
    def setUp(self):
        with open("config.example.yaml", encoding="utf-8") as f:
            self.cfg = yaml.safe_load(f)
        self.tmp = tempfile.TemporaryDirectory()
        self.state = State(os.path.join(self.tmp.name, "seen.json"))
        self.acme = Company("Acme", "greenhouse", "acme", "Main", "High")

    def tearDown(self):
        self.tmp.cleanup()

    def test_first_run_reports_only_last_24h(self):
        results = {"Acme": [job(1, age_hours=3), job(2, age_hours=72), job(3)]}
        new, failures, stats = find_new([self.acme], results, self.cfg, self.state, NOW)
        self.assertEqual([j.job_id for j in new], ["1"])
        self.assertEqual(stats["matching"], 3)
        self.assertTrue(self.state.seen("greenhouse:Acme:3"))  # remembered even though not reported

    def test_second_run_reports_unseen_jobs(self):
        find_new([self.acme], {"Acme": [job(1, age_hours=3), job(2, age_hours=72)]}, self.cfg, self.state, NOW)
        later = NOW + timedelta(days=1)
        results = {"Acme": [job(1, age_hours=27), job(2, age_hours=96), job(4), job(5, age_hours=2), job(6, age_hours=24 * 30)]}
        new, _, _ = find_new([self.acme], results, self.cfg, self.state, later)
        self.assertEqual(sorted(j.job_id for j in new), ["4", "5"])  # 6 is unseen but a month old
        self.assertEqual(new[0].priority, "High")

    def test_filters_applied(self):
        results = {"Acme": [job(1, age_hours=1, title="Account Executive"), job(2, age_hours=1, loc="Pune, India"),
                            job(3, age_hours=1, title="Data Engineer")]}
        new, _, _ = find_new([self.acme], results, self.cfg, self.state, NOW)
        self.assertEqual([(j.job_id, j.tracks) for j in new], [("3", ("data",))])

    def test_failures_are_reported_and_state_kept(self):
        find_new([self.acme], {"Acme": [job(1, age_hours=1)]}, self.cfg, self.state, NOW)
        new, failures, _ = find_new([self.acme], {"Acme": RuntimeError("HTTP 503")}, self.cfg, self.state, NOW)
        self.assertEqual(new, [])
        self.assertEqual(failures, [("Acme", "HTTP 503")])
        self.assertTrue(self.state.seen("greenhouse:Acme:1"))

    def test_state_roundtrip_and_prune(self):
        self.state.touch("k1", "2026-06-01")
        self.state.touch("k2", "2026-09-28")
        self.state.save()
        s2 = State(self.state.path)
        self.assertEqual(s2.prune("2026-09-29", 60), 1)
        self.assertFalse(s2.seen("k1"))
        self.assertTrue(s2.seen("k2"))

    def test_fetch_all_isolates_errors(self):
        def fake_fetch(http, company, **opts):
            if company.name == "Bad":
                raise RuntimeError("boom")
            return [job(1)]
        res = fetch_all(None, [self.acme, Company("Bad", "lever", "bad")], {"workers": 2}, fetch=fake_fetch)
        self.assertEqual(len(res["Acme"]), 1)
        self.assertIsInstance(res["Bad"], RuntimeError)


class TestDigest(unittest.TestCase):
    def test_outputs(self):
        a = job(1, title="Senior Backend Engineer <x>", age_hours=1)
        a.tracks, a.priority, a.list = ("backend",), "High", "Main"
        b = job(2, title="Data Engineer", age_hours=1, company="Beta")
        b.tracks, b.list = ("data",), "Secondary"
        md = build_markdown([b, a], [("Gamma", "HTTP 500")], {"companies": 3, "matching": 10}, "2026-09-29")
        self.assertLess(md.index("Acme"), md.index("Beta"))  # High priority first
        self.assertIn("1 company could not be checked", md)
        html_body = build_html([a, b], [], {"companies": 2, "matching": 2}, "2026-09-29")
        self.assertIn("&lt;x&gt;", html_body)
        self.assertEqual(subject([a, b], "2026-09-29"), "Job watch 2026-09-29: 2 new postings at 2 companies")
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "h.csv")
            write_csv(p, [a], "2026-09-29", append=True)
            write_csv(p, [b], "2026-09-30", append=True)
            with open(p) as f:
                rows = f.read().strip().splitlines()
            self.assertEqual(len(rows), 3)  # one header + two rows


if __name__ == "__main__":
    unittest.main()
