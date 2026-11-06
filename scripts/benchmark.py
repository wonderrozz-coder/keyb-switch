#!/usr/bin/env python3
"""Measure accuracy on real text and print a table.

Downloads a small Wikipedia sample once (cached in the data dir), then feeds
every word through the decision engine in both directions.

    ./scripts/benchmark.py            # 2500 words per direction
    ./scripts/benchmark.py 5000       # more words, slower
"""

from __future__ import annotations

import json
import os
import random
import re
import sys
import time
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
sys.path.insert(0, ROOT)

from kbswitch.engine import Engine, State  # noqa: E402
from kbswitch.frequency import score as frequency_score  # noqa: E402

CACHE = os.path.join(ROOT, "dict", "corpus_cache.json")
WIKI = "https://{lang}.wikipedia.org/w/api.php"


def fetch(lang: str, pages: int = 12) -> str:
    """Random article intros, cached so repeated runs are stable and offline-safe."""
    cache = {}
    if os.path.exists(CACHE):
        try:
            with open(CACHE, encoding="utf-8") as handle:
                cache = json.load(handle)
        except (OSError, ValueError):
            cache = {}
    if lang in cache:
        return cache[lang]

    texts = []
    for _ in range(pages):
        query = urllib.parse.urlencode(
            {
                "action": "query",
                "generator": "random",
                "grnnamespace": "0",
                "grnlimit": "5",
                "prop": "extracts",
                "explaintext": "1",
                "exintro": "1",
                "format": "json",
            }
        )
        request = urllib.request.Request(
            WIKI.format(lang=lang) + "?" + query,
            headers={"User-Agent": "keyb-switch-benchmark/1.0"},
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                data = json.load(response)
        except (OSError, ValueError):
            continue
        for page in (data.get("query") or {}).get("pages", {}).values():
            text = page.get("extract") or ""
            if text:
                texts.append(text)
    corpus = "\n".join(texts)
    if corpus:
        cache[lang] = corpus
        try:
            with open(CACHE, "w", encoding="utf-8") as handle:
                json.dump(cache, handle)
        except OSError:
            pass
    return corpus


def words_of(corpus: str, pattern: str, limit: int) -> list[str]:
    seen = []
    for word in re.findall(pattern, corpus, re.IGNORECASE):
        lowered = word.lower()
        if len(lowered) >= 2 and lowered not in seen:
            seen.append(lowered)
        if len(seen) >= limit:
            break
    return seen


def main() -> int:
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 2500
    state = State()
    engine = Engine(state)
    state.config.autocorrect = False
    state.config.live = False
    en = state.layouts.group_for(0)
    ru = state.layouts.group_for(1)
    names = state.dict_names

    print(f"keyb-switch benchmark: {limit} слов на направление\n")

    def run(words, intended, typed_in, label):
        """`intended` is the language meant, `typed_in` the layout actually used.

        Only words that are a genuine mistake are counted: the intended word must
        be common, and the mistyped form must not be a word of the other language.
        Otherwise "bush" -> "игыр" would be scored as a miss even though leaving
        a real English word alone is exactly right.
        """
        fixed = 0
        total = 0
        ambiguous = 0
        samples = []
        started = time.time()
        intended_dict = state.dict_names[intended.index]
        typed_dict = state.dict_names[typed_in.index]
        for word in words:
            typed = state.layouts.translate(word, intended, typed_in)
            if not typed or not typed.isprintable():
                continue
            if not state.frequency.rank(intended_dict, word):
                continue                        # not a common word: nothing to fix
            if frequency_score(state.frequency, typed_dict, typed, state.speller) >= 2:
                ambiguous += 1                  # real word in both: cannot decide
                continue
            total += 1
            # the buffer was produced in `typed_in`, the fix would go to `intended`
            state.watcher.set_layout_index(typed_in.index)
            if engine._judge(typed, typed_in, intended):
                fixed += 1
            elif len(samples) < 8:
                samples.append((word, typed))
        elapsed = (time.time() - started) / max(1, total) * 1000
        print(f"{label}")
        print(f"  исправлено: {fixed}/{total} = {fixed / max(1, total) * 100:.1f}%"
              f"   (неоднозначных пропущено: {ambiguous})")
        print(f"  время:      {elapsed:.2f} мс/слово")
        if samples:
            print("  промахи:    " + ", ".join(f"{w}({t})" for w, t in samples))
        print()

    def false_positives(words, typed_in, other, label):
        bad = 0
        samples = []
        for word in words:
            state.watcher.set_layout_index(typed_in.index)
            if engine._judge(word, typed_in, other):
                bad += 1
                if len(samples) < 8:
                    samples.append((word, state.layouts.translate(word, typed_in, other)))
        print(f"{label}")
        print(f"  испорчено:  {bad}/{len(words)} = {bad / max(1, len(words)) * 100:.2f}%")
        if samples:
            print("  ложные:     " + ", ".join(f"{w}->{t}" for w, t in samples))
        print()

    print("Загружаю текст (кэшируется в dict/corpus_cache.json)...")
    ru_corpus = fetch("ru")
    en_corpus = fetch("en")
    if not ru_corpus or not en_corpus:
        print("Не удалось скачать текст. Проверьте сеть и запустите снова.", file=sys.stderr)
        return 1

    ru_words = words_of(ru_corpus, r"[а-яё]+", limit)
    en_words = words_of(en_corpus, r"[a-z]+", limit)

    run(ru_words, intended=ru, typed_in=en,
        label="Русское слово, набранное в английской раскладке")
    run(en_words, intended=en, typed_in=ru,
        label="Английское слово, набранное в русской раскладке")
    false_positives(en_words, typed_in=en, other=ru,
                    label="Корректный английский текст (не должен меняться)")
    false_positives(ru_words, typed_in=ru, other=en,
                    label="Корректный русский текст (не должен меняться)")

    state.speller.close()
    state.layouts.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
