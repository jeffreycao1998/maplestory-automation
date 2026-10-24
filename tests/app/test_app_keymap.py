"""Tk keysym / Windows VK -> bot key name."""
from __future__ import annotations

import pytest

from maplebot.app import keymap as K
from maplebot.controls import available_keys, normalize_key


@pytest.mark.parametrize("keysym,name", [
    ("Control_L", "ctrl"), ("Control_R", "ctrl"), ("Alt_L", "alt"), ("Alt_R", "alt"),
    ("Shift_L", "shift"), ("Shift_R", "shift"), ("Prior", "pageup"), ("Next", "pagedown"),
    ("Delete", "delete"), ("Insert", "insert"), ("Home", "home"), ("End", "end"),
    ("Return", "enter"), ("Escape", "esc"), ("space", "space"), ("Tab", "tab"),
    ("Left", "left"), ("Right", "right"), ("Up", "up"), ("Down", "down"),
    ("a", "a"), ("A", "a"), ("z", "z"), ("7", "7"), ("0", "0"),
    ("F1", "f1"), ("F12", "f12"), ("comma", "comma"), ("minus", "minus"),
    ("bracketleft", "lbracket"), ("apostrophe", "quote"), ("grave", "backtick"),
    ("exclam", "1"), ("question", "slash"),     # shifted symbols -> the physical key
])
def test_keysym_to_name(keysym, name):
    assert K.keysym_to_name(keysym) == name


@pytest.mark.parametrize("keysym", [None, "", "??", "Super_L", "Num_Lock", "F25", "é", "Fx", "KP_5"])
def test_unknown_keysyms(keysym):
    assert K.keysym_to_name(keysym) is None


def test_every_mapped_name_is_a_real_bot_key():
    keys = set(available_keys())
    names = set(K.KEYSYM_TO_NAME.values()) | set(K.SHIFTED_TO_NAME.values()) | set(K.VK_TO_NAME.values())
    names -= {"capslock"}                       # mapped for completeness, not pressable
    names -= {f"f{i}" for i in range(13, 25)}
    missing = names - keys
    assert not missing, missing
    for n in names:
        assert normalize_key(n) == n


def test_vk_codes():
    assert K.vk_to_name(0x11) == "ctrl"
    assert K.vk_to_name(0xA2) == "ctrl"
    assert K.vk_to_name(0x21) == "pageup"
    assert K.vk_to_name(0x41) == "a"
    assert K.vk_to_name(0x7B) == "f12"
    assert K.vk_to_name(0x99) is None
    assert K.vk_to_name(None) is None
    assert K.vk_to_name("x") is None


def test_name_from_event_prefers_keysym_and_filters_available():
    keys = available_keys()
    assert K.name_from_event("Control_L", 17, windows=True, available=keys) == "ctrl"
    # Unknown keysym but a known Windows VK code (e.g. odd keyboard layouts)
    assert K.name_from_event("??", 0x44, windows=True, available=keys) == "d"
    # VK fallback only on Windows (X11 keycodes mean something else)
    assert K.name_from_event("??", 0x44, windows=False, available=keys) is None
    # Keys the bot can't press are rejected
    assert K.name_from_event("Caps_Lock", 0x14, windows=True, available=keys) is None
    assert K.name_from_event("Super_L", None, available=keys) is None
    # No filter
    assert K.name_from_event("Caps_Lock") == "capslock"


def test_display_name():
    assert K.display_name("ctrl") == "Ctrl"
    assert K.display_name("pageup") == "Page Up"
    assert K.display_name("f11") == "F11"
    assert K.display_name("a") == "A"
    assert K.display_name("comma") == ","
    assert K.display_name("") == "(none)"
    assert K.display_name(None) == "(none)"
    for k in available_keys():
        assert K.display_name(k)
