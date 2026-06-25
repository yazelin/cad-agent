import os
import shutil
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path

# Default scratch lives under $HOME and is NOT hidden: the snap FreeCAD has a
# private /tmp and its `home` interface cannot read dotfiles, so neither /tmp
# nor a ~/.dotdir scratch is visible to freecad.cmd. A plain ~/cad-agent-scratch
# is. Override with CAD_AGENT_SCRATCH.
DEFAULT_SCRATCH = Path(
    os.environ.get("CAD_AGENT_SCRATCH", str(Path.home() / "cad-agent-scratch"))
)
# The headless FreeCAD binary is named differently per install (apt: freecadcmd,
# snap: freecad.cmd). Resolve at call time; override with CAD_AGENT_FREECAD.
FREECAD_CANDIDATES = ("freecadcmd", "freecad.cmd", "FreeCADCmd")
# env whitelist, not blacklist -- anything not listed never reaches the child.
SAFE_ENV_KEYS = {"PATH", "HOME", "LANG", "LC_ALL", "LC_CTYPE", "DISPLAY", "TMPDIR"}

def default_freecad_cmd() -> str:
    override = os.environ.get("CAD_AGENT_FREECAD")
    if override:
        return override
    for candidate in FREECAD_CANDIDATES:
        if shutil.which(candidate):
            return candidate
    return "freecadcmd"

@dataclass
class RunResult:
    ok: bool
    timed_out: bool
    stdout: str
    stderr: str
    stl_path: Path | None
    step_path: Path | None
    workdir: Path

def _scrubbed_env() -> dict:
    return {k: v for k, v in os.environ.items() if k in SAFE_ENV_KEYS}

def run_freecad(script: str, *, timeout: float = 30.0,
                cmd: list[str] | None = None,
                scratch_base: Path | None = None) -> RunResult:
    base = scratch_base or DEFAULT_SCRATCH
    workdir = base / uuid.uuid4().hex[:12]
    workdir.mkdir(parents=True, exist_ok=True)
    script_path = workdir / "model.py"
    script_path.write_text(script)
    run_cmd = (cmd or [default_freecad_cmd()]) + [str(script_path)]
    try:
        proc = subprocess.run(
            run_cmd, cwd=workdir, env=_scrubbed_env(),
            capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired as e:
        return RunResult(False, True, e.stdout or "", e.stderr or "",
                         None, None, workdir)
    stl = workdir / "out.stl"
    step = workdir / "out.step"
    ok = proc.returncode == 0 and stl.exists()
    return RunResult(ok, False, proc.stdout, proc.stderr,
                     stl if stl.exists() else None,
                     step if step.exists() else None, workdir)
