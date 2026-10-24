from __future__ import annotations

import json
import threading

import numpy as np
import pytest
import yaml

from maplebot.supervisor import RunLogger
from maplebot.supervisor import runlog as runlog_mod
from maplebot.types import (Action, Decision, Flag, Frame, Mob, Perception, Verdict,
                            VerdictKind)

from supervisor_helpers import MOB, make_p, make_settings

OK = Verdict(VerdictKind.OK)
FIELDS = {"t", "state", "action", "reason", "verdict", "player_map", "player_screen",
          "n_mobs", "target", "hp", "mp", "flags", "confidence"}


def frame(t, h=20, w=30):
    return Frame(image=np.zeros((h, w, 3), np.uint8), t=t, window=(0, 0, w, h))


def lines(rl):
    return [json.loads(x) for x in rl.ticks_path.read_text(encoding="utf-8").splitlines()]


@pytest.fixture
def rl(tmp_path):
    r = RunLogger(tmp_path / "logs", make_settings())
    yield r
    r.close({})


def test_creates_run_dir(rl, tmp_path):
    assert rl.run_dir.parent == tmp_path / "logs"
    assert len(rl.run_dir.name) == len("20261023-120000")
    assert (rl.run_dir / "frames").is_dir()
    assert rl.ticks_path.exists()
    snap = yaml.safe_load((rl.run_dir / "settings.yaml").read_text())
    assert snap["safety"]["hp_critical"] == 0.15


def test_two_loggers_same_second_get_distinct_dirs(tmp_path):
    a = RunLogger(tmp_path, make_settings())
    b = RunLogger(tmp_path, make_settings())
    assert a.run_dir != b.run_dir
    a.close({}); b.close({})


def test_log_tick_writes_valid_jsonl(rl):
    d = Decision(Action.ATTACK, "ENGAGE", target=MOB, reason="mob in range")
    rl.log_tick(make_p(1.2345, flags={Flag.LOW_CONFIDENCE}, confidence=0.4), d, OK)
    rl.log_tick(make_p(1.3, player=None, mobs=(), hp=None, mp=None), None,
                Verdict(VerdictKind.PAUSE, "lost"))
    rl.flush()
    a, b = lines(rl)
    assert FIELDS <= set(a)
    assert a["t"] == 1.234 or a["t"] == 1.235
    assert a["state"] == "ENGAGE" and a["action"] == "attack" and a["verdict"] == "ok"
    assert a["target"] == {"x": 400, "y": 300}
    assert a["player_map"] == [0.5, 0.5] and a["player_screen"] == [400, 320]
    assert a["flags"] == ["low_confidence"] and a["n_mobs"] == 1
    assert "verdict_reason" not in a
    assert b["state"] is None and b["action"] is None and b["target"] is None
    assert b["player_map"] is None and b["hp"] is None and b["n_mobs"] == 0
    assert b["verdict"] == "pause" and b["verdict_reason"] == "lost"


def test_numpy_values(rl):
    mob = Mob(x=np.int64(10), y=np.int32(20), w=np.int64(5), h=5,
              score=np.float32(0.8), template="m")
    p = Perception(t=np.float64(2.0), screen_size=(np.int64(800), 600),
                   player_map=(np.float32(0.25), np.float64(0.75)),
                   player_screen=(np.int64(1), np.int64(2)), player_screen_estimated=np.bool_(True),
                   mobs=(mob,), hp=np.float32(0.5), mp=np.float64(np.nan),
                   flags=frozenset(), confidence=np.float32(0.9))
    rl.log_tick(p, Decision(Action.IDLE, "PATROL", target=mob), OK)
    rl.flush()
    (r,) = lines(rl)
    assert r["t"] == 2.0 and r["player_map"] == [0.25, 0.75]
    assert r["target"] == {"x": 10, "y": 20}
    assert r["hp"] == 0.5 and r["mp"] is None          # NaN → null
    assert r["confidence"] == pytest.approx(0.9, abs=1e-3)


def test_never_raises_on_weird_input(rl, caplog):
    rl.log_tick(None, None, None)
    rl.log_tick(object(), "not a decision", "not a verdict")
    rl.log_tick(make_p(1.0), Decision(Action.IDLE, state=None), Verdict(None))
    rl.save_frame(None, "x")
    rl.save_frame(Frame(image=None, t=1.0, window=(0, 0, 0, 0)), "x")
    rl.save_frame(Frame(image="garbage", t=1.0, window=(0, 0, 0, 0)), "y")
    rl.close({"weird": object(), "arr": np.arange(3)})
    rl.log_tick(make_p(5.0), None, OK)                 # after close: silently ignored
    summary = json.loads((rl.run_dir / "summary.json").read_text())
    assert summary["arr"] == [0, 1, 2]
    for line in rl.ticks_path.read_text().splitlines():
        json.loads(line)                               # every written line is valid


def test_log_tick_error_logged_once(rl, caplog, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("disk on fire")
    monkeypatch.setattr(rl, "make_record", boom)
    with caplog.at_level("ERROR"):
        for _ in range(5):
            rl.log_tick(make_p(1.0), None, OK)
    assert sum("log_tick failed" in r.message for r in caplog.records) == 1


def test_flushes_at_least_every_second(rl, monkeypatch):
    import types as _types
    clock = [1000.0]
    monkeypatch.setattr(runlog_mod, "time",
                        _types.SimpleNamespace(monotonic=lambda: clock[0], time=lambda: 0.0))
    rl._last_flush = clock[0]
    rl.log_tick(make_p(1.0), None, OK)
    assert rl.ticks_path.read_text() == ""             # buffered
    clock[0] += 1.01
    rl.log_tick(make_p(2.0), None, OK)
    assert len(rl.ticks_path.read_text().splitlines()) == 2


def test_save_frame_writes_png_and_rate_limits_per_tag(rl):
    p = rl.save_frame(frame(10.0), "stuck")
    assert p is not None and p.exists() and p.parent == rl.run_dir / "frames"
    assert p.name == "10.000_stuck.png"
    assert p.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    assert rl.save_frame(frame(11.9), "stuck") is None        # < 2 s, same tag
    assert rl.save_frame(frame(11.0), "pause") is not None    # other tag ok
    assert rl.save_frame(frame(12.0), "stuck") is not None    # 2 s later ok


def test_save_frame_sanitises_tag(rl):
    p = rl.save_frame(frame(1.0), "../../evil tag")
    assert p.parent == rl.run_dir / "frames"
    assert "/" not in p.name and " " not in p.name


def test_save_frame_cap(rl):
    saved = [rl.save_frame(frame(i * 3.0), "lowconf") for i in range(runlog_mod.MAX_FRAMES_PER_RUN + 20)]
    assert sum(p is not None for p in saved) == runlog_mod.MAX_FRAMES_PER_RUN
    assert len(list((rl.run_dir / "frames").glob("*.png"))) == runlog_mod.MAX_FRAMES_PER_RUN


def test_save_frame_disabled(tmp_path):
    rl = RunLogger(tmp_path, make_settings(save_anomaly_frames=False))
    assert rl.save_frame(frame(1.0), "x") is None
    assert list((rl.run_dir / "frames").iterdir()) == []
    rl.close({})


def test_close_writes_summary_with_counts(rl):
    attack = Decision(Action.ATTACK, "ENGAGE", target=MOB)
    for i in range(3):
        rl.log_tick(make_p(i * 0.1), attack, OK)
    rl.log_tick(make_p(0.3), Decision(Action.WALK_LEFT, "PATROL"), OK)
    rl.log_tick(make_p(0.4, player=None), Decision(Action.IDLE, "WAIT"),
                Verdict(VerdictKind.PAUSE, "Can't see your character"))
    rl.close({"stop_reason": "stopped by user", "kills": 7})
    rl.close({"ignored": True})                        # idempotent
    s = json.loads((rl.run_dir / "summary.json").read_text())
    assert s["ticks"] == 5 and s["attacks"] == 3
    assert s["state_ticks"] == {"ENGAGE": 3, "PATROL": 1, "WAIT": 1}
    assert s["verdict_ticks"] == {"ok": 4, "pause": 1}
    assert s["verdict_reason"] == "Can't see your character"
    assert s["stop_reason"] == "stopped by user" and s["kills"] == 7
    assert "ignored" not in s and s["tick_span_s"] == pytest.approx(0.4)
    assert len(lines(rl)) == 5


def test_thread_safety(rl):
    def worker(k):
        for i in range(200):
            rl.log_tick(make_p(k * 1000 + i), Decision(Action.ATTACK, "ENGAGE"), OK)
            if i % 50 == 0:
                rl.save_frame(frame(k * 1000 + i), f"w{k}")
    ts = [threading.Thread(target=worker, args=(k,)) for k in range(4)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    rl.flush()
    assert len(lines(rl)) == 800 and rl.ticks == 800 and rl.attacks == 800
