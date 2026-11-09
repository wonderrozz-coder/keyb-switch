#!/usr/bin/env python3
"""Simulate real typing, key by key, and measure how often a live switch helps.

The difference from `benchmark.py`: that one asks "would the engine decide
correctly for this finished word?". This one reproduces what actually happens
when the decision is taken *while typing*: the switch fires at some prefix
length, the remaining physical keys then produce the other layout, and whatever
came before the switch is already on screen.

That timing effect matters because a wrong switch at 2 letters does not just
leave one word wrong -- every remaining key lands in the wrong alphabet.

    ./scripts/simulate_typing.py            # full comparison
    ./scripts/simulate_typing.py --live 2   # only the 2-letter live variant
"""

from __future__ import annotations

import argparse
import os
import random
import re
import sys
from dataclasses import dataclass, field

ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
sys.path.insert(0, ROOT)

from kbswitch.engine import Engine, State  # noqa: E402
from kbswitch.frequency import score as frequency_score  # noqa: E402


@dataclass
class Result:
    name: str
    words: int = 0
    perfect: int = 0
    damaged: int = 0            # ended as garbled text
    wrong_but_valid: int = 0    # ended as a different real word
    samples: list = field(default_factory=list)

    def report(self) -> None:
        total = max(1, self.words)
        print(f"  {self.name}")
        print(f"    итог верный:        {self.perfect / total * 100:5.1f}%  ({self.perfect}/{self.words})")
        print(f"    испорчено:          {self.damaged / total * 100:5.1f}%  ({self.damaged})")
        print(f"    стало другим словом:{self.wrong_but_valid / total * 100:5.1f}%  ({self.wrong_but_valid})")
        if self.samples:
            print("    примеры: " + ", ".join(self.samples[:6]))


def load_corpus(limit: int) -> tuple[list[str], list[str]]:
    """Word lists from the benchmark cache, or the frequency lists as a fallback."""
    cache = os.path.join(ROOT, "dict", "corpus_cache.json")
    ru: list[str] = []
    en: list[str] = []
    if os.path.exists(cache):
        import json

        try:
            with open(cache, encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, ValueError):
            data = {}
        ru = [w.lower() for w in re.findall(r"[а-яё]{2,}", data.get("ru", ""), re.I)]
        en = [w.lower() for w in re.findall(r"[a-z]{2,}", data.get("en", ""), re.I)]
    # Real text is better (it contains inflected forms), the frequency list is
    # the fallback when the benchmark cache has not been generated yet.
    if len(ru) < 100:
        ru = _from_frequency("ru", limit * 4)
    if len(en) < 100:
        en = _from_frequency("en", limit * 4)
    if len(ru) < 100:
        print("мало русских слов: нужен dict/corpus_cache.json "
              "(запустите scripts/benchmark.py один раз)", file=sys.stderr)

    def unique(words):
        seen, out = set(), []
        for word in words:
            if word not in seen:
                seen.add(word)
                out.append(word)
        return out

    random.seed(20261106)  # stable samples between runs
    return unique(ru)[:limit], unique(en)[:limit]


def _from_frequency(lang: str, limit: int) -> list[str]:
    path = os.path.join(ROOT, "dict", "freq_%s.txt" % lang)
    pattern = r"[а-яё]+" if lang == "ru" else r"[a-z]+"
    words = []
    try:
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                parts = line.split()
                if parts and re.fullmatch(pattern, parts[0]):
                    words.append(parts[0])
                if len(words) >= limit:
                    break
    except OSError:
        pass
    return words


class Simulator:
    def __init__(self, state: State, engine: Engine):
        self.state = state
        self.engine = engine
        self.en = state.layouts.group_for(0)
        self.ru = state.layouts.group_for(1)

    def type_word(self, word: str, typed_in, intended, live_at: int | None) -> str:
        """Simulate typing `word` with `typed_in` active; return what ends on screen.

        `live_at` — number of letters after which the live switch may fire
        (None disables live, so only the end-of-word check runs).
        """
        state, engine = self.state, self.engine
        state.watcher.set_layout_index(typed_in.index)
        engine.reset_buffer()
        engine.suppress_until = 0.0
        engine._live_corrected = None
        engine._live_last_check = 0.0
        engine.last_word = None

        output = []
        current = typed_in
        switched_at = None
        # sanity: the very first key must be able to produce something
        first_key = intended.key_for(word[0])
        if first_key is None or not typed_in.char_for(first_key[0], first_key[1], False):
            return ""

        for index, char in enumerate(word):
            # The physical key is the one that produces `char` in its own
            # layout; pressing it while `typed_in` is active is what makes the
            # mistake.  (A Russian letter simply does not exist in the English
            # table, so the lookup must use the intended layout.)
            key = intended.key_for(char)
            if key is None:
                return ""  # not typeable, skip
            keycode, shift = key
            produced = current.char_for(keycode, shift, False)
            if not produced:
                return ""
            output.append(produced)

            # feed the engine as a real keystroke
            if shift:
                engine.handle_key(42, 1)  # KEY_LEFTSHIFT
            engine.handle_key(keycode - 8, 1)
            engine.handle_key(keycode - 8, 0)
            if shift:
                engine.handle_key(42, 0)

            if live_at is not None and switched_at is None and len(output) >= live_at:
                if engine._live_corrected:
                    # the engine re-typed the buffer and moved the layout
                    switched_at = len(output)
                    current = self.state.layouts.group_for(self.state.layout_index())
                    output = list(engine.buffer and "".join(e[0] for e in engine.buffer) or "")

        state.watcher.set_layout_index(current.index)
        # end of word: the normal separator check
        result = engine.commit(separator=57, separator_text=" ")
        if result and result.get("ok"):
            return str(result.get("to") or "")
        return "".join(output)


def classify(result: str, intended_word: str, intended_dict: str, other_dict: str,
             state: State) -> str:
    if result == intended_word:
        return "perfect"
    if state.speller.check_any_case(intended_dict, result):
        return "wrong_but_valid"
    return "damaged"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", type=int, default=None,
                        help="test only this live threshold")
    parser.add_argument("--words", type=int, default=800)
    args = parser.parse_args()

    state = State()
    state.config.notifications = False
    state.config.autocorrect = True
    state.config.live = True
    engine = Engine(state)
    sim = Simulator(state, engine)
    names = state.dict_names

    ru_words, en_words = load_corpus(args.words)
    print(f"симуляция набора: {len(ru_words)} русских, {len(en_words)} английских слов\n")

    if args.live is not None:
        variants = [(f"live на {args.live} символах", args.live)]
    else:
        variants = [("без live (только пробел)", None)]
        variants += [(f"live на {n} символах", n) for n in (2, 3, 4, 5)]

    for label, live_at in variants:
        engine.state.config.live = live_at is not None
        if live_at is not None:
            engine.state.config.live_min_length = live_at
            # let the engine act as soon as the threshold is reached
            engine.state.config.live_interval_ms = 0

        results = []
        for words, typed_in, intended, i_dict, o_dict, lang in (
            (ru_words, sim.en, sim.ru, names[1], names[0], "RU в EN"),
            (en_words, sim.ru, sim.en, names[0], names[1], "EN в RU"),
        ):
            res = Result(f"{lang} ({label})")
            for word in words:
                if not word.isalpha():
                    continue
                if not state.frequency.rank(i_dict, word):
                    continue  # not a common word: a mistake here is not meaningful
                res.words += 1
                out = sim.type_word(word, typed_in, intended, live_at)
                if not out:
                    res.words -= 1
                    continue
                kind = classify(out, word, i_dict, o_dict, state)
                if kind == "perfect":
                    res.perfect += 1
                elif kind == "wrong_but_valid":
                    res.wrong_but_valid += 1
                    if len(res.samples) < 6:
                        res.samples.append(f"{word}->{out}")
                else:
                    res.damaged += 1
                    if len(res.samples) < 6:
                        res.samples.append(f"{word}->{out}")
            results.append(res)
        for res in results:
            res.report()
        print()

    state.speller.close()
    state.layouts.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
