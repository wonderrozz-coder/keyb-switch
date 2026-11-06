"""Engine behaviour driven by synthetic key events (no /dev/input needed)."""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from evdev import ecodes  # noqa: E402

from kbswitch import hypr  # noqa: E402
from kbswitch.engine import Engine, State  # noqa: E402
from kbswitch.layout import Layouts  # noqa: E402


class EngineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.state = State()
        cls.en = cls.state.layouts.group_for(0)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.state.speller.close()
        cls.state.layouts.close()

    def setUp(self) -> None:
        self.engine = Engine(self.state)
        self.engine.suppress_until = 0
        self.state.config.autocorrect = True
        self.state.config.autocorrect_blacklist = []
        # live mode acts while typing; these tests drive the buffer directly
        self.state.config.live = False
        self.state.watcher.set_layout_index(0)
        try:
            hypr.switch_layout(0)
        except hypr.HyprlandError:
            pass

    # -- helpers ---------------------------------------------------------
    def press(self, evdev_code: int) -> None:
        self.engine.handle_key(evdev_code, 1)
        self.engine.handle_key(evdev_code, 0)

    def type_word(self, word: str, group=None) -> None:
        group = group or self.en
        for char in word:
            keycode, shift = group.reverse[char]
            if shift:
                self.engine.handle_key(ecodes.KEY_LEFTSHIFT, 1)
            self.press(keycode - 8)
            if shift:
                self.engine.handle_key(ecodes.KEY_LEFTSHIFT, 0)

    def buffer_text(self) -> str:
        return "".join(entry[0] for entry in self.engine.buffer)

    # -- tests -----------------------------------------------------------
    def test_buffer_collects_word(self) -> None:
        self.type_word("hello")
        self.assertEqual(self.buffer_text(), "hello")

    def test_space_clears_buffer(self) -> None:
        self.type_word("hello")
        self.press(ecodes.KEY_SPACE)
        self.assertEqual(self.buffer_text(), "")

    def test_navigation_clears_buffer(self) -> None:
        for key in (ecodes.KEY_LEFT, ecodes.KEY_ESC, ecodes.KEY_HOME, ecodes.KEY_TAB):
            with self.subTest(key=key):
                self.type_word("hello")
                self.press(key)
                self.assertEqual(self.buffer_text(), "")

    def test_modifiers_do_not_break_word(self) -> None:
        self.type_word("hel")
        self.engine.handle_key(ecodes.KEY_LEFTCTRL, 1)
        self.engine.handle_key(ecodes.KEY_LEFTCTRL, 0)
        self.type_word("lo")
        self.assertEqual(self.buffer_text(), "hello")

    def test_ctrl_shortcut_breaks_word(self) -> None:
        self.type_word("ghbdtn")
        self.engine.handle_key(ecodes.KEY_LEFTCTRL, 1)
        self.press(ecodes.KEY_V)
        self.engine.handle_key(ecodes.KEY_LEFTCTRL, 0)
        self.assertEqual(self.buffer_text(), "")

    def test_shift_produces_uppercase(self) -> None:
        self.engine.handle_key(ecodes.KEY_LEFTSHIFT, 1)
        self.press(ecodes.KEY_H)
        self.engine.handle_key(ecodes.KEY_LEFTSHIFT, 0)
        self.assertEqual(self.buffer_text(), "H")

    def test_capslock_produces_uppercase(self) -> None:
        self.engine.handle_key(ecodes.KEY_CAPSLOCK, 1)
        self.type_word("hello")
        self.engine.handle_key(ecodes.KEY_CAPSLOCK, 0)
        self.assertEqual(self.buffer_text(), "HELLO")

    def test_digits_stay_inside_the_word(self) -> None:
        # "nt,z" is "тебя": punctuation is a letter of the *other* layout
        self.type_word("nt,z")
        self.assertEqual(self.buffer_text(), "nt,z")

    def test_punctuation_does_not_break_a_typed_word(self) -> None:
        self.type_word("ghbdtn")
        self.press(ecodes.KEY_COMMA)
        self.assertEqual(self.buffer_text(), "ghbdtn,")

    def test_space_clears_after_punctuation(self) -> None:
        self.type_word("ghbdtn")
        self.press(ecodes.KEY_COMMA)
        self.press(ecodes.KEY_SPACE)
        self.assertEqual(self.buffer_text(), "")

    def test_punctuated_word_is_corrected(self) -> None:
        self.type_word("nt,z")          # "тебя" typed in the wrong layout
        result = self.engine.commit(separator=ecodes.KEY_SPACE, separator_text=" ")
        self.assertIsNotNone(result)
        self.assertEqual(result.get("to"), "тебя")

    def test_two_letter_words_are_checked(self) -> None:
        # "пщ" -> "go": short words matter, evidence decides
        self.state.watcher.set_layout_index(1)
        group = self.state.layouts.group_for(1)
        self.type_word("пщ", group)
        self.assertEqual(self.engine._judge("пщ", group, self.en), "exact")

    def test_correct_word_is_left_alone(self) -> None:
        self.assertIsNone(self.engine._judge("hello", self.en, self.state.layouts.group_for(1)))

    def test_autocorrect_can_be_disabled(self) -> None:
        self.state.config.autocorrect = False
        self.type_word("ghbdtn")
        self.assertIsNone(self.engine.commit())

    def test_blacklist_blocks_window(self) -> None:
        self.state.config.autocorrect_blacklist = ["^Blocked$"]
        self.engine._window_class = lambda: "Blocked"  # type: ignore[assignment]
        self.type_word("ghbdtn")
        self.assertIsNone(self.engine.commit())

    def test_translation_roundtrip(self) -> None:
        ru = self.state.layouts.group_for(1)
        self.assertEqual(self.state.layouts.translate("ghbdtn", self.en, ru), "привет")
        self.assertEqual(self.state.layouts.translate("привет", ru, self.en), "ghbdtn")


class TypoGuardTests(unittest.TestCase):
    """A misspelling must not be "corrected" into another layout's word."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.state = State()
        cls.en = cls.state.layouts.group_for(0)
        cls.ru = cls.state.layouts.group_for(1)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.state.speller.close()
        cls.state.layouts.close()

    def judge(self, word, group, other):
        engine = Engine(self.state)
        self.state.watcher.set_layout_index(group.index)
        return engine._judge(word, group, other)

    def test_typo_of_common_word_is_left_alone(self) -> None:
        # "учше" is one letter from "лучше"; converting it to "exit" is wrong
        self.assertIsNone(self.judge("учше", self.ru, self.en))

    def test_layout_mistake_still_wins_over_typo(self) -> None:
        # "ерфе" is also one letter from a Russian word, but "that" is common
        self.assertEqual(self.judge("ерфе", self.ru, self.en), "exact")

    def test_single_letters(self) -> None:
        # "ш" is a Russian word and "i" is the English one: clear winner
        self.assertEqual(self.judge("ш", self.ru, self.en), "exact")
        # "z" -> "я" is a single letter with no frequency evidence on either
        # side, so it is deliberately left alone now
        self.assertIsNone(self.judge("z", self.en, self.ru))

    def test_two_letter_words_need_a_clear_margin(self) -> None:
        # "пщ"/"go" is a real fix, "gj"/"по" is an abbreviation: both are two
        # letters, so the margin decides
        self.assertEqual(self.judge("пщ", self.ru, self.en), "exact")
        self.assertEqual(self.judge("yt", self.en, self.ru), "exact")

    def test_correct_words_untouched(self) -> None:
        self.assertIsNone(self.judge("hello", self.en, self.ru))
        self.assertIsNone(self.judge("привет", self.ru, self.en))


class SelectionTests(unittest.TestCase):
    """Text selected and converted through the primary selection."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.state = State()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.state.speller.close()
        cls.state.layouts.close()

    def setUp(self) -> None:
        self.state.config.notifications = False
        self.state.watcher.set_layout_index(0)
        self.original = self.state.selection

    def tearDown(self) -> None:
        self.state.selection = self.original

    def fake(self, text):
        class Fake:
            written = None
            def __init__(self, value): self.text = value
            def read(self, timeout=1.5): return self.text
            def write(self_inner, value, timeout=1.5):
                Fake.written = value
                return True
            def clear(self): pass
        return Fake(text)

    def test_phrase_is_converted(self) -> None:
        from kbswitch.engine import Engine
        self.state.selection = self.fake("ghbdtn, vbh")
        result = Engine(self.state)._correct_selection()
        self.assertTrue(result and result.get("ok"))
        self.assertEqual(result["to"], "привет, мир")

    def test_correct_text_is_not_converted(self) -> None:
        from kbswitch.engine import Engine
        self.state.selection = self.fake("hello")
        self.assertIsNone(Engine(self.state)._correct_selection())


class DoubleShiftTests(unittest.TestCase):
    """The double Shift must never paste a stale word."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.state = State()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.state.speller.close()
        cls.state.layouts.close()

    def setUp(self) -> None:
        self.engine = Engine(self.state)
        self.state.config.autocorrect = False
        self.state.watcher.set_layout_index(0)

    def test_no_word_means_no_action(self) -> None:
        self.engine.buffer.clear()
        self.engine.last_word = None
        self.assertFalse(self.engine.force_last_word().get("ok"))

    def test_old_word_is_not_reused(self) -> None:
        self.engine.last_word = {
            "word": "ghbdtn",
            "entries": [("g", 34, False), ("h", 35, False)],
            "separator": None,
            "separator_text": None,
            "at": 0.0,          # ancient
        }
        self.engine.buffer.clear()
        result = self.engine.force_last_word()
        self.assertFalse(result.get("ok"))


class ManualCorrectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.state = State()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.state.speller.close()
        cls.state.layouts.close()

    def test_no_word_no_correction(self) -> None:
        engine = Engine(self.state)
        result = engine.manual_correct()
        self.assertFalse(result.get("ok"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
