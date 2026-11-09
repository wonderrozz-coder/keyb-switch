"""Core autocorrection engine: word buffer, detection, correction."""

from __future__ import annotations

import os
import re
import threading
import time

from evdev import ecodes

from . import config as cfg
from . import hypr
from .dictionaries import Speller, resolve_dict_name
from .prefixes import PrefixModel
from .selection import Selection, Unavailable as SelectionUnavailable
from .frequency import (
    Frequency,
    score as frequency_score,
    SCORE_COMMON,
    SCORE_KNOWN,
    SCORE_NONE,
    SCORE_VALID,
)
from .watcher import StaticLayouts, Watcher
from .inject import Injector, Unavailable
from .layout import Layouts, XkbNames

#: characters that belong to a word
WORD_CHAR = re.compile(r"[^\W\d_]", re.UNICODE)

#: keys that cancel the current word without terminating it
NAVIGATION_KEYS = {
    ecodes.KEY_HOME,
    ecodes.KEY_END,
    ecodes.KEY_LEFT,
    ecodes.KEY_RIGHT,
    ecodes.KEY_UP,
    ecodes.KEY_DOWN,
    ecodes.KEY_PAGEUP,
    ecodes.KEY_PAGEDOWN,
    ecodes.KEY_INSERT,
    ecodes.KEY_DELETE,
    ecodes.KEY_ESC,
}

#: keys that finish a word and are re-typed after a correction
TERMINATOR_KEYS = {ecodes.KEY_SPACE, ecodes.KEY_ENTER, ecodes.KEY_KPENTER}

#: punctuation finishes a word too: "ghbdtn," is still the word "ghbdtn"
PUNCTUATION_KEYS = {
    ecodes.KEY_COMMA,
    ecodes.KEY_DOT,
    ecodes.KEY_SEMICOLON,
    ecodes.KEY_APOSTROPHE,
    ecodes.KEY_SLASH,
    ecodes.KEY_BACKSLASH,
    ecodes.KEY_LEFTBRACE,
    ecodes.KEY_RIGHTBRACE,
    ecodes.KEY_MINUS,
    ecodes.KEY_EQUAL,
    ecodes.KEY_GRAVE,
    ecodes.KEY_1, ecodes.KEY_2, ecodes.KEY_3, ecodes.KEY_4, ecodes.KEY_5,
    ecodes.KEY_6, ecodes.KEY_7, ecodes.KEY_8, ecodes.KEY_9, ecodes.KEY_0,
    ecodes.KEY_KP1, ecodes.KEY_KP2, ecodes.KEY_KP3, ecodes.KEY_KP4, ecodes.KEY_KP5,
    ecodes.KEY_KP6, ecodes.KEY_KP7, ecodes.KEY_KP8, ecodes.KEY_KP9, ecodes.KEY_KP0,
    ecodes.KEY_KPCOMMA, ecodes.KEY_KPDOT, ecodes.KEY_KPSLASH,
}

#: keys that finish a word but are not re-typed (focus changes, etc.)
PLAIN_BOUNDARY_KEYS = {ecodes.KEY_TAB}

#: characters the plain separators insert, for re-typing after a correction
SEPARATOR_CHARS = {
    ecodes.KEY_SPACE: " ",
    ecodes.KEY_ENTER: "\n",
    ecodes.KEY_KPENTER: "\n",
}

SHIFT_KEYS = {ecodes.KEY_LEFTSHIFT, ecodes.KEY_RIGHTSHIFT}
CAPS_KEYS = {ecodes.KEY_CAPSLOCK}
#: while these are held the keys are shortcuts, not typed text
CTRL_KEYS = {ecodes.KEY_LEFTCTRL, ecodes.KEY_RIGHTCTRL}
META_KEYS = {ecodes.KEY_LEFTMETA, ecodes.KEY_RIGHTMETA}
ALTGR_KEYS = {ecodes.KEY_RIGHTALT}
IGNORED_MODIFIER_KEYS = (
    SHIFT_KEYS
    | ALTGR_KEYS
    | {
        ecodes.KEY_LEFTCTRL,
        ecodes.KEY_RIGHTCTRL,
        ecodes.KEY_LEFTALT,
        ecodes.KEY_LEFTMETA,
        ecodes.KEY_RIGHTMETA,
        ecodes.KEY_CAPSLOCK,
        ecodes.KEY_NUMLOCK,
    }
)


def xkb_keycode(evdev_code: int) -> int:
    return evdev_code + 8


def _split_trailing_punctuation(word: str, entries: list[tuple[str, int, bool]]):
    """Separate a typed word from trailing punctuation: "ghbdtn," -> "ghbdtn" + ","."""
    index = len(entries)
    while index > 0 and not WORD_CHAR.match(entries[index - 1][0]):
        index -= 1
    return word[:index], entries[:index], word[index:]


#: words inside a selection
_SPLIT_RE = re.compile(r"[^\W\d_]+", re.UNICODE)


def _translate_any(text: str, layouts, source, target, phrase: bool = False) -> str | None:
    """Translate `text` between layouts, preserving case and punctuation.

    Works on whole phrases: every letter is mapped through the key tables, so
    "ghbdtn, vbh!" becomes "привет, мир!".
    """
    out = []
    for char in text:
        if char.isalpha():
            lower = char.lower()
            mapped = layouts.translate(lower, source, target)
            if mapped is None:
                return None
            out.append(mapped.upper() if char.isupper() else mapped)
            continue
        if char in " \n\t":
            out.append(char)
            continue
        if phrase and not char.isalnum():
            # in a sentence a comma is a comma, not the letter "б"
            out.append(char)
            continue
        # punctuation may itself be a letter of the other layout ("," -> "б")
        key = source.key_for(char)
        if key is not None:
            keycode, shift = key
            produced = target.char_for(keycode, shift, False)
            out.append(produced if produced else char)
        else:
            out.append(char)
    return "".join(out)


class _CaseMapper:
    """Carry the user's capitalisation over to the corrected text."""

    def __init__(self, source: str, target: str):
        self.source = source
        self.target = target

    def apply(self) -> str:
        out = []
        for index, char in enumerate(self.target):
            source = self.source[index] if index < len(self.source) else ""
            out.append(char.upper() if source.isupper() else char)
        return "".join(out)


def _letters_only(text: str) -> str:
    return "".join(char for char in text if WORD_CHAR.match(char))


def _case_variants(text: str) -> list[str]:
    """Lower-case plus the capitalised form, which dictionaries often differ on."""
    variants = [text]
    capitalised = text.capitalize()
    if capitalised != text:
        variants.append(capitalised)
    return variants


def state_dir() -> str:
    base = os.environ.get("XDG_STATE_HOME") or os.path.join(
        os.path.expanduser("~"), ".local", "state"
    )
    return os.path.join(base, cfg.APP)


def log_path() -> str:
    return os.path.join(state_dir(), cfg.APP + ".log")


def notify(title: str, body: str = "", urgency: str = "normal") -> None:
    try:
        import shutil
        import subprocess

        if not shutil.which("notify-send"):
            return
        command = ["notify-send", "--app-name", cfg.APP]
        if urgency:
            command += ["-u", urgency]
        command.append(title)
        if body:
            command.append(body)
        subprocess.run(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
            check=False,
        )
    except (OSError, ValueError):
        pass


class State:
    """Everything expensive: xkb tables, spellers, injector, config."""

    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.config = cfg.Config.load()
        self.watcher: Watcher | StaticLayouts = StaticLayouts()
        self.layout_names = hypr.layout_names() or ["us"]
        self.dict_names = self._dict_names(self.layout_names)
        self.layouts: Layouts | None = None
        self.xkb_names = self._xkb_names()
        self.speller = Speller(cfg.dict_dir(), self.dict_names)
        self.prefixes = PrefixModel(cfg.dict_dir())
        self.frequency = Frequency(cfg.dict_dir())
        try:
            self.selection: Selection | None = Selection()
        except SelectionUnavailable:
            self.selection = None
        self.injector: Injector | None = None
        self.inject_error: str | None = None
        self._build_layouts()
        self._build_injector()

    def _dict_names(self, layout_names: list[str]) -> list[str]:
        names = [resolve_dict_name(name) for name in layout_names]
        configured = list(self.config.dicts or [])
        result: list[str] = []
        for index, name in enumerate(names):
            if name:
                result.append(name)
            elif index < len(configured):
                result.append(configured[index])
        return result or configured or ["en_US", "ru_RU"]

    def _xkb_names(self) -> XkbNames:
        try:
            layout = hypr.str_option("input:kb_layout") or ",".join(self.layout_names)
            variant = hypr.str_option("input:kb_variant")
            options = hypr.str_option("input:kb_options")
            model = hypr.str_option("input:kb_model") or "pc105+inet"
        except hypr.HyprlandError:
            layout, variant, options, model = ",".join(self.layout_names), "", "", "pc105"
        return XkbNames(model=model, layout=layout, variant=variant, options=options)

    def _build_layouts(self) -> None:
        with self.lock:
            if self.layouts is not None:
                self.layouts.close()
                self.layouts = None
            self.layouts = Layouts(self.xkb_names)
            self.speller.set_names(self.dict_names)
            self._build_prefixes()

    def _build_prefixes(self) -> None:
        """Build the prefix statistics (cached), never block startup on failure."""
        try:
            ok = self.prefixes.build(self.layouts, self.dict_names)
        except (OSError, ValueError):
            ok = False
        if not ok:
            return
        self.speller.prefixes_ready = True

    def _build_injector(self) -> None:
        self.injector = None
        self.inject_error = None
        try:
            self.injector = Injector(self.config.inject_delay_ms)
        except Unavailable as error:
            self.inject_error = str(error)

    def save_autocorrect(self, value: bool) -> None:
        """Persist the on/off switch without touching the rest of the config."""
        path = cfg.config_path()
        line = "autocorrect = %s" % ("true" if value else "false")
        try:
            with open(path, encoding="utf-8") as handle:
                lines = handle.readlines()
        except OSError:
            return
        replaced = False
        for index, existing in enumerate(lines):
            if existing.strip().startswith("autocorrect"):
                lines[index] = line + "\n"
                replaced = True
                break
        if not replaced:
            lines.append(line + "\n")
        try:
            with open(path, "w", encoding="utf-8") as handle:
                handle.writelines(lines)
        except OSError:
            pass

    def reload(self) -> None:
        with self.lock:
            self.config = cfg.Config.load()
            names = hypr.layout_names() or self.layout_names
            self.layout_names = names
            self.dict_names = self._dict_names(names)
            self.xkb_names = self._xkb_names()
            self._build_layouts()
            self._build_injector()

    def layout_index(self) -> int:
        return self.watcher.layout_index()

    def group(self, index: int | None = None):
        if self.layouts is None:
            return None
        if index is None:
            index = self.layout_index()
        return self.layouts.group_for(index)

    def status(self) -> dict:
        with self.lock:
            return {
                "events": self.watcher.connected,
                "autocorrect": self.config.autocorrect,
                "layouts": self.layout_names,
                "groups": [group.name for group in self.layouts.groups] if self.layouts else [],
                "dicts": self.speller.status(),
                "active_layout": self.layout_index(),
                "injector": self.inject_error or "wtype",
                "min_word_length": self.config.min_word_length,
                "prefixes": self.prefixes.info(),
                "double_shift_ms": self.config.double_shift_ms,
                "live": self.config.live,
                "live_min_length": self.config.live_min_length,
            }


class Engine:
    def __init__(self, state: State):
        self.state = state
        #: [(char, evdev keycode, shift)]
        self.buffer: list[tuple[str, int, bool]] = []
        self.pressed: set[int] = set()
        self.suppress_until = 0.0
        self.last_correction: dict | None = None
        #: "ghbdtn"+entries as of the last commit, for the double-Shift command
        self.last_word: dict | None = None
        self._last_shift = 0.0
        self._shift_buffer_size = -1
        #: live mode: text we already fixed inside the current buffer, and when
        self._live_corrected: str | None = None
        self._live_last_check = 0.0
        self._window_cache: tuple[float, str | None] = (0.0, None)

    # -- modifier state --------------------------------------------------
    def update_modifiers(self, code: int, value: int) -> None:
        if value == 1:
            self.pressed.add(code)
        elif value == 0:
            self.pressed.discard(code)

    @property
    def shift(self) -> bool:
        return bool(self.pressed & SHIFT_KEYS)

    @property
    def altgr(self) -> bool:
        return bool(self.pressed & ALTGR_KEYS)

    @property
    def caps(self) -> bool:
        return bool(self.pressed & CAPS_KEYS)

    @property
    def shortcut(self) -> bool:
        """True while a shortcut modifier makes the keys non-textual."""
        return bool(self.pressed & (CTRL_KEYS | META_KEYS))

    # -- events ----------------------------------------------------------
    def reset_buffer(self) -> None:
        self.buffer.clear()
        self._live_corrected = None
        self._live_last_check = 0.0

    def handle_key(self, code: int, value: int) -> None:
        if value == 0:
            self.update_modifiers(code, value)
            if code in SHIFT_KEYS:
                self._on_shift_release()
            return
        if value == 2:
            # autorepeat: keep the buffer in sync, never act
            if (
                code in TERMINATOR_KEYS
                or code in NAVIGATION_KEYS
                or code in PLAIN_BOUNDARY_KEYS
            ):
                self.buffer.clear()
            elif code not in IGNORED_MODIFIER_KEYS:
                self._append(code)
            return

        self.update_modifiers(code, 1)
        if code in IGNORED_MODIFIER_KEYS:
            return
        if code in NAVIGATION_KEYS:
            self.reset_buffer()
            return
        if code in PLAIN_BOUNDARY_KEYS:
            self.reset_buffer()
            return
        if code in TERMINATOR_KEYS:
            self.commit(separator=code, separator_text=SEPARATOR_CHARS.get(code))
            return
        if code in PUNCTUATION_KEYS:
            # "," "." etc. are part of a word in the other layout ("nt,z" is
            # "тебя"), so they join the buffer instead of ending it.
            result = self._char_for(code)
            if result is not None and result[0]:
                self.buffer.append((result[0], code, result[1]))
            else:
                self.reset_buffer()
            return
        self._append(code)

    # -- live correction -------------------------------------------------
    def _try_live_correction(self) -> None:
        """Switch and re-type the word before the separator, while typing.

        Decisions only become reliable after three or four letters, and they are
        solid enough by then to act on: a wrong switch is undone by typing on,
        because the buffer keeps the corrected text.  The separator is never
        involved here, so the correction is a plain backspace-and-retype.
        """
        config = self.state.config
        if not config.live:
            return
        if not config.autocorrect or self.shortcut:
            return
        if self.blacklisted(config.autocorrect_blacklist):
            return
        letters = _letters_only("".join(entry[0] for entry in self.buffer))
        # The floor is configurable so the "switch on two letters" variant can be
        # measured honestly; 3+ is what ships (two letters are too ambiguous).
        minimum = max(2, int(config.live_min_length))
        if len(letters) < minimum or len(letters) > 32:
            return
        if time.time() < self.suppress_until:
            return
        now = time.monotonic()
        if now - self._live_last_check < config.live_interval_ms / 1000.0:
            return
        self._live_last_check = now
        if letters == self._live_corrected:
            return  # already switched at this length, do not loop

        active = self.state.layout_index()
        if self.state.layouts is None:
            return
        group = self.state.layouts.group_for(active)
        other = self.state.layouts.other_group(active)
        if group is None or other is None:
            return
        if active >= len(self.state.dict_names) or other.index >= len(self.state.dict_names):
            return
        word = "".join(entry[0] for entry in self.buffer)
        # Only act on strong evidence: a live switch must not guess.
        if self._judge(word, group, other) != "exact":
            return
        if self.state.injector is None or not self.state.injector.available():
            return

        entries = list(self.buffer)
        typed = self.state.layouts.translate(word.lower(), group, other)
        if not typed:
            return
        # keep the case the user typed (CapsLock/Shift is not in the letters)
        case_map = _CaseMapper(word, typed)
        typed = case_map.apply()
        try:
            with self.state.lock:
                self.suppress_until = time.time() + 1.0
                hypr.switch_layout(other.index)
                self.state.watcher.set_layout_index(other.index)
                self.state.injector.replace(typed, len(entries))
                news = []
                for char, entry in zip(typed, entries):
                    news.append((char, entry[1], entry[2]))
                self.buffer = news
                self._live_corrected = _letters_only(typed)
        except (hypr.HyprlandError, Unavailable):
            self.state.watcher.set_layout_index(active)
        finally:
            self.suppress_until = time.time() + 0.25
        self._log("live", word, typed, other.name)

    # -- double Shift ----------------------------------------------------
    def _on_shift_release(self) -> None:
        """Two clean Shift taps in a row: force the switch.

        "Clean" is what makes this safe: no character may be typed between the
        two taps and nothing else may be held.  Otherwise ordinary typing (a
        capital letter, then Shift for a bracket) would keep triggering it and
        re-typing an unrelated word, which is exactly what went wrong before.
        """
        if self.shortcut or self.pressed & (CTRL_KEYS | META_KEYS | ALTGR_KEYS):
            # Shift+Ctrl is the user's own layout switch, not ours
            self._last_shift = 0.0
            self._shift_buffer_size = -1
            return
        if self.pressed & {ecodes.KEY_LEFTALT}:
            self._last_shift = 0.0
            self._shift_buffer_size = -1
            return
        now = time.monotonic()
        interval = self.state.config.double_shift_ms / 1000.0
        size = len(self.buffer)
        previous_size = getattr(self, "_shift_buffer_size", -1)
        if (
            interval
            and now - self._last_shift <= interval
            and size == previous_size
        ):
            self._last_shift = 0.0
            self._shift_buffer_size = -1
            threading.Thread(target=self.force_last_word, daemon=True).start()
        else:
            self._last_shift = now
            self._shift_buffer_size = size

    def _selection_makes_sense(self, text: str, group, other) -> bool:
        """True when at least one word of the selection reads better as `other`."""
        state = self.state
        active_d, other_d = state.dict_names[group.index], state.dict_names[other.index]
        words = [w for w in _SPLIT_RE.findall(text) if w]
        if not words:
            return False
        better = 0
        worse = 0
        for word in words:
            candidate = _translate_any(word.lower(), state.layouts, group, other)
            if not candidate:
                continue
            here = frequency_score(state.frequency, active_d, word.lower(), state.speller)
            there = frequency_score(state.frequency, other_d, candidate, state.speller)
            if there > here:
                better += 1
            elif here > there:
                worse += 1
        return better > 0 and better >= worse

    def _correct_selection(self) -> dict | None:
        """Double Shift on a selection: convert the selected text in place."""
        selection = self.state.selection
        if selection is None:
            return None
        text = selection.read()
        if not text or len(text) > 400 or "\n" in text:
            return None

        active = self.state.layout_index()
        if self.state.layouts is None:
            return None
        group = self.state.layouts.group_for(active)
        other = self.state.layouts.other_group(active)
        if group is None or other is None:
            return None

        # Only convert when the other layout is the plausible reading: a correct
        # English word must not become "руддщ".  The same evidence score the
        # autocorrection uses decides, per word.
        if not self._selection_makes_sense(text, group, other):
            return None

        converted = _translate_any(text, self.state.layouts, group, other, phrase=True)
        if not converted or converted == text:
            return None
        if not selection.write(converted):
            return None
        # the layout follows the conversion: the user is now typing that language
        self.state.watcher.set_layout_index(other.index)
        try:
            hypr.switch_layout(other.index)
        except (hypr.HyprlandError, OSError):
            pass
        self._log("selection", text, converted, other.name)
        return {"ok": True, "from": text, "to": converted, "layout": other.name, "selected": True}

    def force_last_word(self) -> dict:
        """Force the switch on the word being typed (double Shift).

        Only the current word, or one committed moments ago, is ever touched: a
        stale word from an earlier correction must never be pasted.
        """
        # A selection is the most explicit request there is: convert it in place.
        selected = self._correct_selection()
        if selected:
            if self.state.config.notifications:
                notify("keyb-switch", str(selected.get("to", "")))
            return selected
        with self.state.lock:
            entries = list(self.buffer)
            self.buffer.clear()
            separator = None
            separator_text = None
            if len(entries) < 2:
                recent = self.last_word
                if not recent:
                    return {"ok": False, "error": "нечего исправлять"}
                age = time.monotonic() - recent["at"]
                if age > self.state.config.force_window_s:
                    return {"ok": False, "error": "слово уже не то"}
                entries = recent["entries"]
                separator = recent["separator"]
                separator_text = recent["separator_text"]
            word = "".join(entry[0] for entry in entries)
            if len(_letters_only(word)) < 2:
                return {"ok": False, "error": "нечего исправлять"}
            self.suppress_until = 0.0
            result = self.correct_last(
                word=word, entries=entries, separator=separator,
                separator_text=separator_text,
            )
        if result.get("ok") and self.state.config.notifications:
            notify("keyb-switch", "%s → %s" % (word, result.get("to", "")))
        return result

    def _char_for(self, code: int) -> tuple[str, bool] | None:
        """Return the produced character and whether Shift was in effect."""
        group = self.state.group()
        if group is None:
            return None
        shift = self.shift
        char = group.char_for(xkb_keycode(code), shift, self.altgr)
        if char is None:
            return None
        # CapsLock only affects letters: it flips the case while Shift keeps it
        # simple for every other key (digits stay digits).
        if self.caps and char.isalpha():
            char = char.swapcase()
            return char, not shift
        return char, shift

    def _append(self, code: int) -> None:
        if self.shortcut:
            # Ctrl+C, Ctrl+V, Super+... are commands: the word being typed is
            # no longer contiguous, so stop tracking it
            self.reset_buffer()
            return
        result = self._char_for(code)
        if result is None:
            # function keys, dead keys, anything unmapped: a word cannot span it
            self.reset_buffer()
            return
        char, shift = result
        if WORD_CHAR.match(char):
            self.buffer.append((char, code, shift))
            self._try_live_correction()
        else:
            self.reset_buffer()

    # -- correction ------------------------------------------------------
    def commit(
        self, separator: int | None = None, separator_text: str | None = None
    ) -> dict | None:
        word = "".join(entry[0] for entry in self.buffer)
        entries = list(self.buffer)
        self.buffer.clear()
        if len(entries) >= 2:
            self.last_word = {
                "word": word,
                "entries": entries,
                "separator": separator,
                "separator_text": separator_text,
                "at": time.monotonic(),
            }
        if len(entries) < 2:
            return None
        if time.time() < self.suppress_until:
            return None
        return self._handle_word(word, entries, separator, separator_text)

    def _handle_word(
        self,
        word: str,
        entries: list[tuple[str, int, bool]],
        separator: int | None = None,
        separator_text: str | None = None,
    ) -> dict | None:
        state, config = self.state, self.state.config
        minimum = max(2, int(config.min_word_length))
        if len(_letters_only(word)) < minimum:
            return None
        if not any(WORD_CHAR.match(char) for char in word):
            return None
        if not config.autocorrect:
            return None
        if self.blacklisted(config.autocorrect_blacklist):
            return None
        # in password fields there is no way to know, but terminals and
        # code editors are handled by the window-class blacklist above

        active = state.layout_index()
        if state.layouts is None:
            return None
        group = state.layouts.group_for(active)
        other = state.layouts.other_group(active)
        if group is None or other is None:
            return None
        if active >= len(state.dict_names) or other.index >= len(state.dict_names):
            return None

        # Punctuation plays two different roles, so decide in order of evidence:
        #   1. exact dictionary match of the whole buffer (punctuation inside a
        #      word is a letter of the other layout: "nt,z" is "тебя");
        #   2. exact match after stripping trailing punctuation ("ghbdtn," is
        #      the word "ghbdtn" plus a real comma);
        #   3. only then the weaker prefix statistics, on the stripped form.
        whole = self._judge(word, group, other)
        trimmed_word, trimmed_entries, tail = _split_trailing_punctuation(word, entries)
        trimmed = None
        if tail and len(trimmed_entries) >= 2:
            trimmed = self._judge(trimmed_word, group, other)

        if whole == "exact" or (not tail and whole == "prefix"):
            return self.correct_last(word=word, entries=entries,
                                     separator=separator, separator_text=separator_text)
        if tail and trimmed:
            return self.correct_last(
                word=trimmed_word,
                entries=trimmed_entries,
                separator=separator,
                separator_text=tail + (separator_text or ""),
            )
        return None

    def _judge(self, word: str, group, other) -> str | None:
        """Which layout the buffer really is, or None for "leave it alone".

        Both candidates are scored, and the winner needs a clear margin:

            score 3  common word (frequency top 4 000)
            score 2  known word (top 20 000)
            score 1  valid dictionary form only
            score 0  unknown

        Comparing scores (instead of asking "is the other one a word?") is what
        makes short words and homographs work:

            пщ / go        0 vs 3   -> go
            еру / the      1 vs 3   -> the   (еру is a legal but rare form)
            here / руку    3 vs 3   -> tie, leave it
            ghbdtn/привет  0 vs 3   -> привет
        """
        state = self.state
        active_d, other_d = state.dict_names[group.index], state.dict_names[other.index]
        base = word.lower()

        candidate = state.layouts.translate(base, group, other)
        if not candidate:
            return None
        # punctuation typed inside the word belongs to the letters of the other
        # layout ("nt,z" -> "тебя"), so only letters take part in the lookup
        word_letters = _letters_only(word)
        candidate_letters = _letters_only(candidate)
        if not word_letters or not candidate_letters:
            return None

        here = frequency_score(state.frequency, active_d, word_letters, state.speller)
        there = frequency_score(state.frequency, other_d, candidate_letters, state.speller)

        # The other side may be misspelled too ("expirience" -> experience).  A
        # candidate one keystroke from a common word is not promoted to a fair
        # score -- that would let "abueh" outvote a real Russian word -- it is
        # only remembered as a plausible reading for the weak-evidence branch.
        candidate_plausible = bool(
            state.frequency.near_common(other_d, candidate_letters)
        )

        # Guard against typos, but only when the typo is the better explanation.
        # "учше" is one letter from "лучше" (rank ~500) and was being turned into
        # "exit" (rank 3146): the typo wins, leave it.  "ерфе" is also one letter
        # from a Russian word, but the candidate is "that" (rank 30), so the
        # layout mistake still wins.
        if there > here:
            typo_rank = state.frequency.nearest_common_rank(active_d, word_letters)
            if typo_rank is not None:
                candidate_rank = state.frequency.best_rank(other_d, candidate_letters)
                if candidate_rank is None or typo_rank < candidate_rank:
                    return None

        # Two-letter words are the hardest case: "фр", "ин", "gj" appear as
        # initials and abbreviations in normal text, yet "пщ"->"go" is a real
        # fix.  Ask for a clear margin and never fall back to prefix statistics.
        if len(word_letters) < 3:
            if there >= SCORE_KNOWN and there - here >= 2:
                return "exact"
            return None

        if there > here:
            return "exact" if there >= 2 else "prefix"
        if there < here:
            return None

        # Equal and weak: the prefix statistics decide, but only between two
        # plausible readings, and never for one- or two-letter words, where
        # every letter is a word in both languages ("фр", "gj", "vc").
        if there <= 1 and here <= 1 and len(word_letters) >= 3:
            candidate_looks = there == SCORE_VALID or candidate_plausible
            if candidate_looks and state.prefixes.prefers_other(
                word_letters, group.index, other.index
            ):
                return "prefix"
        return None

    def _should_correct(self, word: str, group, other) -> bool:
        """Boolean form of :meth:`_judge`, kept for the tests and the CLI."""
        return self._judge(word, group, other) is not None

    def blacklisted(self, patterns: list[str]) -> bool:
        if not patterns:
            return False
        klass = self._window_class()
        if not klass:
            return False
        for pattern in patterns:
            try:
                if re.search(pattern, klass):
                    return True
            except re.error:
                continue
        return False

    def _window_class(self) -> str | None:
        return self.state.watcher.window_class()

    # -- injection -------------------------------------------------------
    def correct_last(
        self,
        word: str | None = None,
        entries: list[tuple[str, int, bool]] | None = None,
        target_index: int | None = None,
        separator: int | None = None,
        separator_text: str | None = None,
    ) -> dict:
        state = self.state
        if word is None or entries is None:
            saved = self.last_correction
            if not saved:
                return {"ok": False, "error": "ещё нечего исправлять"}
            word = saved["word"]
            entries = saved["entries"]
            target_index = saved.get("from_index")
        if state.layouts is None:
            return {"ok": False, "error": "раскладки не загружены"}
        if state.injector is None or not state.injector.available():
            return {"ok": False, "error": state.inject_error or "wtype недоступен"}

        current = state.layout_index()
        if target_index is None:
            target_index = (current + 1) % len(state.layouts.groups)
        target = state.layouts.group_for(target_index)
        if target is None:
            return {"ok": False, "error": "раскладка не найдена"}
        if target.index == current:
            return {"ok": False, "error": "уже в этой раскладке"}

        typed = "".join(
            target.char_for(xkb_keycode(code), shift, False) or ""
            for _char, code, shift in entries
        )
        if len(typed) != len(entries):
            return {"ok": False, "error": "символы не раскладываются"}

        # The application already received the separator (space/enter/,/...) that
        # triggered the correction, so it has to be erased and typed again.
        suffix = separator_text if separator_text is not None else SEPARATOR_CHARS.get(separator or 0, "")
        delete = len(entries) + (1 if suffix else 0)

        try:
            with state.lock:
                self.suppress_until = time.time() + 2.0
                hypr.switch_layout(target_index)
                state.watcher.set_layout_index(target_index)
                state.injector.replace(typed + suffix, delete)
        except (hypr.HyprlandError, Unavailable) as error:
            state.watcher.set_layout_index(current)
            return {"ok": False, "error": str(error)}
        finally:
            self.suppress_until = time.time() + 0.4

        self.last_correction = {
            "word": word,
            "entries": entries,
            "from_index": target_index,
            "separator": separator,
            "separator_text": suffix,
            "at": time.time(),
        }
        self._log("correct", word, typed, target.name)
        if state.config.notify_autocorrect:
            notify("keyb-switch", "%s → %s" % (word, typed))
        return {"ok": True, "from": word, "to": typed, "layout": target.name}

    def _log(self, event: str, source: str, result: str, layout: str = "") -> None:
        try:
            path = log_path()
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "a", encoding="utf-8") as handle:
                handle.write(
                    "%s\t%s\t%s\t%s\t%s\n"
                    % (time.strftime("%Y-%m-%d %H:%M:%S"), event, source, result, layout)
                )
        except OSError:
            pass

    # -- commands --------------------------------------------------------
    def manual_correct(self) -> dict:
        """Hotkey entry point: convert the current or the previous word."""
        entries = list(self.buffer)
        self.buffer.clear()
        word = "".join(entry[0] for entry in entries)
        if len(entries) >= 2 and word.isalpha():
            result = self.correct_last(word=word, entries=entries, separator=None)
            if result.get("ok"):
                return result
        saved = self.last_correction
        if not saved:
            return {"ok": False, "error": "нечего исправлять"}
        return self.correct_last()

    def set_autocorrect(self, value: bool) -> None:
        self.state.config.autocorrect = bool(value)
        # remember the choice for the next start
        self.state.save_autocorrect(bool(value))
        if self.state.config.notifications:
            notify(
                "keyb-switch",
                "автопереключение включено" if value else "автопереключение выключено",
            )

    def toggle_autocorrect(self) -> bool:
        self.set_autocorrect(not self.state.config.autocorrect)
        return self.state.config.autocorrect

    def simulate(self, text: str, layout_index: int | None = None) -> dict:
        """Feed `text` as if it was typed on a keyboard in `layout_index`.

        Used for debugging and by the test-suite; behaves exactly like typing.
        """
        state = self.state
        if state.layouts is None:
            return {"ok": False, "error": "раскладки не загружены"}
        if layout_index is None:
            layout_index = state.layout_index()
        # make the whole simulation use the same group, whatever is active now
        state.watcher.set_layout_index(layout_index)
        group = state.layouts.group_for(layout_index)
        if group is None:
            return {"ok": False, "error": "раскладка не найдена"}
        results = []
        for char in text:
            if char == " ":
                result = self.commit()
                if result:
                    results.append(result)
                continue
            key = group.key_for(char)
            if key is None:
                return {"ok": False, "error": "нет клавиши для %r" % char}
            keycode, shift = key
            self.handle_key(keycode - 8, 1)
            self.handle_key(keycode - 8, 0)
        return {"ok": True, "corrected": results}

    def typo(self, word: str) -> dict:
        """Translate a string into the other layout, without typing it."""
        state = self.state
        if state.layouts is None:
            return {"ok": False, "error": "раскладки не загружены"}
        active = state.layout_index()
        group = state.layouts.group_for(active)
        other = state.layouts.other_group(active)
        if group is None or other is None:
            return {"ok": False, "error": "нужно две раскладки"}
        translated = state.layouts.translate(word, group, other)
        if translated is None:
            translated = state.layouts.translate(word, other, group)
        if translated is None:
            return {"ok": False, "error": "не раскладывается"}
        return {"ok": True, "result": translated}
