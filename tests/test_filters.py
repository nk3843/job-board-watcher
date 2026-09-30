import unittest

import yaml

from jobwatch.filters import TitleFilter, is_us_location


class TestLocation(unittest.TestCase):
    def test_us(self):
        for loc in ["Seattle, WA", "US-TX-Austin", "Remote - US", "Remote (United States)", "New York, NY",
                    "Indianapolis, IN", "Seattle", "London, UK; New York, NY", "U.S. Remote", "Remote", "3 Locations", ""]:
            self.assertTrue(is_us_location(loc), loc)

    def test_non_us(self):
        for loc in ["Bengaluru, Karnataka, India", "London, UK", "Toronto, ON, CA", "Remote - Canada",
                    "Berlin, Germany", "Remote - EMEA", "Singapore"]:
            self.assertFalse(is_us_location(loc), loc)

    def test_real_leaks_from_first_run(self):
        # Locations that slipped through on 2026-09-28
        for loc in ["1401-G-India: Floor 1 to 12, Hafeezpet Phoenix, Hyderabad", "Nottingham, Eng", "Nairobi, Kenya",
                    "Office - Bulgaria - Sofia", "Knutsford, Radbroke Hall", "IND CHNN 32 A&B 3FL STE C-D"]:
            self.assertFalse(is_us_location(loc), loc)

    def test_real_us_locations_from_first_run(self):
        for loc in ["McLean, VA", "5 Locations", "Dublin, OH", "GEORGIA - VIRTUAL - GA01",
                    "STORE SUPPORT CENTER, ATLANTA - 9090", "CIO KPop-Dallas (US152527)", "Irving Texas United States",
                    "IL - Chicago, 350 N. Orleans St 1300N", "USA - IL (Remote)", "Santa Clara, CALIFORNIA",
                    "Eden Prairie, MN United States of America", "Hybrid", "NerdWallet US (Remote)",
                    "San Francisco, CA | Seattle, WA", "Sunnyvale, CA; Toronto, CAN (Remote)", "O'Fallon, Missouri"]:
            self.assertTrue(is_us_location(loc), loc)

    def test_real_leaks_found_by_the_dashboard(self):
        # Non-US locations the dashboard's data checks caught on 2026-09-30
        for loc in ["Malmö", "Indore", "Kochi", "Bhubaneswar", "Coimbatore", "Farringdon", "Reykjavík",
                    "São Paulo", "Brussels", "Munich, DE", "Budapest, OR"]:
            self.assertFalse(is_us_location(loc), loc)

    def test_state_codes_that_are_also_country_codes(self):
        for loc in ["Wilmington, DE", "Portland, OR", "Dover, DE", "2 Locations (primary: Wilmington DE)",
                    "Vancouver, WA", "Paris, TX", "Lima, OH", "Manchester, NH"]:
            self.assertTrue(is_us_location(loc), loc)

    def test_ambiguous_city_names_stay_us(self):
        # "Athens" is also a city in Georgia and Ohio; without more context, keep the job rather than lose it.
        self.assertTrue(is_us_location("Athens"))

    def test_canada_state_code_ambiguity(self):
        self.assertFalse(is_us_location("Toronto, ON, CA"))
        self.assertTrue(is_us_location("Oakland, CA"))

    def test_title_used_when_location_is_blank(self):
        self.assertFalse(is_us_location("", title="Senior Applied AI Engineer (all genders)"))
        self.assertFalse(is_us_location("Remote", title="Account Manager - India"))
        self.assertFalse(is_us_location("", title="Mainframe Software Engineer - 6 to 10 Years- Chennai"))
        self.assertTrue(is_us_location("", title="AI / ML Engineer"))
        self.assertTrue(is_us_location("Seattle, WA", title="Engineer - India payments team"))  # location wins

    def test_extra_non_us_terms(self):
        from jobwatch.filters import LocationFilter
        f = LocationFilter(["radbroke hall", "moonli"])
        self.assertFalse(f.is_us("1/124, SHIVAJI GARDENS, MOONLI"))
        self.assertTrue(f.is_us("Remote"))

    def test_country_field(self):
        self.assertTrue(is_us_location("Remote", "US"))
        self.assertTrue(is_us_location("", "United States"))
        self.assertFalse(is_us_location("Remote", "IN"))
        self.assertTrue(is_us_location("Toronto; San Francisco", "CA"))  # a US city listed wins


class TestTitles(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open("config.example.yaml", encoding="utf-8") as f:
            cls.f = TitleFilter(yaml.safe_load(f))

    def test_tracks(self):
        c = self.f.classify
        self.assertEqual(c("Senior Backend Engineer"), ("backend",))
        self.assertEqual(c("Senior Software Engineer, Data Platform"), ("backend", "data"))
        self.assertEqual(c("Staff Machine Learning Engineer"), ("ai_ml",))
        self.assertEqual(c("AI Engineer, Agents"), ("ai_ml",))
        self.assertEqual(c("Member of Technical Staff"), ("backend",))
        self.assertEqual(c("Data Engineer II"), ("data",))
        self.assertIn("ai_ml", c("Forward Deployed Engineer"))
        self.assertIn("backend", c("Senior Software Engineer, Python"))

    def test_excluded(self):
        c = self.f.classify
        for t in ["Software Engineering Intern", "Engineering Manager, Backend", "iOS Software Engineer",
                  "Frontend Engineer", "Director of Data Engineering", "Software Engineer (TS/SCI)",
                  "Machine Learning Engineer - Cleared", "Account Executive", "AI Product Designer", "Data Analyst",
                  "AVP, Data Platform Engineering", "Mainframe Software Engineer"]:
            self.assertEqual(c(t), (), t)

    def test_word_boundaries(self):
        c = self.f.classify
        self.assertEqual(c("Maintenance Engineer"), ())   # 'ai' inside a word must not match
        self.assertEqual(c("Retail Engineer"), ())


if __name__ == "__main__":
    unittest.main()
