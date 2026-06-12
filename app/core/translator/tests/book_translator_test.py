import os
import sys
import types
import unittest
from unittest.mock import Mock


APP_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if APP_ROOT not in sys.path:
    sys.path.insert(0, APP_ROOT)

if "app.core.translator" not in sys.modules:
    translator_pkg = types.ModuleType("app.core.translator")
    translator_pkg.__path__ = [os.path.join(APP_ROOT, "core", "translator")]
    sys.modules["app.core.translator"] = translator_pkg

from app.core.translator.analyser.adventures_analyser import AdventuresAnalyser
from app.core.translator.analyser.books_analyser import BooksAnalyser


class TestTranslatorFromCredits(unittest.TestCase):
    def setUp(self):
        self.mock_dictionary = Mock()
        self.mock_dictionary.dumpLockedEntries.return_value = {}
        self.mock_dictionary.get_bunch.return_value = {}

    def test_adventure_analyser_uses_translation_credit(self):
        self.mock_dictionary.get_credits.return_value = [
            {"job_type": "翻译", "names": "数据库冒险译者，另一位译者"},
            {"job_type": "校对", "names": "校对者"},
        ]
        analyser = AdventuresAnalyser(self.mock_dictionary, "adventure/adventure-lmop.json")
        res_dict, _ = analyser.process({"adventure": [{"id": "LMoP"}, {"id": "UNKNOWN"}]})

        self.assertEqual(res_dict["adventure"][0]["translator"], "数据库冒险译者等")
        self.assertEqual(res_dict["adventure"][1]["translator"], "数据库冒险译者等")

    def test_book_analyser_defaults_to_machine_translation_when_no_credit(self):
        self.mock_dictionary.get_credits.return_value = []
        analyser = BooksAnalyser(self.mock_dictionary, "book/book-phb.json")
        res_dict, _ = analyser.process({"book": [{"id": "PHB"}, {"id": "UNKNOWN"}]})

        self.assertEqual(res_dict["book"][0]["translator"], "机翻")
        self.assertEqual(res_dict["book"][1]["translator"], "机翻")

    def test_prefers_translation_job_type_when_multiple_credits_exist(self):
        self.mock_dictionary.get_credits.return_value = [
            {"job_type": "编辑", "names": "编辑者"},
            {"job_type": "翻译", "names": "正式译者"},
            {"job_type": "校对", "names": "校对者"},
        ]

        analyser = BooksAnalyser(self.mock_dictionary, "book/book-phb.json")

        self.assertEqual(analyser.get_translator_from_credits(), "正式译者")

    def test_formats_multi_translator_names_with_deng(self):
        analyser = BooksAnalyser(self.mock_dictionary, "book/book-phb.json")

        self.assertEqual(analyser.format_translator_names("东风，萧永念，Amethyst Dragonlord"), "东风等")
        self.assertEqual(analyser.format_translator_names("Eygma、戈蓝"), "Eygma等")
        self.assertEqual(analyser.format_translator_names("不全书"), "不全书")


if __name__ == "__main__":
    unittest.main()
