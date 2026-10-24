"""Settings must load/save without PyYAML (no Windows wheel on brand-new Pythons)."""
import subprocess
import sys
import textwrap


def test_settings_round_trip_without_pyyaml(tmp_path):
    code = textwrap.dedent(f"""
        import sys, builtins
        real = builtins.__import__
        def fake(name, *a, **k):
            if name == "yaml" or name.startswith("yaml."):
                raise ImportError("no yaml")
            return real(name, *a, **k)
        builtins.__import__ = fake
        sys.path.insert(0, "src")
        from pathlib import Path
        from maplebot.settings import Profile, Settings
        p = Profile(Path(r"{tmp_path}"))
        s = Settings(); s.keys.attack = "z"; s.calibration.minimap_box = [1, 2, 3, 4]
        p.save(s)
        s2 = p.load()
        assert s2.keys.attack == "z" and s2.calibration.minimap_box == [1, 2, 3, 4]
        from maplebot.supervisor import RunLogger
        RunLogger(Path(r"{tmp_path}") / "logs", s2).close({{}})
        print("ok")
    """)
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    assert "ok" in out.stdout
