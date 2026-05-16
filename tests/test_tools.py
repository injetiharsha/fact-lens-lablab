import os
import unittest
from unittest.mock import patch

from factlens_crew.tools import search_primary_sources, search_web


class SearchToolsTest(unittest.TestCase):
    def test_search_web_uses_duckduckgo_before_fallbacks(self):
        env = {"GEMINI_API_KEY": "", "TAVILY_API_KEY": "", "FACTLENS_ALLOW_OFFLINE_FALLBACK": "0"}
        with patch.dict(os.environ, env):
            with patch("factlens_crew.tools._search_duckduckgo") as ddg:
                ddg.return_value = []
                rows = search_web("The Earth revolves around the Sun")
                ddg.assert_called_once()
                self.assertEqual(rows, [])

    def test_offline_fallback_requires_explicit_dev_flag(self):
        env = {"GEMINI_API_KEY": "", "TAVILY_API_KEY": "", "FACTLENS_ALLOW_OFFLINE_FALLBACK": "1"}
        with patch.dict(os.environ, env):
            with patch("factlens_crew.tools._search_duckduckgo", return_value=[]):
                rows = search_web("The Earth revolves around the Sun")
        self.assertEqual(rows[0].source_type, "offline")

    def test_primary_source_search_returns_rows(self):
        env = {"GEMINI_API_KEY": "", "TAVILY_API_KEY": "", "FACTLENS_ALLOW_OFFLINE_FALLBACK": "0"}
        with patch.dict(os.environ, env):
            with patch("factlens_crew.tools._search_duckduckgo", return_value=[]):
                rows = search_primary_sources("NASA says the Earth orbits the Sun")
        self.assertEqual(rows, [])


if __name__ == "__main__":
    unittest.main()
