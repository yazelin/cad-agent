import os
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path

DEFAULT_SCRATCH = Path(os.environ.get("CAD_AGENT_SCRATCH", "/tmp/cad-agent-scratch"))
# env whitelist, not blacklist -- anything not listed never reaches the child.
SAFE_ENV_KEYS = {"PATH", "HOME", "LANG", "LC_ALL", "LC_CTYPE", "DISPLAY", "TMPDIR"}

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
    run_cmd = (cmd or ["freecadcmd"]) + [str(script_path)]
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
