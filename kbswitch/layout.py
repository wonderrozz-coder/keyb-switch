"""Keyboard layout tables built from the live xkb configuration.

The compositor's keymap is used, so variants/options the user configured in
Hyprland are respected.  libxkbcommon ships with python-evdev, so no extra
system dependency is required.
"""

from __future__ import annotations

import ctypes
import ctypes.util
from dataclasses import dataclass, field

SHIFT = "shift"
ALTGR = "altgr"


def _load_xkbcommon() -> ctypes.CDLL:
    candidates = []
    try:  # python-evdev bundles the shared library, in a private directory
        from evdev import _input

        candidates.append(
            ctypes.CDLL(str(_input.__file__).replace("_input", "libxkbcommon.so.0"))
        )
    except Exception:
        pass
    for name in ("libxkbcommon.so.0", ctypes.util.find_library("xkbcommon")):
        if not name:
            continue
        try:
            candidates.append(ctypes.CDLL(name))
        except OSError:
            continue
    if not candidates:
        raise RuntimeError("libxkbcommon not found")
    return candidates[0]


_lib = _load_xkbcommon()

_c_void_p = ctypes.c_void_p
_c_uint32 = ctypes.c_uint32
_c_char_p = ctypes.c_char_p

_lib.xkb_context_new.restype = _c_void_p
_lib.xkb_context_new.argtypes = [ctypes.c_int]
_lib.xkb_keymap_new_from_names.restype = _c_void_p
_lib.xkb_keymap_new_from_names.argtypes = [_c_void_p, ctypes.c_void_p, ctypes.c_int]
_lib.xkb_keymap_min_keycode.restype = _c_uint32
_lib.xkb_keymap_min_keycode.argtypes = [_c_void_p]
_lib.xkb_keymap_max_keycode.restype = _c_uint32
_lib.xkb_keymap_max_keycode.argtypes = [_c_void_p]
_lib.xkb_keymap_num_layouts.restype = _c_uint32
_lib.xkb_keymap_num_layouts.argtypes = [_c_void_p]
_lib.xkb_keymap_layout_get_name.restype = _c_char_p
_lib.xkb_keymap_layout_get_name.argtypes = [_c_void_p, _c_uint32]
_lib.xkb_keymap_key_get_syms_by_level.restype = ctypes.c_int
_lib.xkb_keymap_key_get_syms_by_level.argtypes = [
    _c_void_p,
    _c_uint32,
    _c_uint32,
    _c_uint32,
    ctypes.POINTER(ctypes.POINTER(_c_uint32)),
]
_lib.xkb_keysym_to_utf32.restype = _c_uint32
_lib.xkb_keysym_to_utf32.argtypes = [_c_uint32]
_lib.xkb_keymap_unref.restype = None
_lib.xkb_keymap_unref.argtypes = [_c_void_p]
_lib.xkb_context_unref.restype = None
_lib.xkb_context_unref.argtypes = [_c_void_p]


@dataclass
class XkbNames:
    rules: str = ""
    model: str = ""
    layout: str = "us"
    variant: str = ""
    options: str = ""

    def as_struct(self):
        class _Names(ctypes.Structure):
            _fields_ = [
                ("rules", _c_char_p),
                ("model", _c_char_p),
                ("layout", _c_char_p),
                ("variant", _c_char_p),
                ("options", _c_char_p),
            ]

        return _Names(
            self.rules.encode() or None,
            self.model.encode() or None,
            self.layout.encode() or None,
            self.variant.encode() or None,
            self.options.encode() or None,
        )


@dataclass
class Group:
    index: int
    name: str
    #: (keycode, shift, altgr) -> character
    chars: dict[tuple[int, bool, bool], str] = field(default_factory=dict)
    #: character -> (keycode, shift)
    reverse: dict[str, tuple[int, bool]] = field(default_factory=dict)

    def char_for(self, keycode: int, shift: bool, altgr: bool) -> str | None:
        return self.chars.get((keycode, shift, altgr))

    def key_for(self, char: str) -> tuple[int, bool] | None:
        return self.reverse.get(char)


class Layouts:
    """All xkb groups of the current keymap plus the group <-> layout mapping."""

    def __init__(self, names: XkbNames):
        self._ctx = _lib.xkb_context_new(0)
        names_struct = names.as_struct()
        self._keymap = _lib.xkb_keymap_new_from_names(
            self._ctx, ctypes.byref(names_struct), 0
        )
        if not self._keymap:
            raise RuntimeError("cannot compile xkb keymap for %r" % (names,))
        self.names = names
        self.groups: list[Group] = []
        self._build()

    def close(self) -> None:
        if self._keymap:
            _lib.xkb_keymap_unref(self._keymap)
            self._keymap = None
        if self._ctx:
            _lib.xkb_context_unref(self._ctx)
            self._ctx = None

    def __del__(self) -> None:  # pragma: no cover - best effort
        self.close()

    def _char(self, keycode: int, group: int, level: int) -> str | None:
        arr = ctypes.POINTER(_c_uint32)()
        count = _lib.xkb_keymap_key_get_syms_by_level(
            self._keymap, keycode, group, level, ctypes.byref(arr)
        )
        if count < 1:
            return None
        code = _lib.xkb_keysym_to_utf32(arr[0])
        if not code:
            return None
        char = chr(code)
        if not char.isprintable():
            return None
        return char

    def _build(self) -> None:
        total = _lib.xkb_keymap_num_layouts(self._keymap)
        min_kc = _lib.xkb_keymap_min_keycode(self._keymap)
        max_kc = _lib.xkb_keymap_max_keycode(self._keymap)
        for group_index in range(total):
            name = _lib.xkb_keymap_layout_get_name(self._keymap, group_index)
            group = Group(group_index, (name or "").decode(errors="replace"))
            for keycode in range(min_kc, max_kc + 1):
                for shift in (False, True):
                    char = self._char(keycode, group_index, 1 if shift else 0)
                    if char:
                        group.chars[(keycode, shift, False)] = char
                        # Reverse lookups must be unambiguous. Keys that share a
                        # character (digits across groups, Cyrillic homoglyphs)
                        # keep the first mapping so injection stays predictable.
                        if char not in group.reverse:
                            group.reverse[char] = (keycode, shift)
                # AltGr characters are recognised but never used for reverse
                # lookups: their keycode/shift pair is ambiguous (a level-3 char
                # would be injected as the level-1 char).
                char = self._char(keycode, group_index, 2)
                if char:
                    group.chars[(keycode, False, True)] = char
            self.groups.append(group)

    def group_for(self, layout_index: int) -> Group | None:
        if not self.groups:
            return None
        index = layout_index % len(self.groups)
        return self.groups[index]

    def other_group(self, layout_index: int) -> Group | None:
        if len(self.groups) < 2:
            return None
        return self.groups[(layout_index + 1) % len(self.groups)]

    def translate(self, text: str, source: Group, target: Group) -> str | None:
        """Re-type `text` as it would be typed with `target` instead of `source`."""
        out = []
        for char in text:
            key = source.key_for(char)
            if key is None:
                return None
            keycode, shift = key
            mapped = target.char_for(keycode, shift, False)
            if not mapped:
                return None
            out.append(mapped)
        return "".join(out)
