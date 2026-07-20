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
- Always start the script with EXACTLY these two import lines (no variation):
    import FreeCAD as App
    import Part
  Do NOT write `from FreeCAD import App` — that raises ImportError under freecadcmd.
- Use App.Vector(x, y, z) for points.
- End by exporting BOTH files to the current directory (bare names, no path):
    shape.exportStl('out.stl')
    shape.exportStep('out.step')
- For fillets/rounds use Shape.makeFillet(radius, edges); for chamfers/bevels use
  Shape.makeChamfer(size, edges). Pick the edges from shape.Edges by geometry
  (a vertex's Z on the top face, an edge length matching a named side), never by
  guessing raw indices.
- Output ONLY raw Python. No markdown fences, no prose, no explanation.
- Do not create or write any files; put the whole script in your reply text.
"""

# Issue #5: a modification request is a feature-level edit. One-shot regeneration
# makes fillets/chamfers/threads fragile; the fix is to touch only the named
# feature and leave the rest of a working script untouched, so a failure's blast
# radius (and self-repair scope) stays small.
FEATURE_EDIT_GUIDANCE = """This is a feature-level edit. Change ONLY what the request names; keep the rest of the script exactly as-is:
- Reproduce every other line of the current script VERBATIM — same variable names, values, and order. Do not rewrite, rename, reorder, or "tidy" unrelated code.
- Add the requested feature as ONE small extra step on the already-built shape. For fillets/rounds use Shape.makeFillet(radius, edges); for chamfers/bevels use Shape.makeChamfer(size, edges). Do NOT rebuild the whole part just to add a fillet or chamfer.
- Identify the edges/faces named in the request geometrically from the built shape (e.g. filter shape.Edges by a vertex Z on the top face, or by an edge length that matches a named side). Add any new dimension (a fillet radius, a chamfer size) as a new UPPERCASE variable at the top.
- If the request only changes a number, change just that one variable's value and nothing else.
"""


def preserved_line_ratio(prev_script: str, new_script: str) -> float:
    """Fraction of the previous script's non-blank lines that survive verbatim.

    A true feature-level edit keeps almost every prior line and adds a small
    step, so this stays near 1.0; a full rewrite (renamed vars, reordered code)
    drops it well below. Used as a cheap proxy for "only touched the named
    feature" in the acceptance test.
    """
    prev_lines = [ln for ln in prev_script.splitlines() if ln.strip()]
    if not prev_lines:
        return 1.0
    new_lines = {ln for ln in new_script.splitlines() if ln.strip()}
    kept = sum(1 for ln in prev_lines if ln in new_lines)
    return kept / len(prev_lines)

# `claude -p` is agentic: use an allowlist so only the Read tool is available.
# Read is needed so claude can view the image by absolute path in generate_from_photo.
# All other tools (Write, Edit, Bash, WebFetch, WebSearch, Task, Glob, Grep, etc.)
# are implicitly denied because they are not on the allowlist. This hardens both
# generate (text) and generate_from_photo (vision), which share this cmd.
DEFAULT_CLAUDE_CMD = [
    "claude", "-p",
    "--allowed-tools", "Read",
]

def build_prompt(user_msg: str, prev_script: str | None) -> str:
    parts = [FREECAD_SYSTEM_PROMPT]
    if prev_script:
        parts.append("Current script:\n" + prev_script)
        parts.append(FEATURE_EDIT_GUIDANCE)
        parts.append("Modify it to satisfy this request: " + user_msg)
    else:
        parts.append("Write a script for: " + user_msg)
    return "\n\n".join(parts)

_FENCE_RE = re.compile(r"```[a-zA-Z0-9_+-]*\n(.*?)```", re.DOTALL)
# a line that looks like the start of a FreeCAD script (import / comment / assignment / App./Part.)
_CODE_START = re.compile(r"^\s*(import |from |#|App\.|Part\.|[A-Za-z_][A-Za-z0-9_]*\s*=)")

def _trim_to_code(code: str) -> str:
    # bracket the real code: drop leading prose (before the first code-looking
    # line) and trailing prose (after the last export). claude sometimes prepends
    # "Here's the script:" or appends a "skipped: ..." note despite the prompt.
    lines = code.splitlines()
    start = 0
    for i, ln in enumerate(lines):
        if _CODE_START.match(ln):
            start = i
            break
    last = -1
    for i, ln in enumerate(lines):
        if "exportStl(" in ln or "exportStep(" in ln:
            last = i
    if last >= start:
        return "\n".join(lines[start:last + 1]).strip()
    return code

def strip_fences(text: str) -> str:
    text = text.strip()
    # Real claude output sometimes wraps the code in a fenced block AND adds
    # prose around it despite the prompt. Extract the first fenced block's
    # contents when present; otherwise treat the whole output as raw code.
    m = _FENCE_RE.search(text)
    if m:
        text = m.group(1).strip()
    return _trim_to_code(text)

PHOTO_SYSTEM_PROMPT = """You reverse-engineer a photographed mechanical part into a FreeCAD Python script.

- Look at the image referenced by path in the user message.
- Decompose the part into Part primitives (Part.makeBox, Part.makeCylinder, boolean cuts).
- Put every tunable dimension as an UPPERCASE variable at the TOP. If no scale is
  visible, assume the largest dimension is about 100 mm.
- Always start the script with EXACTLY these two import lines (no variation):
    import FreeCAD as App
    import Part
  Do NOT write `from FreeCAD import App` — that raises ImportError under freecadcmd.
- Use App.Vector(x, y, z) for points.
- End by exporting BOTH files to the current directory (bare names):
    shape.exportStl('out.stl')
    shape.exportStep('out.step')
- Output ONLY raw Python. No markdown fences, no prose, no explanation.
- Do not write files; put the whole script in your reply text.
- Build each part's holes/features in that part's own LOCAL coordinates first,
  then rotate/position the finished part. Do not compute hole/feature coordinates
  in an already-rotated frame.
"""

def build_photo_prompt(image_path: str, hint: str | None,
                       prev_script: str | None = None) -> str:
    parts = [PHOTO_SYSTEM_PROMPT, f"Image to model (read this file): {image_path}"]
    if prev_script:
        parts.append("Current script (revise it, do not start over):\n" + prev_script)
        parts.append(FEATURE_EDIT_GUIDANCE)
        parts.append("Look at the image again and apply this change, fixing the "
                     "script to better match the part: "
                     + (hint or "improve the script's fidelity to the image"))
    elif hint:
        parts.append("Hint for dimensions/material/notes: " + hint)
    return "\n\n".join(parts)

def generate_from_photo(image_path: str, hint: str | None = None,
                        prev_script: str | None = None, *,
                        claude_cmd: list[str] | None = None, timeout: float = 180) -> str:
    # Reuses DEFAULT_CLAUDE_CMD: it allows Read (so claude can view the image) and
    # disallows file/shell mutation. The image is read by absolute path, so the
    # throwaway cwd is only a pollution guard.
    prompt = build_photo_prompt(image_path, hint, prev_script)
    cmd = claude_cmd or DEFAULT_CLAUDE_CMD
    with tempfile.TemporaryDirectory(prefix="cad-agent-photo-") as cwd:
        proc = subprocess.run(cmd, input=prompt, cwd=cwd, capture_output=True,
                              text=True, timeout=timeout)
    return strip_fences(proc.stdout)

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
