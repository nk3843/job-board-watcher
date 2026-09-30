import unittest

from discover import check_board, discover_one, extract_from_page, similar, slug_candidates
from tests.fakes import FakeHttp


class TestDiscoverHelpers(unittest.TestCase):
    def test_extract_greenhouse_embed(self):
        html = '<script src="https://boards.greenhouse.io/embed/job_board/js?for=acmeco"></script>'
        self.assertEqual(extract_from_page("https://acme.com/careers", html), ("greenhouse", "acmeco"))

    def test_extract_prefers_most_linked(self):
        html = ('<a href="https://jobs.lever.co/acme/1">a</a><a href="https://jobs.lever.co/acme/2">b</a>'
                '<a href="https://jobs.ashbyhq.com/other">c</a>')
        self.assertEqual(extract_from_page("https://acme.com", html), ("lever", "acme"))

    def test_extract_workday_and_redirect(self):
        found = extract_from_page("https://acme.wd5.myworkdayjobs.com/en-US/AcmeCareers", "")
        self.assertEqual(found, ("workday", "https://acme.wd5.myworkdayjobs.com/AcmeCareers"))

    def test_extract_ignores_noise(self):
        self.assertIsNone(extract_from_page("https://acme.com", '<a href="https://boards.greenhouse.io/embed/job_app?token=1">x</a>'))
        self.assertIsNone(extract_from_page("https://acme.com", "<html>no boards here</html>"))

    def test_slug_candidates(self):
        strict, loose = slug_candidates("Grafana Labs")
        self.assertEqual(strict[:2], ["grafanalabs", "grafana-labs"])
        self.assertEqual(loose, ["grafana"])
        strict, loose = slug_candidates("Anysphere (Cursor)")
        self.assertIn("cursor", strict)
        strict, _ = slug_candidates("CoreWeave (incl. Weights & Biases)")
        self.assertNotIn("weightsandbiases", strict)

    def test_similar(self):
        self.assertTrue(similar("Stripe", "Stripe, Inc."))
        self.assertTrue(similar("Grafana Labs", "Grafana"))
        self.assertTrue(similar("Scale AI", "Scale AI"))
        self.assertFalse(similar("Block (Square, Cash App)", "Blockchain Capital"))
        self.assertFalse(similar("Target", "Target Global"))


class TestDiscoverOne(unittest.TestCase):
    def test_from_careers_page(self):
        http = FakeHttp(pages={"https://acme.com/careers": ("https://acme.com/careers", "jobs.ashbyhq.com/acme")},
                        get={"https://api.ashbyhq.com/posting-api/job-board/acme": {"jobs": [{}, {}]}})
        res = discover_one(http, {"name": "Acme", "list": "Main", "priority": "High", "careers_url": "https://acme.com/careers"})
        self.assertEqual((res["ats"], res["board"], res["found_via"], res["check"]), ("ashby", "acme", "careers page", False))

    def test_probe_greenhouse_validates_name(self):
        http = FakeHttp(get={"https://boards-api.greenhouse.io/v1/boards/grafana": {"name": "Some Other Co"},
                             "https://boards-api.greenhouse.io/v1/boards/grafanalabs": {"name": "Grafana Labs"}})
        res = discover_one(http, {"name": "Grafana Labs", "careers_url": ""})
        self.assertEqual((res["ats"], res["board"], res["check"]), ("greenhouse", "grafanalabs", False))

    def test_probe_rejects_wrong_company(self):
        http = FakeHttp(get={"https://boards-api.greenhouse.io/v1/boards/target": {"name": "Target Global"}})
        self.assertIsNone(discover_one(http, {"name": "Target", "careers_url": "https://www.google.com/search?q=x"}))

    def test_probe_lever_is_flagged_for_review(self):
        http = FakeHttp(get={"https://api.lever.co/v0/postings/acmeco": []})
        res = discover_one(http, {"name": "AcmeCo", "careers_url": ""})
        self.assertEqual((res["ats"], res["check"]), ("lever", True))

    def test_check_board_workday(self):
        api = "https://acme.wd1.myworkdayjobs.com/wday/cxs/acme/Ext/jobs"
        http = FakeHttp(post={api: {"total": 42, "jobPostings": []}})
        self.assertEqual(check_board(http, "workday", "https://acme.wd1.myworkdayjobs.com/Ext", "Acme", False), {"open_jobs": 42})



class TestAliases(unittest.TestCase):
    def test_parenthetical_and_slash_names(self):
        self.assertTrue(similar("Cursor", "Anysphere (Cursor)"))
        self.assertTrue(similar("AWS", "Amazon / AWS"))
        self.assertTrue(similar("Capital One Financial", "Capital One"))
        self.assertFalse(similar("Weights & Biases", "CoreWeave (incl. Weights & Biases)"))


if __name__ == "__main__":
    unittest.main()
