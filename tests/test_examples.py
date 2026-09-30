"""The example configs in examples/ are documented in the README; make sure they keep doing what it says."""
import unittest

import yaml

from jobwatch.filters import TitleFilter
from jobwatch.labels import Labeler


class TestTpmSeattleExample(unittest.TestCase):
    def setUp(self):
        with open("examples/config.tpm-seattle.yaml", encoding="utf-8") as f:
            self.cfg = yaml.safe_load(f)
        self.titles = TitleFilter(self.cfg)
        self.labels = Labeler(self.cfg)

    def test_keeps_tpm_titles(self):
        for title in ["Technical Program Manager, AWS", "Senior TPM - Azure Networking", "Principal Program Manager",
                      "Engineering Program Manager, Silicon", "Technical Program Management Lead",
                      "Technical Principal Program Manager"]:
            self.assertEqual(self.titles.classify(title), ("TPM",), title)

    def test_drops_other_roles(self):
        for title in ["Software Engineer II", "Product Manager, Payments", "Engineering Manager, Backend",
                      "Director, Technical Program Management", "Technical Program Manager Intern",
                      "Marketing Program Manager", "Customer Success Program Manager",
                      "Program Manager, Global Partnerships and Channels"]:
            self.assertEqual(self.titles.classify(title), (), title)

    def test_seattle_area_and_remote_first(self):
        for loc in ["Seattle, WA", "Bellevue, WA", "United States, Washington, Redmond", "Remote - US"]:
            self.assertTrue(self.labels.highlight(loc), loc)
        self.assertFalse(self.labels.highlight("Austin, TX"))
        self.assertEqual(self.labels.resume("Technical Program Manager"), "")

    def test_workday_and_eightfold_search_for_tpm_roles(self):
        self.assertIn("technical program manager", self.cfg["workday_queries"])


if __name__ == "__main__":
    unittest.main()
