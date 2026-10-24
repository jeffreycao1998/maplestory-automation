"""
A tiny interpreter for the subset of cmd.exe that start.bat uses, with a fake Windows.

It lets the tests walk every start.bat path (first run, normal run, pyproject changed,
app already open, slow start, crash, venv failure, elevation refused) on Linux. It is
not a general batch interpreter: unknown lines raise, so new constructs in start.bat
must be taught here first.

goto follows cmd semantics: search forward from the current line for the label, then
wrap to the top -- so duplicated labels behave as badly here as they do on Windows.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set


@dataclass
class World:
    admin: bool = True
    elevated_arg: bool = False          # start.bat was relaunched with --elevated
    elevation_ok: bool = True           # user clicks Yes on the UAC prompt
    python_ok: bool = True
    venv_exists: bool = True
    venv_create_ok: bool = True
    installed_matches: Optional[bool] = True   # None = no installed-pyproject.txt
    pip_ok: bool = True
    pythonw_exists: bool = True
    # What a background pythonw launch does: "ok" | "slow" | "crash" (writes app.pid,
    # dies a second later) | "dead" (dies at once, e.g. import error) | "hang" (alive,
    # never writes app.pid)
    launch: str = "ok"
    launch_delay_s: int = 2             # seconds until app.pid appears ("ok"/"slow")
    already_open: bool = False          # a live copy already holds app.lock / app.pid
    stale_pid: bool = False             # app.pid left behind by a dead process

    # --- recorded effects
    t: int = 0
    started_pythonw: int = 0
    started_console: int = 0
    pip_installs: int = 0
    pid_deleted_while_alive: bool = False
    pauses: int = 0
    echoed: List[str] = field(default_factory=list)
    exit_code: Optional[int] = None
    trace: List[str] = field(default_factory=list)   # labels visited

    # --- internal process state
    _files: Set[str] = field(default_factory=set)
    _app_pid_at: Optional[int] = None     # time app.pid appears
    _app_alive: bool = False
    _bg_alive_until: Optional[int] = None

    def setup(self):
        if self.venv_exists:
            self._files |= {r".venv\Scripts\python.exe"}
            if self.pythonw_exists:
                self._files.add(r".venv\Scripts\pythonw.exe")
        if self.installed_matches is not None:
            self._files.add(r".venv\installed-pyproject.txt")
        if self.already_open:
            self._files.add(r"logs\app.pid")
            self._app_alive = True
            self._app_pid_at = -1
        elif self.stale_pid:
            self._files.add(r"logs\app.pid")

    # pid file present and its process alive?
    def pid_file(self) -> bool:
        if self._app_pid_at is not None and self.t >= self._app_pid_at and self._app_alive:
            self._files.add(r"logs\app.pid")
        if self.launch == "crash" and self._app_pid_at is not None and not self.already_open \
                and self.t >= self._app_pid_at + 1:
            self._app_alive = False
            self._files.discard(r"logs\app.pid")     # atexit removes it
        return r"logs\app.pid" in self._files

    def pid_alive(self) -> bool:
        return self.pid_file() and self._app_alive

    def pyw_alive(self) -> bool:
        self.pid_file()
        if self.already_open or self._app_alive:
            return True
        return self._bg_alive_until is not None and self.t < self._bg_alive_until

    def start_background(self):
        self.started_pythonw += 1
        if self.already_open:
            return          # the new copy sees the lock and exits
        if self.launch == "dead":
            return
        if self.launch in ("ok", "slow", "crash"):
            self._app_alive = True
            self._app_pid_at = self.t + self.launch_delay_s
        elif self.launch == "hang":
            self._bg_alive_until = 10 ** 9   # never writes app.pid


LABEL_RE = re.compile(r"^:([A-Za-z_]+)\s*$")


class Batch:
    def __init__(self, text: str, world: World, max_steps: int = 20000):
        self.lines = text.replace("\r\n", "\n").split("\n")
        self.w = world
        self.vars: Dict[str, str] = {}
        self.errorlevel = 0
        self.max_steps = max_steps

    # ---- helpers
    def find_label(self, name: str, start: int) -> int:
        n = len(self.lines)
        for k in list(range(start, n)) + list(range(0, start)):
            m = LABEL_RE.match(self.lines[k].strip())
            if m and m.group(1).lower() == name.lower():
                return k
        raise KeyError(f"label :{name} not found")

    def expand(self, s: str) -> str:
        return re.sub(r"%([A-Za-z_]+)%", lambda m: self.vars.get(m.group(1), ""), s)

    def exists(self, path: str) -> bool:
        path = path.strip('"')
        if path == "logs":
            return True
        if path == r"logs\app.pid":
            return self.w.pid_file()
        return path in self.w._files

    def cond(self, c: str) -> bool:
        c = c.strip()
        neg = False
        if c.startswith("not "):
            neg, c = True, c[4:].strip()
        if c.startswith("errorlevel "):
            r = self.errorlevel >= int(c.split()[1])
        elif c.startswith("exist "):
            r = self.exists(c[6:].strip())
        elif c.startswith("defined "):
            r = bool(self.vars.get(c[8:].strip()))
        elif c.startswith('/i "%~1"=='):
            r = self.w.elevated_arg and c.endswith('"--elevated"')
        elif "GEQ" in c:
            a, b = [x.strip() for x in self.expand(c).split("GEQ")]
            r = int(a or 0) >= int(b)
        elif "==" in c:
            a, b = self.expand(c).split("==")
            r = a.strip().strip('"') == b.strip().strip('"')
        else:
            raise ValueError(f"unknown condition: {c}")
        return r != neg

    def split_if(self, line: str):
        """'if <cond> <action>' -> (cond, action)."""
        body = line[3:]
        for kw in (" goto ", " exit ", " set ", " del ", " mkdir "):
            i = body.find(kw)
            if i >= 0:
                return body[:i], body[i + 1:]
        raise ValueError(f"can't split if: {line}")

    # ---- run
    def run(self) -> World:
        w = self.w
        w.setup()
        pc = 0
        stack: List[int] = []
        steps = 0
        while pc < len(self.lines):
            steps += 1
            if steps > self.max_steps:
                raise RuntimeError("too many steps (infinite loop?)")
            line = self.lines[pc].strip()
            pc += 1
            res = self.execute(line, pc, stack)
            if res is None:
                continue
            kind, val = res
            if kind == "goto":
                pc = self.find_label(val, pc) + 1
                w.trace.append(val)
            elif kind == "call":
                stack.append(pc)
                pc = self.find_label(val, pc) + 1
            elif kind == "exit":
                if stack:
                    pc = stack.pop()
                    self.errorlevel = val
                else:
                    w.exit_code = val
                    return w
        w.exit_code = self.errorlevel
        return w

    def execute(self, line: str, pc: int, stack):
        w = self.w
        low = line.lower()
        if not line or low.startswith("rem") or low.startswith("@echo") or low.startswith(":"):
            return None
        if low.startswith("if "):
            c, action = self.split_if(line)
            if self.cond(c):
                return self.execute(action, pc, stack)
            return None
        if low.startswith("goto "):
            return ("goto", line.split(":", 1)[1].strip())
        if low.startswith("call :"):
            return ("call", line.split(":", 1)[1].strip())
        if low.startswith("exit /b"):
            parts = line.split()
            return ("exit", int(parts[2]) if len(parts) > 2 else self.errorlevel)
        if low.startswith(("setlocal", "title", "cd ", "mkdir")):
            return None
        if low.startswith("echo"):
            if "| findstr" in low:     # numeric PID check
                v = self.expand(line[5:].split("|")[0])
                self.errorlevel = 0 if v.isdigit() else 1
            else:
                w.echoed.append(line[5:])
            return None
        if low == "pause":
            w.pauses += 1
            return None
        if low.startswith("set /a "):
            expr = line[7:]
            if "+=" in expr:
                k, v = expr.split("+=")
                self.vars[k] = str(int(self.vars.get(k, "0")) + int(v))
                if k == "WAITED":
                    pass
            else:
                k, v = expr.split("=")
                self.vars[k] = v
            return None
        if low.startswith("set /p "):
            k = line[7:].split("=")[0]
            self.vars[k] = "4242" if w.pid_file() else ""
            return None
        if low.startswith('set "'):
            k, v = line[5:].rstrip('"').split("=", 1)
            self.vars[k] = self.expand(v)
            return None
        if low.startswith("timeout"):
            w.t += int(line.split()[2])
            return None
        if low.startswith("net session"):
            self.errorlevel = 0 if w.admin else 2
            return None
        if low.startswith("fltmc"):
            self.errorlevel = 0 if w.admin else 1
            return None
        if low.startswith("powershell") and "Start-Process" in line:
            self.errorlevel = 0 if w.elevation_ok else 1
            return None
        if low.startswith("powershell") and "Win32_Process" in line:
            self.errorlevel = 0 if w.pyw_alive() else 1
            return None
        if low.startswith(("py -3 -c", "python -c")):
            self.errorlevel = 0 if w.python_ok else 1
            return None
        if low.startswith("start \"\" \"https"):
            return None
        if low.startswith("%py% -m venv"):
            self.errorlevel = 0 if w.venv_create_ok else 1
            if w.venv_create_ok:
                w._files |= {r".venv\Scripts\python.exe", r".venv\Scripts\pythonw.exe"}
            return None
        if low.startswith("del "):
            target = re.findall(r'"([^"]+)"', line)[0]
            if target == r"logs\app.pid" and w.pid_alive():
                w.pid_deleted_while_alive = True
            w._files.discard(target)
            return None
        if low.startswith("fc /b"):
            self.errorlevel = 0 if w.installed_matches else 1
            return None
        if '-m pip install -e' in low:
            w.pip_installs += 1
            self.errorlevel = 0 if w.pip_ok else 1
            return None
        if '-m pip install --upgrade pip' in low:
            return None
        if low.startswith("copy /y"):
            w._files.add(r".venv\installed-pyproject.txt")
            return None
        if low.startswith('start "maplebot" /b') and "pythonw.exe" in low:
            w.start_background()
            return None
        if low.startswith('".venv\\scripts\\python.exe" -m maplebot'):
            w.started_console += 1
            return None
        if low.startswith("tasklist"):
            self.errorlevel = 0 if w.pid_alive() else 1
            return None
        raise ValueError(f"batsim doesn't understand: {line}")
