"""Tests that do not need a running compositor or /dev/input access."""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kbswitch import dictionaries, hypr  # noqa: E402
from kbswitch.layout import Layouts, XkbNames  # noqa: E402

DATA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "dict")


def names() -> XkbNames:
    try:
        return XkbNames(
            model=hypr.str_option("input:kb_model") or "pc105+inet",
            layout=hypr.str_option("input:kb_layout") or "us,ru",
            variant=hypr.str_option("input:kb_variant"),
            options=hypr.str_option("input:kb_options"),
        )
    except hypr.HyprlandError:
        return XkbNames(model="pc105+inet", layout="us,ru")


class LayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.layouts = Layouts(names())
        cls.en = cls.layouts.group_for(0)
        cls.ru = cls.layouts.group_for(1)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.layouts.close()

    def test_two_groups(self) -> None:
        self.assertGreaterEqual(len(self.layouts.groups), 2)

    def test_known_translations(self) -> None:
        cases = {
            "ghbdtn": "привет",
            "vfvf": "мама",
            "ntcn": "тест",
            "ltkfq": "делай",
            "rhfcbdsq": "красивый",
        }
        for source, expected in cases.items():
            with self.subTest(source=source):
                self.assertEqual(self.layouts.translate(source, self.en, self.ru), expected)
                self.assertEqual(self.layouts.translate(expected, self.ru, self.en), source)

    def test_unmappable_returns_none(self) -> None:
        self.assertIsNone(self.layouts.translate("привет", self.en, self.ru))
        self.assertIsNone(self.layouts.translate("ghbdtn", self.ru, self.en))

    def test_reverse_mapping_roundtrip(self) -> None:
        for group in (self.en, self.ru):
            for char, (keycode, shift) in list(group.reverse.items())[:200]:
                self.assertEqual(group.char_for(keycode, shift, False), char)


class DictionaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.speller = dictionaries.Speller(DATA, ["en_US", "ru_RU"])

    @classmethod
    def tearDownClass(cls) -> None:
        cls.speller.close()

    def test_words(self) -> None:
        self.assertIs(self.speller.check("ru_RU", "привет"), True)
        self.assertIs(self.speller.check("ru_RU", "мама"), True)
        self.assertIs(self.speller.check("ru_RU", "ghbdtn"), False)
        self.assertIs(self.speller.check("en_US", "hello"), True)
        self.assertIs(self.speller.check("en_US", "ghbdtn"), False)

    def test_unknown_dictionary(self) -> None:
        self.assertIsNone(self.speller.check("xx_XX", "test"))

    def test_resolve_names(self) -> None:
        self.assertEqual(dictionaries.resolve_dict_name("us"), "en_US")
        self.assertEqual(dictionaries.resolve_dict_name("ru"), "ru_RU")
        self.assertEqual(dictionaries.resolve_dict_name("de"), "de_DE")
        self.assertEqual(dictionaries.resolve_dict_name("en_GB"), "en_GB")


if __name__ == "__main__":
    unittest.main(verbosity=2)
