"""Word frequency ranks, used to tell "a real, common word" from "a valid form".

Hunspell answers "is this a legal word form", which is too generous for layout
detection: ``еру`` is a legal (but extremely rare) genitive of ``ерунда``, so a
dictionary-only test refuses to turn ``еру`` into ``the``.

Frequency ranks separate the two questions:

    rank 3  common word (top 5 000)      -> certainly this language
    rank 2  known word (top 20 000)      -> almost certainly
    rank 1  valid form, not in the list  -> weak evidence
    rank 0  unknown                      -> no evidence

Then a layout is chosen by comparing the evidence for both candidates, which
handles ``еру``/``the`` (1 vs 3), ``here``/``руку`` (3 vs 3, tie -> no change)
and ``ghbdtn``/``привет`` (0 vs 3) with one rule.
"""

from __future__ import annotations

import os

CACHE_NAMES = {
    "en": "freq_en.txt",
    "ru": "freq_ru.txt",
}

LANG_OF_DICT = {
    "en_US": "en",
    "en_GB": "en",
    "en": "en",
    "ru_RU": "ru",
    "ru": "ru",
}

#: rank boundaries: (max index into the frequency list, score)
COMMON = 4_000
KNOWN = 20_000

SCORE_COMMON = 3
SCORE_KNOWN = 2
SCORE_VALID = 1
SCORE_NONE = 0


class Frequency:
    """Frequency ranks per language, loaded from the cached word lists."""

    def __init__(self, directory: str):
        self.directory = directory
        self._ranks: dict[str, dict[str, int]] = {}
        #: language -> {word without one letter: [words]} for typo detection
        self._deletions: dict[str, dict[str, list[str]]] = {}

    def lang_for(self, dict_name: str) -> str:
        return LANG_OF_DICT.get(dict_name, "")

    def _load(self, lang: str) -> dict[str, int]:
        if lang in self._ranks:
            return self._ranks[lang]
        ranks: dict[str, int] = {}
        path = os.path.join(self.directory, CACHE_NAMES.get(lang, ""))
        try:
            with open(path, encoding="utf-8", errors="replace") as handle:
                for index, line in enumerate(handle):
                    parts = line.split()
                    if not parts:
                        continue
                    word = parts[0]
                    if word not in ranks:
                        ranks[word] = index
                    if index >= KNOWN:
                        break
        except OSError:
            ranks = {}
        self._ranks[lang] = ranks
        return ranks

    def rank(self, dict_name: str, word: str) -> int | None:
        lang = self.lang_for(dict_name)
        if not lang:
            return None
        return self._load(lang).get(word)

    def available(self, dict_name: str) -> bool:
        return bool(self._load(self.lang_for(dict_name)))

    # -- typo detection --------------------------------------------------
    def _deletion_index(self, lang: str) -> dict[str, list[str]]:
        """Index of common words by the word with one letter removed.

        With it, "one keystroke away" is O(len) hash lookups: a substitution
        shares a deletion, an insertion or a deletion is the other word itself.
        """
        if lang in self._deletions:
            return self._deletions[lang]
        index: dict[str, list[str]] = {}
        for word in self._load(lang):
            rank = self._ranks[lang].get(word)
            if rank is None or rank >= COMMON:
                continue
            for position in range(len(word)):
                index.setdefault(word[:position] + word[position + 1:], []).append(word)
        self._deletions[lang] = index
        return index

    def best_rank(self, dict_name: str, word: str) -> int | None:
        """Most frequent rank among the case variants of `word`."""
        best = None
        for variant in (word, word.lower(), word.capitalize()):
            rank = self.rank(dict_name, variant)
            if rank is not None and (best is None or rank < best):
                best = rank
        return best

    def nearest_common_rank(self, dict_name: str, word: str) -> int | None:
        """Rank of the most plausible typo target, if any (lower is likelier)."""
        best = None
        for candidate in self.near_common(dict_name, word):
            rank = self.best_rank(dict_name, candidate)
            if rank is not None and (best is None or rank < best):
                best = rank
        return best

    def near_common(self, dict_name: str, word: str, limit: int = 3) -> list[str]:
        """Common words one edit away from `word` (nothing for very short words).

        "учше" is one letter from "лучше" and "еру" is one from "ему", so both
        are far more likely to be typos than wrong-layout text.
        """
        lang = self.lang_for(dict_name)
        lowered = word.lower()
        if not lang or len(lowered) < 4:
            return []
        index = self._deletion_index(lang)
        found: list[str] = []
        candidates: list[str] = []
        # Same length (one substitution) needs the *other* words' deletions:
        # walk the words hanging off each of our own deletions.
        for position in range(len(lowered)):
            candidates.extend(index.get(lowered[:position] + lowered[position + 1:], ()))
        # One insertion or one deletion: those are keyed by our word itself.
        candidates.extend(index.get(lowered, ()))
        for candidate in candidates:
            if candidate != lowered and candidate not in found:
                found.append(candidate)
                if len(found) >= limit:
                    break
        return found


def score(frequency: Frequency, dict_name: str, word: str, speller) -> int:
    """Evidence that `word` belongs to the language of `dict_name`."""
    if not word:
        return SCORE_NONE
    for variant in (word, word.lower(), word.capitalize()):
        rank = frequency.rank(dict_name, variant)
        if rank is not None:
            return SCORE_COMMON if rank < COMMON else SCORE_KNOWN
    if speller.check_any_case(dict_name, word):
        return SCORE_VALID
    return SCORE_NONE
