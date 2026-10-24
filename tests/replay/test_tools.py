"""tools.*: import on Linux, --help, eval metrics, make_synthetic, replay end to end."""
from __future__ import annotations

import csv
import importlib
import json
import subprocess
import sys
from pathlib import Path

import cv2
import pytest

ROOT = Path(__file__).resolve().parents[2]
TOOLS = ["tools.record", "tools.replay", "tools.eval_perception", "tools.make_synthetic"]


@pytest.mark.parametrize("mod", TOOLS)
def test_tool_imports(mod):
    m = importlib.import_module(mod)
    assert callable(m.main)


@pytest.mark.parametrize("mod", TOOLS)
def test_help_in_process(mod, capsys):
    m = importlib.import_module(mod)
    with pytest.raises(SystemExit) as e:
        m.main(["--help"])
    assert e.value.code == 0
    assert "usage" in capsys.readouterr().out.lower()


def test_help_as_module_subprocess():
    r = subprocess.run([sys.executable, "-m", "tools.replay", "--help"], cwd=ROOT,
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0 and "usage" in r.stdout.lower()


def test_record_window_not_found_message(tmp_path, capsys):
    from tools import record
    rc = record.main(["--name", "x", "--out", str(tmp_path), "--profile", str(tmp_path / "p"),
                      "--title", "NoSuchWindowXYZ", "--delay", "0"])
    out = capsys.readouterr().out
    assert rc == 1
    assert "NoSuchWindowXYZ" in out and "windowed" in out
    assert not (tmp_path / "x").exists()


# ---- eval matching ----------------------------------------------------------------
def test_match_mobs_half_box_rule():
    from tools.eval_perception import match_mobs
    boxes = [[100, 100, 40, 40]]            # centre (120, 120)
    m, fn, fp = match_mobs(boxes, [(140, 140)])
    assert m == [(0, 0)] and fn == [] and fp == []
    m, fn, fp = match_mobs(boxes, [(141, 120)])
    assert m == [] and fn == [0] and fp == [0]


def test_match_mobs_one_to_one_nearest_first():
    from tools.eval_perception import match_mobs
    boxes = [[100, 100, 40, 40], [130, 100, 40, 40]]     # centres 120 and 150
    m, fn, fp = match_mobs(boxes, [(148, 120), (125, 120), (122, 121)])
    assert sorted(m) == [(0, 2), (1, 0)] and fn == [] and fp == [1]


def test_score_frame_counts():
    from maplebot.types import Mob, Perception
    from tools.eval_perception import Totals, score_frame
    truth = {"mobs": [{"box": [0, 0, 20, 20]}, {"box": [100, 0, 20, 20]}],
             "player_map": [0.5, 0.5], "player_screen": [50, 50], "hp": 0.5, "mp": None}
    p = Perception(t=0, screen_size=(200, 100), player_map=(0.51, 0.5), player_screen=(53, 54),
                   player_screen_estimated=False,
                   mobs=(Mob(10, 10, 20, 20, 0.9, "a"), Mob(60, 60, 20, 20, 0.8, "a")),
                   hp=0.45, mp=0.3)
    tot = Totals()
    row = score_frame(truth, p, 5.0, True, tot)
    s = tot.summary()
    assert (row["tp"], row["fp"], row["fn"]) == (1, 1, 1)
    assert s["mob_recall"] == 0.5 and s["mob_fp_per_frame"] == 1.0
    assert s["minimap_x_err_mean"] == pytest.approx(0.01)
    assert s["player_screen_err_px_mean"] == pytest.approx(5.0)
    assert s["hp_abs_err_mean"] == pytest.approx(0.05)
    assert s["mp_abs_err_mean"] is None


def test_eval_synthetic_runs_and_is_exact_on_ui(tmp_path, capsys):
    from tools import eval_perception
    rc = eval_perception.main(["--synthetic", "3", "--reports", str(tmp_path),
                               "--json", str(tmp_path / "s.json")])
    assert rc == 0
    out = capsys.readouterr().out
    assert "recall" in out and "ms" in out
    csvs = list(tmp_path.glob("eval_*.csv"))
    assert len(csvs) == 1
    rows = list(csv.DictReader(open(csvs[0], encoding="utf-8")))
    assert len(rows) == 3
    s = json.loads((tmp_path / "s.json").read_text())["synthetic"]
    # minimap dot, player name tag and bars are pixel-exact in the generator
    assert s["minimap_missed"] == 0 and s["minimap_x_err_max"] < 0.005
    assert s["hp_abs_err_max"] < 0.02 and s["mp_abs_err_max"] < 0.02
    assert s["mob_precision"] is not None


def test_eval_no_labelled_data_falls_back_to_synthetic(tmp_path, capsys):
    from tools import eval_perception
    rc = eval_perception.main(["--data", str(tmp_path / "empty"), "--reports", str(tmp_path),
                               "--size", "640x480"])
    assert rc == 0
    assert "synthetic" in capsys.readouterr().out


# ---- make_synthetic -> eval on it ----------------------------------------------------
def test_make_synthetic_then_eval(tmp_path, capsys):
    from tools import eval_perception, make_synthetic
    out = tmp_path / "syn"
    assert make_synthetic.main(["--n", "3", "--out", str(out), "--seed", "7"]) == 0
    pngs = sorted(out.glob("*.png"))
    assert [p.name for p in pngs] == ["000001.png", "000002.png", "000003.png"]
    assert (out / "templates" / "mob_synthetic-0.png").is_file()
    assert (out / "templates" / "player.png").is_file()
    label = json.loads((out / "000001.json").read_text())
    for k in ("minimap_box", "player_map", "player_screen", "mobs", "hp", "mp"):
        assert k in label
    assert cv2.imread(str(pngs[0])).shape == (768, 1024, 3)
    capsys.readouterr()
    assert eval_perception.main(["--data", str(out), "--reports", str(tmp_path)]) == 0
    text = capsys.readouterr().out
    assert "3 frames" in text and "1 mob templates" in text


def test_make_synthetic_default_size_is_small(tmp_path):
    from tools import make_synthetic
    assert make_synthetic.MAX_DEFAULT_FRAMES <= 20
    files = make_synthetic.make_dataset(tmp_path, 1)
    assert files[0].stat().st_size < 1_500_000


def test_eval_crop_templates(tmp_path, capsys):
    from tools import eval_perception, make_synthetic
    make_synthetic.make_dataset(tmp_path / "d", 2)
    import shutil
    shutil.rmtree(tmp_path / "d" / "templates")
    assert eval_perception.main(["--data", str(tmp_path / "d"), "--crop-templates",
                                 "--reports", str(tmp_path)]) == 0
    assert "cropped from labels" in capsys.readouterr().out


# ---- replay --------------------------------------------------------------------------
def test_replay_recording_folder(tmp_path, tmp_profile, capsys):
    from maplebot.replay.synthetic import SceneSequence
    from tools import replay
    from tools._common import write_png
    seq = SceneSequence(seed=0)
    rec = tmp_path / "rec1"
    for i in range(6):
        write_png(rec / "frames" / f"{i + 1:06d}.png", seq.frame(i / 5).image)
    (rec / "meta.json").write_text(json.dumps({"fps": 5}))
    rc = replay.main([str(rec), "--profile", str(tmp_profile.root), "--logs", str(tmp_path / "logs")])
    out = capsys.readouterr().out
    assert rc == 0, out
    assert "6 frames" in out and "states:" in out and "actions:" in out
    runs = list((tmp_path / "logs" / "replay_rec1").iterdir())
    assert len(runs) == 1
    summary = json.loads((runs[0] / "summary.json").read_text())
    assert summary["frames"] == 6 and summary["keys_held_at_end"] == []
    lines = (runs[0] / "ticks.jsonl").read_text().splitlines()
    assert len(lines) == 6


def test_replay_closed_loop_kills_mobs(tmp_path):
    """Closed loop on the synthetic world: the bot must find, face and kill mobs."""
    import tempfile
    from maplebot.replay import synthetic as S
    from maplebot.replay.folder_capture import SequenceCapture
    from tools.replay import run_pipeline
    seq = S.SceneSequence(seed=0)
    settings = seq.settings()
    cap = SequenceCapture(seq, fps=15, max_frames=300)
    paths = S.write_templates(tmp_path / "t")
    r = run_pipeline(cap, settings, [paths[0]], paths[1], tmp_path / "logs",
                     on_decision=lambda d: cap.on_action(d.action))
    assert r["frames"] == 300
    assert r["verdict_ticks"].get("ok") == 300
    assert r["state_ticks"].get("PATROL", 0) > 0 and r["state_ticks"].get("ENGAGE", 0) > 0
    assert seq.kills >= 2
    assert r["keys_held_at_end"] == []


def test_replay_bad_args(tmp_path, capsys):
    from tools import replay
    assert replay.main([]) == 2
    assert replay.main([str(tmp_path / "missing")]) == 1
    assert replay.main([str(tmp_path)]) == 1          # no frames
