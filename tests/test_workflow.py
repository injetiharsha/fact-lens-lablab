import unittest
from unittest.mock import patch

from factlens_crew import run_factlens_crew
from factlens_crew.schemas import EvidenceItem


class FactLensCrewWorkflowTest(unittest.TestCase):
    def test_text_claim_returns_contract(self):
        live_row = EvidenceItem(
            title="NASA Solar System",
            url="https://nasa.gov/solar-system",
            snippet="Earth orbits the Sun.",
            source_type="trusted",
            credibility=90,
        )
        env = {"GEMINI_API_KEY": "", "FEATHERLESS_API_KEY": "", "TAVILY_API_KEY": ""}
        with patch.dict("os.environ", env):
            with patch("factlens_crew.tools._search_duckduckgo", return_value=[live_row]):
                result = run_factlens_crew(text="The Earth revolves around the Sun.", input_type="text")
        self.assertIn("run_id", result)
        self.assertIn("verdict", result)
        self.assertIn("confidence", result)
        self.assertIn("agent_reports", result)
        self.assertIn("events", result)
        self.assertGreaterEqual(len(result["agent_reports"]), 5)

    def test_no_live_evidence_stops_without_verdict(self):
        env = {"GEMINI_API_KEY": "", "FEATHERLESS_API_KEY": "", "TAVILY_API_KEY": ""}
        with patch.dict("os.environ", env):
            with patch("factlens_crew.tools._search_duckduckgo", return_value=[]):
                result = run_factlens_crew(text="The Earth revolves around the Sun.", input_type="text")
        self.assertEqual(result["verdict"], "needs_live_evidence")
        self.assertEqual(result["confidence"], 0)
        self.assertEqual(result["sources"], [])

    def test_empty_input_is_insufficient(self):
        with patch.dict("os.environ", {"GEMINI_API_KEY": ""}):
            result = run_factlens_crew(text="", input_type="text")
        self.assertEqual(result["verdict"], "insufficient_evidence")
        self.assertLess(result["confidence"], 50)


if __name__ == "__main__":
    unittest.main()
