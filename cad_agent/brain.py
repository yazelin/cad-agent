import re
import subprocess

# NOTE: the exact FreeCAD export API (exportStl/exportStep on a Part shape) is
# verified against the installed FreeCAD in Task 6; tune this prompt there if the
# first real run errors. The self-repair loop (server feeds stderr back) also
# catches API mismatches.
FREECAD_SYSTEM_PROMPT = """You write FreeCAD Python scripts to run headless under `freecadcmd`.

Hard rules:
- Put every tunable dimension as an UPPERCASE variable at the TOP of the script.
- Build the part as a Part shape (Part.makeBox, boolean cuts for holes, etc.).
- End by exporting BOTH files to the current directory (bare names, no path):
    shape.exportStl('out.stl')
    shape.exportStep('out.step')
- Output ONLY raw Python. No markdown fences, no prose, no explanation.
"""

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
    prompt = build_prompt(user_msg, prev_script)
    cmd = (claude_cmd or ["claude", "-p"]) + [prompt]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    return strip_fences(proc.stdout)
