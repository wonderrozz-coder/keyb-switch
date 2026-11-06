"""Language statistics from letter prefixes.

Motivation (measured on real Wikipedia text):

    prefix length   russian words recognised   english false positives
    2 letters                     92.8%                      1.1%
    3 letters                     98.7%                      0.4%
    4 letters                     99.9%                      0.0%
    whole word                    98.2%                      0.1%

The whole word is already good, but dictionaries miss inflected forms and
proper nouns (20% of a Russian text is unknown to hunspell: "россии",
"украины", "троицкого").  A prefix model recovers most of those, because the
first two or three letters in *key space* (the physical keys, not the letters)
almost never match between Russian and English.

The table is built once from frequency lists and cached in the data directory,
so no download or build time is paid on every start.
"""

from __future__ import annotations

import collections
import json
import os
import re
import time

#: how many of the most frequent words per language feed the table
FREQUENCY_LIMIT = 20000

#: prefix length used for the vote; 3 keeps 98.7% recall at 0.4% error
PREFIX_LENGTH = 3

#: a prefix votes for a language only above this share
THRESHOLD = 0.75

#: how many letters of the word take part in the vote
VOTE_LETTERS = 4

CACHE_NAME = "prefixes.json"
CACHE_VERSION = 1

FREQUENCY_URLS = {
    "ru": "https://raw.githubusercontent.com/hermitdave/FrequencyWords/master/content/2018/ru/ru_50k.txt",
    "en": "https://raw.githubusercontent.com/hermitdave/FrequencyWords/master/content/2018/en/en_50k.txt",
}

#: dictionary name (en_US) -> frequency list language (en)
LANG_OF_DICT = {
    "en_US": "en", "en_GB": "en", "en": "en",
    "ru_RU": "ru", "ru": "ru",
}

WORD_RE = re.compile(r"[^\W\d_]+", re.UNICODE)


class PrefixModel:
    """Per-layout prefix statistics in key space."""

    def __init__(self, cache_dir: str):
        self.cache_path = os.path.join(cache_dir, CACHE_NAME)
        #: layout index -> {prefix: share of words from this layout}
        self._shares: dict[int, dict[str, float]] = {}
        self._layouts_signature: tuple = ()
        self.built_at: float = 0.0

    # -- public API ------------------------------------------------------
    def ready(self) -> bool:
        return bool(self._shares)

    def prefers_other(self, word: str, active: int, other: int) -> bool:
        """True when the leading letters look like the `other` layout.

        All tables live in *canonical key space*: a word is reduced to the
        characters its own keys produce in the reference layout, so a Russian
        and an English word that collide on the same keys really do collide.
        """
        if not self._shares:
            return False
        other_shares = self._shares.get(other) or {}
        active_shares = self._shares.get(active) or {}
        if not other_shares:
            return False
        votes = 0
        for length in range(2, min(len(word), VOTE_LETTERS) + 1):
            prefix = word[:length]
            if other_shares.get(prefix, 0.0) >= THRESHOLD:
                votes += 1
            elif active_shares.get(prefix, 0.0) >= THRESHOLD:
                votes -= 1
        return votes > 0

    def info(self) -> dict:
        return {
            "ready": self.ready(),
            "prefixes": {index: len(table) for index, table in self._shares.items()},
            "built": bool(self.built_at),
        }

    # -- construction ----------------------------------------------------
    def build(self, layouts, layout_names: list[str], force: bool = False) -> bool:
        """Build the tables from frequency lists (cached on disk)."""
        reference = layouts.groups[0] if layouts.groups else None
        signature = tuple(layout_names) + tuple(g.name for g in layouts.groups)
        if not force and signature == self._layouts_signature and self._shares:
            return True

        cached = None if force else self._load_cache(signature)
        if cached is not None:
            self._shares = {int(k): v for k, v in cached.items()}
            self._layouts_signature = signature
            self.built_at = time.time()
            return True

        frequencies = {lang: _load_frequency(lang) for lang in set(
            LANG_OF_DICT.get(name, "") for name in layout_names
        ) - {""}}
        if not any(frequencies.values()):
            return False

        shares: dict[int, dict[str, float]] = {}
        for group in layouts.groups:
            lang = LANG_OF_DICT.get(layout_names[group.index] if group.index < len(layout_names) else "", "")
            words = frequencies.get(lang) or []
            if not words:
                shares[group.index] = {}
                continue
            # count prefixes of these words reduced to canonical key space
            counts: collections.Counter[str] = collections.Counter()
            for word in words:
                for prefix in _keyed_prefixes(word, group, reference):
                    counts[prefix] += 1
            totals: collections.Counter[str] = collections.Counter()
            for prefix, count in counts.items():
                totals[prefix[:2]] += count
            table = {}
            for prefix, count in counts.items():
                if len(prefix) == 2:
                    table[prefix] = 1.0
                else:
                    base = totals.get(prefix[:2], 0)
                    if base:
                        table[prefix] = count / base
            shares[group.index] = table

        # Merge both languages per prefix: share = how much of the prefix
        # evidence in canonical space belongs to this layout.
        merged: dict[int, dict[str, float]] = {}
        prefix_set: set[str] = set()
        for table in shares.values():
            prefix_set.update(table)
        letters = {index: {p: c for p, c in shares[index].items()} for index in shares}
        for index in shares:
            table = {}
            for prefix in prefix_set:
                mine = letters.get(index, {}).get(prefix, 0.0)
                total = sum(letters.get(other, {}).get(prefix, 0.0) for other in shares)
                if total > 0:
                    table[prefix] = mine / total
            merged[index] = table

        self._shares = merged
        self._layouts_signature = signature
        self.built_at = time.time()
        self._save_cache(signature)
        return bool(shares)

    # -- cache -----------------------------------------------------------
    def _load_cache(self, signature: tuple) -> dict | None:
        try:
            with open(self.cache_path, encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, ValueError):
            return None
        if data.get("version") != CACHE_VERSION:
            return None
        if data.get("signature") != list(signature):
            return None
        return data.get("shares")

    def _save_cache(self, signature: tuple) -> None:
        payload = {
            "version": CACHE_VERSION,
            "signature": list(signature),
            "prefix_length": PREFIX_LENGTH,
            "shares": {str(k): v for k, v in self._shares.items()},
        }
        try:
            os.makedirs(os.path.dirname(self.cache_path), exist_ok=True)
            with open(self.cache_path, "w", encoding="utf-8") as handle:
                json.dump(payload, handle)
        except OSError:
            pass


def _keyed_prefixes(word: str, group, reference) -> list[str]:
    """Prefixes of `word` reduced to canonical (reference layout) key space.

    A Russian word becomes the Latin letters its keys produce, so Russian and
    English candidates end up in the same alphabet and can be compared.
    """
    if reference is None:
        return []
    chars = []
    for char in word:
        key = group.key_for(char)
        if key is None:
            return []
        keycode, shift = key
        produced = reference.char_for(keycode, shift, False)
        if not produced:
            return []
        chars.append(produced)
    text = "".join(chars)
    return [text[:length] for length in range(2, min(len(text), VOTE_LETTERS) + 1)]


def _load_frequency(lang: str, limit: int = FREQUENCY_LIMIT) -> list[str]:
    """Most frequent words of a language, longest-lived cache on disk."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "dict", "freq_%s.txt" % lang)
    path = os.path.normpath(path)
    if os.path.exists(path):
        words = _read_frequency(path, limit)
        if words:
            return words
    url = FREQUENCY_URLS.get(lang)
    if not url:
        return []
    try:
        import urllib.request

        with urllib.request.urlopen(url, timeout=30) as response:
            raw = response.read().decode("utf-8", "replace")
    except OSError:
        return []
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(raw)
    except OSError:
        pass
    return _read_frequency(path, limit)


def _read_frequency(path: str, limit: int) -> list[str]:
    words: list[str] = []
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            for line in handle:
                parts = line.split()
                if not parts:
                    continue
                word = parts[0].lower()
                if len(word) >= 3 and WORD_RE.fullmatch(word):
                    words.append(word)
                if len(words) >= limit:
                    break
    except OSError:
        return []
    return words
