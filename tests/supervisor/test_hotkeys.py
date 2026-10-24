from __future__ import annotations

import builtins
import sys
import types

import pytest

from maplebot.supervisor import Hotkeys, HotkeysUnavailable, parse_key


@pytest.mark.parametrize("name, spec", [
    ("f1", ("special", "f1")),
    ("F11", ("special", "f11")),
    ("f24", ("special", "f24")),
    ("pause", ("special", "pause")),
    ("scroll_lock", ("special", "scroll_lock")),
    ("Scroll Lock", ("special", "scroll_lock")),
    ("insert", ("special", "insert")),
    ("home", ("special", "home")),
    ("end", ("special", "end")),
    ("page_up", ("special", "page_up")),
    ("pageup", ("special", "page_up")),
    ("page_down", ("special", "page_down")),
    ("pagedown", ("special", "page_down")),
    ("esc", ("special", "esc")),
    ("a", ("char", "a")),
    ("A", ("char", "a")),
    ("1", ("char", "1")),
    (" f12 ", ("special", "f12")),
])
def test_parse_key(name, spec):
    assert parse_key(name) == spec


@pytest.mark.parametrize("bad", ["", "  ", "f25", "f0", "ctrl+a", "banana", None, 5])
def test_parse_key_rejects(bad):
    with pytest.raises(ValueError):
        parse_key(bad)


def test_same_key_rejected():
    with pytest.raises(ValueError, match="different"):
        Hotkeys("f11", "F11", lambda: None, lambda: None)


class FakeKey:                    # mimics pynput.keyboard.Key member
    def __init__(self, name):
        self.name = name


class FakeKeyCode:                # mimics pynput.keyboard.KeyCode
    def __init__(self, char):
        self.char = char


def make():
    calls = []
    hk = Hotkeys("f11", "f12", lambda: calls.append("pause"), lambda: calls.append("stop"))
    return hk, calls


def test_dispatch_and_debounce_without_listener():
    hk, calls = make()
    for _ in range(5):                         # auto-repeat while held
        hk._handle_press(FakeKey("f11"))
    hk._handle_release(FakeKey("f11"))
    hk._handle_press(FakeKey("f11"))
    hk._handle_press(FakeKey("f12"))
    hk._handle_press(FakeKey("f12"))
    hk._handle_press(FakeKey("f10"))           # unrelated key
    hk._handle_press(FakeKeyCode("x"))
    hk._handle_press(FakeKeyCode(None))        # KeyCode without char
    assert calls == ["pause", "pause", "stop"]


def test_char_hotkey_case_insensitive():
    calls = []
    hk = Hotkeys("p", "pause", lambda: calls.append("p"), lambda: calls.append("s"))
    hk._handle_press(FakeKeyCode("P"))
    hk._handle_release(FakeKeyCode("P"))
    hk._handle_press(FakeKeyCode("p"))
    hk._handle_press(FakeKey("pause"))
    assert calls == ["p", "p", "s"]


def test_callback_exception_is_swallowed():
    def boom():
        raise RuntimeError("x")
    hk = Hotkeys("f11", "f12", boom, boom)
    hk._handle_press(FakeKey("f11"))           # must not raise


def test_start_raises_unavailable_without_pynput(monkeypatch):
    real_import = builtins.__import__

    def fake_import(name, *a, **kw):
        if name == "pynput" or name.startswith("pynput."):
            raise ImportError("No module named 'pynput'")
        return real_import(name, *a, **kw)

    monkeypatch.delitem(sys.modules, "pynput", raising=False)
    monkeypatch.setattr(builtins, "__import__", fake_import)
    hk, _ = make()
    with pytest.raises(HotkeysUnavailable) as ei:
        hk.start()
    assert "Pause and Stop buttons" in str(ei.value)
    assert not hk.running
    hk.stop()
    hk.stop()                                  # idempotent


def test_start_raises_unavailable_when_listener_fails(monkeypatch):
    class BadListener:
        def __init__(self, **kw):
            raise OSError("no display")
    keyboard = types.SimpleNamespace(Listener=BadListener)
    pynput = types.ModuleType("pynput")
    pynput.keyboard = keyboard
    monkeypatch.setitem(sys.modules, "pynput", pynput)
    hk, _ = make()
    with pytest.raises(HotkeysUnavailable, match="no display"):
        hk.start()


def test_start_stop_with_fake_listener(monkeypatch):
    created = []

    class FakeListener:
        def __init__(self, on_press, on_release):
            self.on_press, self.on_release = on_press, on_release
            self.started = self.stopped = 0
            created.append(self)
        def start(self):
            self.started += 1
        def stop(self):
            self.stopped += 1

    pynput = types.ModuleType("pynput")
    pynput.keyboard = types.SimpleNamespace(Listener=FakeListener)
    monkeypatch.setitem(sys.modules, "pynput", pynput)
    hk, calls = make()
    hk.start()
    hk.start()                                 # second start is a no-op
    assert len(created) == 1 and created[0].started == 1 and hk.running
    created[0].on_press(FakeKey("f12"))
    created[0].on_release(FakeKey("f12"))
    assert calls == ["stop"]
    hk.stop()
    hk.stop()
    assert created[0].stopped == 1 and not hk.running
