import math
import re

# Column-0 UPPERCASE numeric assignment, e.g. `WIDTH = 100` / `HOLE_D = 8.5  # 孔徑`.
# The generation prompts contract that every tunable dimension is such a line.
_PARAM_RE = re.compile(
    r"^(?P<name>[A-Z][A-Z0-9_]*)\s*=\s*(?P<value>-?\d+(?:\.\d+)?)\s*(?P<trail>#.*)?$"
)


def parse_params(script: str) -> list[dict]:
    """Ordered [{name, value}] from column-0 UPPERCASE numeric assignments.

    A name assigned twice keeps its first position, last value (what the
    script actually runs with).
    """
    out: list[dict] = []
    index: dict[str, int] = {}
    for line in script.splitlines():
        m = _PARAM_RE.match(line)
        if not m:
            continue
        name, value = m.group("name"), float(m.group("value"))
        if name in index:
            out[index[name]]["value"] = value
        else:
            index[name] = len(out)
            out.append({"name": name, "value": value})
    return out


def _fmt(v: float) -> str:
    return str(int(v)) if float(v).is_integer() else repr(float(v))


def substitute(script: str, new: dict[str, float]) -> str:
    """Rewrite the numeric literal of matching param lines; keep trailing comments."""
    for v in new.values():
        if not math.isfinite(float(v)):
            raise ValueError("param values must be finite numbers")
    lines = script.splitlines()
    for i, line in enumerate(lines):
        m = _PARAM_RE.match(line)
        if m and m.group("name") in new:
            trail = f"  {m.group('trail')}" if m.group("trail") else ""
            lines[i] = f"{m.group('name')} = {_fmt(new[m.group('name')])}{trail}"
    return "\n".join(lines)
