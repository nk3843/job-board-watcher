import unittest
from datetime import datetime, timedelta, timezone

from jobwatch.ats import (fetch_ashby, fetch_greenhouse, fetch_lever, fetch_smartrecruiters, fetch_workday,
                          parse_iso, parse_workday_posted, parse_workday_url, workday_location)
from jobwatch.filters import is_us_location
from jobwatch.models import Company
from tests.fakes import FakeHttp


class TestParsing(unittest.TestCase):
    def test_parse_iso(self):
        self.assertEqual(parse_iso("2026-09-27T14:00:00-04:00"), datetime(2026, 9, 27, 18, 0, tzinfo=timezone.utc))
        self.assertEqual(parse_iso("2026-09-27T18:00:00.123Z").hour, 18)
        self.assertIsNone(parse_iso(None))
        self.assertIsNone(parse_iso("not a date"))

    def test_workday_url(self):
        self.assertEqual(parse_workday_url("https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite"),
                         ("nvidia.wd5.myworkdayjobs.com", "nvidia", "NVIDIAExternalCareerSite"))
        self.assertEqual(parse_workday_url("https://Acme.wd1.myworkdayjobs.com/en-US/Careers/job/X_123"),
                         ("acme.wd1.myworkdayjobs.com", "acme", "Careers"))
        with self.assertRaises(ValueError):
            parse_workday_url("https://example.com/careers")

    def test_workday_location_from_link(self):
        # Real examples from the 2026-09-28 run
        self.assertEqual(workday_location("", "/job/Hyderabad/AI---ML-Engineer_ATCI-5291815-S1934539-1"), "Hyderabad")
        self.assertEqual(workday_location("", "/job/India-Bengaluru-Karnataka/Lead-Service-Data-Engineer_JREQ203312"),
                         "India Bengaluru Karnataka")
        self.assertEqual(workday_location("5 Locations", "/job/New-York-NY/AI-Engineer-4--MLX-_R1002364-1"),
                         "5 Locations (primary: New York NY)")
        self.assertEqual(workday_location("Nottingham,  Eng", "/job/Nottingham--Eng/X_R1"), "Nottingham,  Eng")  # real text kept
        self.assertEqual(workday_location("McLean, VA", "/job/McLean-VA/X_R2"), "McLean, VA")
        self.assertEqual(workday_location("", "not-a-job-path"), "")

    def test_workday_link_locations_filter_correctly(self):
        dropped = [("", "/job/Hyderabad/X_1"), ("", "/job/Bengaluru/X_2"), ("", "/job/Pune/X_3"),
                   ("", "/job/India-Bengaluru-Karnataka/X_4"), ("2 Locations", "/job/Chennai-Tamil-Nadu-India/X_5"),
                   ("2 Locations", "/job/Hungary--Budapest/X_6")]
        kept = [("", "/job/San-Francisco-415-Mission-Street-Corp/X_7"),
                ("", "/job/United-States-of-America-Eagan-Minnesota/X_8"),
                ("5 Locations", "/job/New-York-NY/X_9"), ("3 Locations", "/job/San-Jose-United-States-of-America/X_10"),
                ("4 Locations", "/job/IL-CHICAGO-233-S-WACKER-DR-STE-3700/X_11"), ("2 Locations", "/job/Arlington-Virginia/X_12")]
        for text, path in dropped:
            self.assertFalse(is_us_location(workday_location(text, path)), path)
        for text, path in kept:
            self.assertTrue(is_us_location(workday_location(text, path)), path)

    def test_workday_posted(self):
        now = datetime(2026, 9, 28, 12, tzinfo=timezone.utc)
        self.assertEqual(parse_workday_posted("Posted Today", now), now)
        self.assertEqual(parse_workday_posted("Posted Yesterday", now), now - timedelta(days=1))
        self.assertEqual(parse_workday_posted("Posted 3 Days Ago", now), now - timedelta(days=3))
        self.assertEqual(parse_workday_posted("Posted 30+ Days Ago", now), now - timedelta(days=30))
        self.assertIsNone(parse_workday_posted(None, now))


class TestFetchers(unittest.TestCase):
    def test_greenhouse(self):
        http = FakeHttp(get={"https://boards-api.greenhouse.io/v1/boards/acme/jobs": {"jobs": [
            {"id": 11, "title": " Senior Backend Engineer ", "location": {"name": "Seattle, WA"},
             "absolute_url": "https://job-boards.greenhouse.io/acme/jobs/11",
             "updated_at": "2026-09-27T10:00:00-04:00", "first_published": "2026-09-27T09:00:00-04:00"},
            {"id": 12, "title": "Data Engineer", "location": None, "absolute_url": "u", "updated_at": "2026-09-01T00:00:00Z"},
        ], "meta": {"total": 2}}})
        jobs = fetch_greenhouse(http, Company("Acme", "greenhouse", "acme"))
        self.assertEqual([j.job_id for j in jobs], ["11", "12"])
        self.assertEqual(jobs[0].title, "Senior Backend Engineer")
        self.assertEqual(jobs[0].location, "Seattle, WA")
        self.assertEqual(jobs[0].posted_at.hour, 13)
        self.assertIsNone(jobs[1].posted_at)  # updated_at is not treated as a posting date

    def test_lever(self):
        http = FakeHttp(get={"https://api.lever.co/v0/postings/acme": [
            {"id": "abc", "text": "ML Engineer", "createdAt": 1790000000000, "country": "US",
             "categories": {"location": "Remote - US", "allLocations": ["Remote - US", "New York, NY"]},
             "hostedUrl": "https://jobs.lever.co/acme/abc"}]})
        [job] = fetch_lever(http, Company("Acme", "lever", "acme"))
        self.assertEqual(job.location, "Remote - US; New York, NY")
        self.assertEqual(job.country, "US")
        self.assertEqual(job.posted_at, datetime.fromtimestamp(1790000000, tz=timezone.utc))

    def test_ashby(self):
        http = FakeHttp(get={"https://api.ashbyhq.com/posting-api/job-board/acme": {"jobs": [
            {"id": "j1", "title": "AI Engineer", "location": "San Francisco", "isRemote": True, "isListed": True,
             "secondaryLocations": [{"location": "New York"}], "publishedAt": "2026-09-28T01:02:03.000+00:00",
             "jobUrl": "https://jobs.ashbyhq.com/acme/j1",
             "address": {"postalAddress": {"addressCountry": "United States"}}},
            {"id": "j2", "title": "Hidden", "isListed": False}]}})
        jobs = fetch_ashby(http, Company("Acme", "ashby", "acme"))
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].location, "San Francisco; New York (Remote)")
        self.assertEqual(jobs[0].country, "United States")

    def test_smartrecruiters_paginates(self):
        base = "https://api.smartrecruiters.com/v1/companies/Acme/postings"

        def page(params):
            off = params["offset"]
            items = [{"id": str(i), "name": f"Software Engineer {i}", "releasedDate": "2026-09-28T00:00:00.000Z",
                      "location": {"city": "Austin", "region": "TX", "country": "us"}}
                     for i in range(off, min(off + 100, 150))]
            return {"totalFound": 150, "content": items}

        jobs = fetch_smartrecruiters(FakeHttp(get={base: page}), Company("Acme", "smartrecruiters", "Acme"))
        self.assertEqual(len(jobs), 150)
        self.assertEqual(jobs[0].location, "Austin, TX")
        self.assertEqual(jobs[0].country, "US")

    def test_workday_paginates_and_dedupes(self):
        api = "https://acme.wd5.myworkdayjobs.com/wday/cxs/acme/External/jobs"

        def respond(payload):
            if payload["searchText"] == "software engineer":
                if payload["offset"] == 0:
                    return {"total": 21, "jobPostings": [
                        {"title": f"Software Engineer {i}", "externalPath": f"/job/X_{i}",
                         "locationsText": "US-WA-Seattle", "postedOn": "Posted Today"} for i in range(20)]}
                return {"total": 0, "jobPostings": [
                    {"title": "Software Engineer 20", "externalPath": "/job/X_20", "locationsText": "2 Locations",
                     "postedOn": "Posted 30+ Days Ago"}]}
            return {"total": 1, "jobPostings": [
                {"title": "Software Engineer 0", "externalPath": "/job/X_0", "postedOn": "Posted Today"}]}

        http = FakeHttp(post={api: respond})
        jobs = fetch_workday(http, Company("Acme", "workday", "https://acme.wd5.myworkdayjobs.com/en-US/External"),
                             workday_queries=["software engineer", "data engineer"], workday_max_pages=5)
        self.assertEqual(len(jobs), 21)  # duplicate from the second query is dropped
        self.assertEqual(jobs[0].url, "https://acme.wd5.myworkdayjobs.com/External/job/X_0")
        self.assertEqual(sum(1 for c in http.calls if c[2]["searchText"] == "software engineer"), 2)


if __name__ == "__main__":
    unittest.main()
