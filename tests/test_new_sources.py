import json
import os
import tempfile
import unittest
from datetime import datetime, timezone

import yaml

from jobwatch.ats import (extract_apple_data, fetch_apple, fetch_eightfold, find_apple_results,
                          parse_eightfold_board)
from jobwatch.http import FetchError
from jobwatch.labels import Labeler
from jobwatch.models import Company, Job
from tests.fakes import FakeHttp


def apple_result(pid, title, city="Cupertino", country="United States", posted="2026-09-29T10:00:00Z"):
    return {"positionId": pid, "postingTitle": title, "transformedPostingTitle": title.lower().replace(" ", "-"),
            "postDateInGMT": posted, "locations": [{"name": city, "countryName": country}]}


def apple_page(results, as_json_parse=True):
    data = {"loaderData": {"search": {"searchResults": results, "totalRecords": 80}}}
    if as_json_parse:
        return f'<script>window.__staticRouterHydrationData = JSON.parse({json.dumps(json.dumps(data))});</script>'
    return f"<script>window.__staticRouterHydrationData = {json.dumps(data)};</script>"


def apple_url(page):
    return f"https://jobs.apple.com/en-us/search?location=united-states-USA&sort=newest&page={page}"


class TestApple(unittest.TestCase):
    def test_extract_both_embedding_styles(self):
        results = [apple_result("200001", "Software Engineer")]
        for style in (True, False):
            data = extract_apple_data(apple_page(results, as_json_parse=style))
            self.assertEqual(find_apple_results(data)[0]["positionId"], "200001")
        self.assertIsNone(extract_apple_data("<html>nothing here</html>"))

    def test_fetch_paginates_and_stops_on_repeat(self):
        p1 = [apple_result(f"20000{i}", f"Backend Engineer {i}") for i in range(3)]
        p2 = [apple_result("200009", "ML Engineer", city="Seattle")]
        pages = {apple_url(1): ("", apple_page(p1)), apple_url(2): ("", apple_page(p2)),
                 apple_url(3): ("", apple_page(p2))}   # Apple repeats the last page when you go past the end
        jobs = fetch_apple(FakeHttp(pages=pages), Company("Apple", "apple", "united-states-USA"), apple_max_pages=10)
        self.assertEqual(len(jobs), 4)
        self.assertEqual(jobs[0].url, "https://jobs.apple.com/en-us/details/200000/backend-engineer-0")
        self.assertEqual(jobs[3].location, "Seattle, United States")
        self.assertEqual(jobs[0].posted_at, datetime(2026, 9, 29, 10, tzinfo=timezone.utc))

    def test_fetch_fails_loudly_when_format_changes(self):
        http = FakeHttp(pages={apple_url(1): ("", "<html>new design</html>")})
        with self.assertRaises(FetchError):
            fetch_apple(http, Company("Apple", "apple", "united-states-USA"))


EF_PCSX = "https://apply.careers.microsoft.com/api/pcsx/search"
EF_V2 = "https://apply.careers.microsoft.com/api/apply/v2/jobs"
MS = Company("Microsoft", "eightfold", "https://apply.careers.microsoft.com/?domain=microsoft.com&location=United%20States")


def ef_positions(start, n, prefix="Software Engineer"):
    return [{"id": 1970000000000 + i, "name": f"{prefix} {i}", "locations": ["Redmond, Washington, United States"],
             "postedTs": 1790000000, "canonicalPositionUrl": f"https://apply.careers.microsoft.com/careers/job/{i}"}
            for i in range(start, start + n)]


class TestEightfold(unittest.TestCase):
    def test_board_parsing(self):
        self.assertEqual(parse_eightfold_board(MS.board), ("apply.careers.microsoft.com", "microsoft.com", "United States"))
        with self.assertRaises(ValueError):
            parse_eightfold_board("https://careers.qualcomm.com/")

    def test_pcsx_pagination_and_fields(self):
        calls = []

        def pcsx(params):
            calls.append(params)
            start = params["start"]
            return {"count": 15, "positions": ef_positions(start, 10 if start == 0 else 5)}

        jobs = fetch_eightfold(FakeHttp(get={EF_PCSX: pcsx}), MS, workday_queries=["software engineer"])
        self.assertEqual(len(jobs), 15)
        self.assertEqual(calls[0]["location"], "United States")
        self.assertEqual(calls[0]["domain"], "microsoft.com")
        self.assertEqual(calls[0]["num"], 10)
        self.assertEqual(jobs[0].title, "Software Engineer 0")
        self.assertEqual(jobs[0].location, "Redmond, Washington, United States")
        self.assertEqual(jobs[0].posted_at, datetime.fromtimestamp(1790000000, tz=timezone.utc))

    def test_drops_sort_param_if_rejected_and_unwraps_data(self):
        def pcsx(params):
            if "sort_by" in params:
                raise FetchError("HTTP 400")
            return {"status": 200, "data": {"count": 2, "positions": ef_positions(0, 2)}}
        self.assertEqual(len(fetch_eightfold(FakeHttp(get={EF_PCSX: pcsx}), MS, workday_queries=["x"])), 2)

    def test_falls_back_to_older_endpoint(self):
        v2 = lambda params: {"count": 1, "positions": [{"id": 5, "name": "Data Engineer", "location": "Redmond, WA",
                                                         "t_create": 1790000000000}]}
        jobs = fetch_eightfold(FakeHttp(get={EF_V2: v2}), MS, workday_queries=["x"])   # pcsx -> 404
        self.assertEqual((jobs[0].title, jobs[0].location), ("Data Engineer", "Redmond, WA"))
        self.assertEqual(jobs[0].url, "https://apply.careers.microsoft.com/careers/job/5")
        self.assertEqual(jobs[0].posted_at.year, 2026)

    def test_changed_fields_and_empty_results_are_errors(self):
        weird = lambda params: {"positions": [{"id": 1, "headline": "?"}]}
        with self.assertRaises(FetchError) as ctx:
            fetch_eightfold(FakeHttp(get={EF_PCSX: weird, EF_V2: weird}), MS, workday_queries=["x"])
        self.assertIn("headline", str(ctx.exception))
        empty = lambda params: {"count": 0, "positions": []}
        with self.assertRaises(FetchError):
            fetch_eightfold(FakeHttp(get={EF_PCSX: empty, EF_V2: empty}), MS, workday_queries=["x"])


class TestLabels(unittest.TestCase):
    def setUp(self):
        with open("config.example.yaml", encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
        cfg["highlight_locations"] = ["seattle", "bellevue", "remote", "virtual"]
        self.l = Labeler(cfg)

    def test_resume_from_real_titles(self):
        cases = {
            "Senior Staff AI Engineer - Agentic AI Platform (Remote Eligible)": "AI Engineer",
            "Lead AI Engineer -- Advanced AI (applied ML, LLMs, agentic AI, ML Ops)": "AI Engineer",
            "Machine Learning Engineer 5": "ML Engineer",
            "Machine Learning Engineer, On-device AI": "ML Engineer",
            "Software Engineer II | ML & AI (REMOTE)": "ML Engineer",
            "Staff Software Engineer, AI Teammates": "AI Engineer",
            "Senior Software Engineer, Data Platform for AI": "Data Engineer",
            "ML Runtime and Kernel Engineer - Core ML": "ML Engineer",
            "Data Engineer 4 - Intelligent Foundations and Experiences (IFX)": "Data Engineer",
            "Senior Software Engineer - Enterprise Data Warehousing (Remote)": "Data Engineer",
            "Full-stack Engineer 4 (Python, Java, Spring Boot)": "Full-stack",
            "Full Stack Engineer 4 (Go, AWS)": "Full-stack",
            "Staff Software Engineer, Backend (Identity Decisioning)": "Backend",
            "Senior Systems Engineer, MCP Server Portals": "Backend",
        }
        for title, want in cases.items():
            self.assertEqual(self.l.resume(title), want, title)

    def test_highlight(self):
        for loc in ["Seattle, WA", "Remote US", "GEORGIA - VIRTUAL - GA01", "San Francisco (Remote)", "Bellevue, WA"]:
            self.assertTrue(self.l.highlight(loc), loc)
        for loc in ["McLean, VA", "5 Locations (primary: New York NY)", ""]:
            self.assertFalse(self.l.highlight(loc), loc)

    def test_defaults_highlight_remote_and_add_no_resume_labels(self):
        l = Labeler({})
        self.assertTrue(l.highlight("Remote - US"))
        self.assertFalse(l.highlight("Seattle, WA"))
        self.assertEqual(l.resume("Machine Learning Engineer"), "")

    def test_config_override(self):
        l = Labeler({"resume_rules": [{"resume": "Python", "keywords": ["python"]}], "default_resume": "General",
                     "highlight_locations": ["austin"]})
        self.assertEqual(l.resume("Python Developer"), "Python")
        self.assertEqual(l.resume("Go Developer"), "General")
        self.assertTrue(l.highlight("Austin, TX"))


class TestExtraCompanies(unittest.TestCase):
    def test_extras_never_override_main_list(self):
        from watch import load_companies
        with tempfile.TemporaryDirectory() as d:
            main, extra = os.path.join(d, "c.yaml"), os.path.join(d, "x.yaml")
            with open(main, "w") as f:
                yaml.safe_dump({"companies": [
                    {"name": "NVIDIA", "ats": "workday", "board": "https://nvidia.wd5.myworkdayjobs.com/Main"},
                    {"name": "Deel", "ats": "ashby", "board": "deel", "skip": True}]}, f)
            with open(extra, "w") as f:
                yaml.safe_dump({"companies": [
                    {"name": "NVIDIA", "ats": "workday", "board": "https://nvidia.wd5.myworkdayjobs.com/Other"},
                    {"name": "Deel", "ats": "greenhouse", "board": "deel"},
                    {"name": "Apple", "ats": "apple", "board": "united-states-USA"}]}, f)
            got = {c.name: c.board for c in load_companies(main, extra)}
            self.assertEqual(got, {"NVIDIA": "https://nvidia.wd5.myworkdayjobs.com/Main", "Apple": "united-states-USA"})
            self.assertEqual(len(load_companies(main, os.path.join(d, "missing.yaml"))), 1)

    def test_shipped_extra_file_is_valid(self):
        from jobwatch.ats import FETCHERS, parse_workday_url
        with open("companies.example.yaml") as f:
            entries = yaml.safe_load(f)["companies"]
        for c in entries:
            self.assertIn(c["ats"], FETCHERS, c["name"])
            if c["ats"] == "eightfold":
                parse_eightfold_board(c["board"])
            if c["ats"] == "workday":
                parse_workday_url(c["board"])


class TestDigestLayout(unittest.TestCase):
    def _job(self, i, company, title, loc, highlight=False, priority="", lst="Main", resume="Backend"):
        return Job(company=company, ats="workday", job_id=str(i), title=title, location=loc, url=f"https://x/{i}",
                   priority=priority, list=lst, resume=resume, highlight=highlight)

    def test_sections_grouping_and_labels(self):
        from jobwatch.digest import build_html, build_markdown, subject
        jobs = [self._job(i, "Capital One", "Full-stack Engineer 4", loc, priority="High", resume="Full-stack")
                for i, loc in enumerate(["McLean, VA", "McLean, VA", "Richmond, VA", "New York, NY", "Plano, TX"])]
        jobs.append(self._job(9, "Qualcomm", "Staff Software Engineer", "Seattle, WA", highlight=True, priority="High"))
        jobs.append(self._job(10, "Intel", "Full Stack Engineer 4", "Remote US", highlight=True))
        labels = {"highlight_title": "Seattle area & remote", "highlight_summary": "in Seattle or remote"}
        md = build_markdown(jobs, [], {"companies": 3, "matching": 50}, "2026-09-29", **labels)
        self.assertLess(md.index("Seattle area & remote (2)"), md.index("Other locations (5)"))
        self.assertLess(md.index("Qualcomm"), md.index("Capital One"))
        line = next(l for l in md.splitlines() if l.startswith("- [Full-stack Engineer 4]"))
        self.assertIn("5 openings (McLean, VA; Richmond, VA; New York, NY +1 more)", line)
        self.assertIn("also [2](https://x/1) [3](https://x/2) [4](https://x/3) [5](https://x/4)", line)
        self.assertTrue(line.endswith("Resume: Full-stack"))
        self.assertEqual(md.count("- [Full-stack Engineer 4]"), 1)
        self.assertEqual(subject(jobs, "2026-09-29", labels["highlight_summary"]),
                         "Job watch 2026-09-29: 7 new postings at 3 companies (2 in Seattle or remote)")
        self.assertEqual(subject(jobs, "2026-09-29"),     # default wording
                         "Job watch 2026-09-29: 7 new postings at 3 companies (2 remote)")
        self.assertIn("## Remote (2)", build_markdown(jobs, [], {}, "2026-09-29"))
        h = build_html(jobs, [("Meta", "x")], {"companies": 3, "matching": 50}, "2026-09-29", **labels)
        self.assertIn("5 openings", h)
        self.assertIn("Full-stack resume", h)
        self.assertIn("1 company could not be checked today", h)

    def test_no_section_headers_without_local_jobs(self):
        from jobwatch.digest import build_markdown
        md = build_markdown([self._job(1, "Stripe", "Backend Engineer", "Seattle, WA")], [], {}, "2026-09-29")
        self.assertNotIn("Other locations", md)
        self.assertIn("## Stripe (Main)", md)

    def test_csv_upgrades_old_history_file(self):
        from jobwatch.digest import CSV_FIELDS, write_csv
        import csv
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "history.csv")
            with open(p, "w", newline="") as f:
                w = csv.writer(f)
                w.writerow(["date", "company", "list", "priority", "title", "tracks", "location", "posted_at", "url"])
                w.writerow(["2026-09-28", "Stripe", "Main", "", "Backend Engineer", "Backend", "SF", "", "https://s"])
            write_csv(p, [self._job(1, "Intel", "ML Engineer", "Remote US", highlight=True, resume="ML Engineer")],
                      "2026-09-29", append=True)
            write_csv(p, [self._job(2, "AMD", "Data Engineer", "Austin, TX", resume="Data Engineer")],
                      "2026-09-30", append=True)
            with open(p, newline="") as f:
                rows = list(csv.DictReader(f))
            self.assertEqual(list(rows[0].keys()), CSV_FIELDS)
            self.assertEqual([r["company"] for r in rows], ["Stripe", "Intel", "AMD"])
            self.assertEqual((rows[0]["url"], rows[1]["resume"], rows[1]["highlighted"]),
                             ("https://s", "ML Engineer", "yes"))


if __name__ == "__main__":
    unittest.main()
