"""Download hunspell dictionaries into the data directory."""

from __future__ import annotations

import os
import urllib.request

from . import config as cfg

BASE = "https://raw.githubusercontent.com/wooorm/dictionaries/main/dictionaries"

SOURCES = {
    "en_US": "en",
    "ru_RU": "ru",
}

LICENSES = {
    "en_US": "https://raw.githubusercontent.com/wooorm/dictionaries/main/dictionaries/en/license",
    "ru_RU": "https://raw.githubusercontent.com/wooorm/dictionaries/main/dictionaries/ru/license",
}


def _fetch(url: str, destination: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=30) as response:
            data = response.read()
    except OSError:
        return False
    if not data:
        return False
    with open(destination, "wb") as handle:
        handle.write(data)
    return True


def install(names: list[str] | None = None) -> bool:
    directory = cfg.dict_dir()
    os.makedirs(directory, exist_ok=True)
    ok = True
    for name in names or list(SOURCES):
        source = SOURCES.get(name)
        if not source:
            continue
        for extension in ("aff", "dic"):
            url = "%s/%s/index.%s" % (BASE, source, extension)
            target = os.path.join(directory, "%s.%s" % (name, extension))
            if not _fetch(url, target):
                ok = False
        license_url = LICENSES.get(name)
        if license_url:
            _fetch(license_url, os.path.join(directory, "LICENSE.%s.txt" % name))
    return ok
