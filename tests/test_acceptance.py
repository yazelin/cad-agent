import shutil
import struct
import tempfile
from pathlib import Path

import pytest

from cad_agent import brain, runner

# Real end-to-end: needs a FreeCAD headless binary AND the claude CLI. Auto-skips
# when FreeCAD is absent so the suite stays green on machines without it.
pytestmark = pytest.mark.skipif(
    shutil.which(runner.default_freecad_cmd()) is None,
    reason="no FreeCAD headless binary found",
)


def _stl_triangle_count(path: Path) -> int:
    data = path.read_bytes()
    if data[:5] == b"solid" and b"facet" in data[:512]:
        return data.count(b"facet normal")  # ascii STL
    return struct.unpack("<I", data[80:84])[0]  # binary STL header count


def test_mounting_plate_end_to_end():
    # FIXTURE, not a product feature: the app accepts any description.
    # snap FreeCAD cannot read /tmp (pytest tmp_path) or dotdirs, so the scratch
    # must be a non-hidden dir under $HOME.
    scratch = Path(tempfile.mkdtemp(prefix="cad-agent-test-", dir=Path.home()))
    try:
        script = brain.generate(
            "a flat plate 80mm x 40mm x 5mm with a 5mm diameter through-hole "
            "near each of the four corners"
        )
        result = runner.run_freecad(script, scratch_base=scratch, timeout=180)
        assert result.ok, f"run failed: {result.stderr}\n--- script ---\n{script}"
        assert result.stl_path and _stl_triangle_count(result.stl_path) > 0
        assert result.step_path and result.step_path.stat().st_size > 0
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
