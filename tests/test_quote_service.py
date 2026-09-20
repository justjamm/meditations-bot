import json
from pathlib import Path
import tempfile
import unittest

from quote_service import QuoteService


class QuoteServiceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "quotes.json"
        self.entry = {
            "id": 1,
            "quote": "Be patient.",
            "summary": "Practice patience.",
            "modern_practical_application": "Pause before replying.",
        }

    def write(self, data):
        self.path.write_text(json.dumps(data), encoding="utf-8")

    def test_loads_and_selects_valid_quote(self):
        self.write([self.entry])
        self.assertEqual(QuoteService(self.path).get_random_quote(), self.entry)

    def test_rejects_invalid_datasets(self):
        invalid = [None, {}, [], [None], [self.entry, self.entry]]
        invalid.extend([{**self.entry, "id": value}] for value in (0, -1, True, "1"))
        for key in ("quote", "summary", "modern_practical_application"):
            invalid.extend([{**self.entry, key: value}] for value in (None, "", "  ", 1))
            invalid.append([{k: v for k, v in self.entry.items() if k != key}])
        for data in invalid:
            with self.subTest(data=data):
                self.write(data)
                with self.assertRaises(ValueError):
                    QuoteService(self.path)

    def test_unreadable_data_has_safe_error(self):
        for content in (None, b"not-json-private-content", b"\xff"):
            with self.subTest(content=content):
                if content is not None:
                    self.path.write_bytes(content)
                with self.assertRaisesRegex(ValueError, "Cannot read the quote file") as error:
                    QuoteService(self.path)
                self.assertNotIn("private-content", str(error.exception))


if __name__ == "__main__":
    unittest.main()
