"""Spelling checks through libhunspell directly.

The ``hunspell -a`` subprocess is noticeably slow on *misses*: for an unknown
word it generates suggestions, which costs about 40 ms.  A whole-word buffer is
almost always a miss in one of the two dictionaries, so the subprocess path
turned every correction decision into 10-40 ms of latency.

Calling the C library is the same engine without the suggestion work:

    hunspell -a subprocess : 0.03 ms hit / 40 ms miss
    libhunspell via ctypes : 0.002 ms hit / 0.003 ms miss

The plain word list (``<name>.dic`` without an ``.aff``) stays as a fallback for
when the library or its dictionaries are missing.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import os
import shutil

HUNSPELL_DIRS = ("/usr/share/hunspell", "/usr/share/myspell", "/usr/share/myspell/dicts")

#: layout name (as in input:kb_layout) -> dictionary base name
DICT_ALIASES = {
    "us": "en_US",
    "gb": "en_GB",
    "ru": "ru_RU",
    "ua": "uk_UA",
    "de": "de_DE",
    "fr": "fr_FR",
    "es": "es_ES",
    "it": "it_IT",
    "pl": "pl_PL",
}

_LIB_NAMES = ("libhunspell-1.7.so.0", "libhunspell-1.7.so", "libhunspell.so.0", "libhunspell.so")


def resolve_dict_name(layout: str) -> str:
    layout = layout.strip()
    if not layout:
        return ""
    if "_" in layout or len(layout) > 3:
        return layout
    return DICT_ALIASES.get(layout, layout)


def system_dict_path(name: str) -> str | None:
    for directory in HUNSPELL_DIRS:
        base = os.path.join(directory, name)
        if os.path.exists(base + ".dic") and os.path.exists(base + ".aff"):
            return base
    return None


def _load_library():
    candidates = [ctypes.util.find_library("hunspell") or ""] + list(_LIB_NAMES)
    for name in candidates:
        if not name:
            continue
        try:
            lib = ctypes.CDLL(name)
        except OSError:
            continue
        lib.Hunspell_create.restype = ctypes.c_void_p
        lib.Hunspell_create.argtypes = [ctypes.c_char_p, ctypes.c_char_p]
        lib.Hunspell_destroy.restype = None
        lib.Hunspell_destroy.argtypes = [ctypes.c_void_p]
        lib.Hunspell_spell.restype = ctypes.c_int
        lib.Hunspell_spell.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
        return lib
    return None


_lib = _load_library()


class _HunspellDictionary:
    """One loaded .aff/.dic pair."""

    def __init__(self, base: str):
        if _lib is None:
            raise RuntimeError("libhunspell не найдена")
        self.base = base
        self._handle = _lib.Hunspell_create(
            (base + ".aff").encode(), (base + ".dic").encode()
        )
        if not self._handle:
            raise RuntimeError("не удалось загрузить словарь %s" % base)

    def check(self, word: str) -> bool:
        if not word:
            return False
        return _lib.Hunspell_spell(self._handle, word.encode("utf-8")) == 1

    def close(self) -> None:
        handle, self._handle = self._handle, None
        if handle and _lib is not None:
            _lib.Hunspell_destroy(handle)

    def __del__(self) -> None:  # pragma: no cover - best effort
        self.close()


class Speller:
    """Spelling checks for the configured dictionaries, in layout order."""

    def __init__(self, directory: str, names: list[str]):
        self.directory = directory
        self.names = list(names)
        self._engines: dict[str, _HunspellDictionary | set[str] | None] = {}
        #: small result cache: the same word is often checked twice per decision
        self._cache: dict[tuple[str, str], bool] = {}
        self._cache_limit = 4096

    # -- configuration ---------------------------------------------------
    def set_names(self, names: list[str]) -> None:
        if names != self.names:
            self.close()
            self.names = list(names)

    def close(self) -> None:
        for engine in self._engines.values():
            if isinstance(engine, _HunspellDictionary):
                engine.close()
        self._engines.clear()
        self._cache.clear()

    # -- resolution ------------------------------------------------------
    def _local_base(self, name: str) -> str | None:
        base = os.path.join(self.directory, name)
        if os.path.exists(base + ".dic") and os.path.exists(base + ".aff"):
            return base
        return None

    def base_for(self, name: str) -> str | None:
        return system_dict_path(name) or self._local_base(name)

    def _plain_path(self, name: str) -> str:
        return os.path.join(self.directory, name + ".dic")

    def _engine(self, name: str):
        if name in self._engines:
            return self._engines[name]
        engine = None
        base = self.base_for(name)
        if base and _lib is not None:
            try:
                engine = _HunspellDictionary(base)
            except (RuntimeError, OSError):
                engine = None
        if engine is None:
            engine = self._load_words(name) or None
        self._engines[name] = engine
        return engine

    def _load_words(self, name: str) -> set[str]:
        words: set[str] = set()
        try:
            with open(self._plain_path(name), encoding="utf-8", errors="replace") as handle:
                next(handle, None)
                for line in handle:
                    word = line.split("/", 1)[0].strip().lower()
                    if word:
                        words.add(word)
        except OSError:
            return set()
        return words

    # -- queries ---------------------------------------------------------
    def status(self) -> dict[str, str]:
        result = {}
        for name in self.names:
            if self.base_for(name) and _lib is not None:
                result[name] = "hunspell"
            elif os.path.exists(self._plain_path(name)):
                result[name] = "wordlist"
            else:
                result[name] = "missing"
        return result

    def missing(self) -> list[str]:
        return [name for name, kind in self.status().items() if kind == "missing"]

    def check(self, name: str, word: str) -> bool | None:
        """True/False for known dictionaries, None when the dictionary is absent."""
        word = word.strip()
        if not word:
            return None
        key = (name, word)
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        engine = self._engine(name)
        if engine is None:
            return None
        if isinstance(engine, set):
            result = word.lower() in engine
        else:
            result = engine.check(word)
        if len(self._cache) > self._cache_limit:
            self._cache.clear()
        self._cache[key] = result
        return result

    def check_any_case(self, name: str, word: str) -> bool:
        """A word is known if any of its case forms is: Российская/российская."""
        if self.check(name, word) is True:
            return True
        for variant in (word.lower(), word.capitalize(), word.upper()):
            if variant != word and self.check(name, variant) is True:
                return True
        return False
