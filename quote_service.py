"""Load and validate the local Meditations dataset."""

import json
from pathlib import Path
import random


class QuoteService:
    def __init__(self, quotes_file):
        self.quotes_file = Path(quotes_file)
        self.quotes = self.load_quotes()

    def load_quotes(self):
        try:
            with self.quotes_file.open(encoding="utf-8") as file:
                quotes = json.load(file)
        except (OSError, UnicodeError, json.JSONDecodeError):
            raise ValueError("Cannot read the quote file as UTF-8 JSON. Check its path and contents.") from None
        if not isinstance(quotes, list) or not quotes:
            raise ValueError("The quote file must contain a nonempty list of entries.")
        seen_ids = set()
        for index, quote in enumerate(quotes, 1):
            if not isinstance(quote, dict):
                raise ValueError(f"Quote entry {index} must be an object.")
            quote_id = quote.get("id")
            if type(quote_id) is not int or quote_id <= 0 or quote_id in seen_ids:
                raise ValueError(f"Quote entry {index} must have a unique positive integer ID.")
            seen_ids.add(quote_id)
            for key in ("quote", "summary", "modern_practical_application"):
                value = quote.get(key)
                if not isinstance(value, str) or not value.strip():
                    raise ValueError(f"Quote entry {index} needs a nonempty {key} string.")
        return quotes

    def get_random_quote(self):
        return random.choice(self.quotes)
