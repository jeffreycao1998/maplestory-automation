import ctypes
import dataclasses
import os
import subprocess
import sys
from pathlib import Path

import pytest

from maplebot.controls import (
    FakeBackend,
    InputBackend,
    SendInputBackend,
    available_keys,
    default_backend,
    normalize_key,
)
from maplebot.controls import backends as be
from maplebot.settings import Keys


def test_module_imports_on_this_os():
    # Fresh interpreter so the check is not satisfied by an earlier import.
    code = ("import maplebot.controls as c; "
            "assert c.SendInputBackend and c.default_backend; print('ok')")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                         env={**os.environ, "PYTHONPATH": str(Path(be.__file__).parents[2])})
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "ok"


@pytest.mark.skipif(sys.platform == "win32", reason="off-Windows behaviour")
def test_sendinput_backend_off_windows_raises_clear_error():
    with pytest.raises(RuntimeError, match="only works on Windows"):
        SendInputBackend()
    with pytest.raises(RuntimeError, match="only works on Windows"):
        default_backend()


def test_fake_backend_is_an_input_backend():
    assert isinstance(FakeBackend(), InputBackend)


def test_fake_backend_records_and_fails():
    t = iter([1.0, 2.0])
    b = FakeBackend(clock=lambda: next(t), fail_on={"x"})
    b.key_down("a")
    b.key_up("a")
    assert b.events == [(1.0, "a", True), (2.0, "a", False)]
    with pytest.raises(be.BackendError):
        b.key_down("x")
    assert b.held == set()


# ---- key names -------------------------------------------------------------------

@pytest.mark.parametrize("alias,canon", [
    ("control", "ctrl"), ("del", "delete"), ("pgup", "pageup"), ("escape", "esc"),
    ("return", "enter"), ("CTRL", "ctrl"), (" Alt ", "alt"), ("Page Up", "pageup"),
    ("page_down", "pagedown"), ("F12", "f12"), (",", "comma"), ("`", "backtick"),
    ("A", "a"), ("5", "5"), ("space", "space"),
])
def test_normalize_key_aliases(alias, canon):
    assert normalize_key(alias) == canon


@pytest.mark.parametrize("bad", ["", "   ", "ctlr", "f13", "numpad5", "win"])
def test_normalize_key_unknown_raises_friendly(bad):
    with pytest.raises(ValueError) as e:
        normalize_key(bad)
    msg = str(e.value)
    assert "key" in msg.lower()
    assert "Traceback" not in msg


def test_normalize_key_non_string():
    with pytest.raises(ValueError):
        normalize_key(None)  # type: ignore[arg-type]


def test_available_keys_contains_required_set():
    keys = available_keys()
    assert len(keys) == len(set(keys))
    required = (list("abcdefghijklmnopqrstuvwxyz") + list("0123456789")
                + [f"f{i}" for i in range(1, 13)]
                + ["ctrl", "shift", "alt", "space", "enter", "tab", "esc",
                   "left", "right", "up", "down", "insert", "delete", "home", "end",
                   "pageup", "pagedown", "minus", "equals", "comma", "period", "slash",
                   "semicolon", "quote", "lbracket", "rbracket", "backslash", "backtick"])
    assert set(required) <= set(keys)
    assert all(k == normalize_key(k) for k in keys)


def test_available_keys_contains_settings_defaults():
    keys = set(available_keys())
    for f in dataclasses.fields(Keys):
        value = getattr(Keys(), f.name)
        if value:
            assert value in keys, f.name


def test_available_keys_returns_copy():
    available_keys().clear()
    assert available_keys()


# ---- scan codes --------------------------------------------------------------------

def test_scan_table_covers_every_available_key():
    for k in available_keys():
        code, ext = be.scan_code(k)
        assert 0 < code < 0x80, k


def test_scan_codes_unique_per_extended_flag():
    seen = {}
    for k in available_keys():
        sc = be.scan_code(k)
        assert sc not in seen, f"{k} and {seen.get(sc)} share {sc}"
        seen[sc] = k


def test_extended_keys():
    expected_ext = {"left", "right", "up", "down", "insert", "delete", "home", "end",
                    "pageup", "pagedown", "rctrl", "ralt"}
    assert be.EXTENDED_KEYS == expected_ext
    for k in ("ctrl", "alt", "shift", "space", "a", "1", "f1", "enter"):
        assert not be.scan_code(k)[1], k


@pytest.mark.parametrize("key,code", [
    ("left", 0x4B), ("right", 0x4D), ("up", 0x48), ("down", 0x50),
    ("ctrl", 0x1D), ("alt", 0x38), ("shift", 0x2A), ("space", 0x39),
    ("a", 0x1E), ("z", 0x2C), ("1", 0x02), ("0", 0x0B), ("f1", 0x3B), ("f10", 0x44),
    ("f11", 0x57), ("f12", 0x58), ("esc", 0x01), ("enter", 0x1C), ("tab", 0x0F),
    ("pageup", 0x49), ("pagedown", 0x51), ("insert", 0x52), ("delete", 0x53),
    ("home", 0x47), ("end", 0x4F), ("comma", 0x33), ("period", 0x34), ("slash", 0x35),
    ("semicolon", 0x27), ("quote", 0x28), ("backtick", 0x29), ("backslash", 0x2B),
    ("minus", 0x0C), ("equals", 0x0D), ("lbracket", 0x1A), ("rbracket", 0x1B),
])
def test_known_scan_codes(key, code):
    assert be.scan_code(key)[0] == code


def test_keyboard_flags():
    assert be.keyboard_flags("left", up=False) == (0x4B, 0x0008 | 0x0001)
    assert be.keyboard_flags("left", up=True) == (0x4B, 0x0008 | 0x0001 | 0x0002)
    assert be.keyboard_flags("ctrl", up=False) == (0x1D, 0x0008)
    assert be.keyboard_flags("ctrl", up=True) == (0x1D, 0x0008 | 0x0002)


@pytest.mark.skipif(ctypes.sizeof(ctypes.c_void_p) != 8, reason="64-bit layout check")
def test_input_struct_layout_matches_64bit_windows():
    KEYBDINPUT, MOUSEINPUT, HARDWAREINPUT, INPUT = be.win32_structs()
    assert ctypes.sizeof(KEYBDINPUT) == 24
    assert ctypes.sizeof(MOUSEINPUT) == 32
    assert ctypes.sizeof(HARDWAREINPUT) == 8
    assert ctypes.sizeof(INPUT) == 40
    assert INPUT.u.offset == 8          # union aligned to 8 after the DWORD type
    assert KEYBDINPUT.wScan.offset == 2
    assert KEYBDINPUT.dwFlags.offset == 4
    assert KEYBDINPUT.dwExtraInfo.offset == 16
    i = INPUT(type=1)
    i.ki.wScan = 0x4B
    assert i.ki.wScan == 0x4B


def test_sendinput_failure_message_is_user_friendly():
    msg = be.SENDINPUT_FAILED_MSG
    assert msg == ("Can't press keys in the game. Close the bot and start it with "
                   "start.bat (it asks for administrator).")
