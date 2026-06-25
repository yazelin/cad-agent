import re
import subprocess
import tempfile

# NOTE: the exact FreeCAD export API (exportStl/exportStep on a Part shape) is
# verified against the installed FreeCAD in Task 6; tune this prompt there if the
# first real run errors. The self-repair loop (server feeds stderr back) also
# catches API mismatches.
FREECAD_SYSTEM_PROMPT = """You write FreeCAD Python scripts to run headless under `freecadcmd`.

Hard rules:
- Put every tunable dimension as an UPPERCASE variable at the TOP of the script.
- Build the part as a Part shape (Part.makeBox, boolean cuts for holes, etc.).
- Import every name you use. `App` and `Part` are preloaded; for points use
  App.Vector(x, y, z). Do NOT use Base.Vector unless you `from FreeCAD import Base`.
- End by exporting BOTH files to the current directory (bare names, no path):
    shape.exportStl('out.stl')
    shape.exportStep('out.step')
- Output ONLY raw Python. No markdown fences, no prose, no explanation.
- Do not create or write any files; put the whole script in your reply text.
"""

# `claude -p` is agentic: left unrestricted it may use its Write tool to save the
# script to a file and reply with prose instead of printing the code. Disabling
# the file-mutating and shell tools forces the code into the text response.
DEFAULT_CLAUDE_CMD = [
    "claude", "-p",
    "--disallowed-tools", "Write", "Edit", "MultiEdit", "NotebookEdit", "Bash",
]

def build_prompt(user_msg: str, prev_script: str | None) -> str:
    parts = [FREECAD_SYSTEM_PROMPT]
    if prev_script:
        parts.append("Current script:\n" + prev_script)
        parts.append("Modify it to satisfy this request: " + user_msg)
    else:
        parts.append("Write a script for: " + user_msg)
    return "\n\n".join(parts)

_FENCE_RE = re.compile(r"```[a-zA-Z0-9_+-]*\n(.*?)```", re.DOTALL)

def strip_fences(text: str) -> str:
    text = text.strip()
    # Real claude output sometimes wraps the code in a fenced block AND adds
    # prose around it despite the prompt. Extract the first fenced block's
    # contents when present; otherwise treat the whole output as raw code.
    m = _FENCE_RE.search(text)
    if m:
        return m.group(1).strip()
    return text

def generate(user_msg: str, prev_script: str | None = None, *,
             claude_cmd: list[str] | None = None, timeout: float = 120) -> str:
    # The prompt goes on stdin, not argv: --disallowed-tools is variadic and
    # would swallow a positional prompt as tool names.
    prompt = build_prompt(user_msg, prev_script)
    cmd = claude_cmd or DEFAULT_CLAUDE_CMD
    # Run claude in a throwaway cwd: even with file tools disallowed, an agentic
    # claude must not be able to pollute the repo or the server's working dir.
    with tempfile.TemporaryDirectory(prefix="cad-agent-brain-") as cwd:
        proc = subprocess.run(cmd, input=prompt, cwd=cwd, capture_output=True,
                              text=True, timeout=timeout)
    return strip_fences(proc.stdout)
