import shutil
import struct
import tempfile
from pathlib import Path
from typing import Tuple

import pytest

from cad_agent import brain, runner

# Real end-to-end: needs both the FreeCAD headless binary AND the claude CLI.
# Auto-skips when either is absent so the suite stays green on machines without them.
pytestmark = pytest.mark.skipif(
    shutil.which(runner.default_freecad_cmd()) is None or shutil.which("claude") is None,
    reason="no FreeCAD headless binary or claude CLI found",
)


def _parse_stl(path: Path) -> Tuple[int, list]:
    """Return (triangle_count, list_of_all_vertices).

    Handles both formats:
    - Binary STL: 80-byte header, uint32 count, then 50 bytes/triangle
      (12-byte normal + 3 * 12-byte vertex + 2-byte attribute).
    - ASCII STL: lines containing 'vertex x y z'.
    """
    data = path.read_bytes()

    # Detect ASCII by checking if the first 256 bytes look like text starting
    # with "solid" and containing "facet". Some binary STLs also start with
    # "solid" (technically invalid), so also check for the "facet" keyword
    # early in the file.
    is_ascii = data[:5] == b"solid" and b"facet" in data[:256]

    if is_ascii:
        vertices = []
        count = 0
        for line in data.decode("utf-8", errors="replace").splitlines():
            stripped = line.strip()
            if stripped.startswith("facet normal"):
                count += 1
            elif stripped.startswith("vertex "):
                parts = stripped.split()
                vertices.append((float(parts[1]), float(parts[2]), float(parts[3])))
        return count, vertices
    else:
        # Binary STL
        tri_count = struct.unpack_from("<I", data, 80)[0]
        vertices = []
        offset = 84
        for _ in range(tri_count):
            # skip 12-byte normal
            v_offset = offset + 12
            for vi in range(3):
                x, y, z = struct.unpack_from("<fff", data, v_offset + vi * 12)
                vertices.append((x, y, z))
            offset += 50  # 12 normal + 3*12 vertices + 2 attr
        return tri_count, vertices


def _bounding_box_extents(vertices: list) -> Tuple[float, float, float]:
    """Return (dx, dy, dz) extents from a list of (x, y, z) vertex tuples."""
    xs = [v[0] for v in vertices]
    ys = [v[1] for v in vertices]
    zs = [v[2] for v in vertices]
    return (max(xs) - min(xs), max(ys) - min(ys), max(zs) - min(zs))


def test_mounting_plate_end_to_end():
    """Spec §8 pass condition: bounding box ~80x40x5 (±1 mm) and four holes present.

    The plate is 80mm x 40mm x 5mm with four 5mm-diameter through-holes near corners.

    Bounding box check: sorted extents must match sorted [5, 40, 80] within ±1 mm.
    Axis assignment may vary by FreeCAD orientation, so we compare sorted extents.

    Holes check: a plain box exports as 12 triangles.  Four cylindrical through-holes
    add many curved-surface triangles.  We assert triangle_count >= 50 as a proxy
    for "four holes are present" (cylinders are tessellated with many facets).
    """
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
        assert result.stl_path and result.stl_path.exists(), "STL file not produced"
        assert result.step_path and result.step_path.stat().st_size > 0, "STEP file empty"

        tri_count, vertices = _parse_stl(result.stl_path)
        assert tri_count > 0, "STL has no triangles"
        assert len(vertices) > 0, "STL has no vertices"

        # Bounding box check (spec §8: 80x40x5 ±1 mm, order-independent)
        TOLERANCE = 1.0  # mm
        EXPECTED_DIMS = sorted([80.0, 40.0, 5.0])
        dx, dy, dz = _bounding_box_extents(vertices)
        actual_sorted = sorted([dx, dy, dz])
        for actual, expected in zip(actual_sorted, EXPECTED_DIMS):
            assert abs(actual - expected) <= TOLERANCE, (
                f"Bounding box extent mismatch: got sorted extents "
                f"{[round(v, 3) for v in actual_sorted]} mm, "
                f"expected {EXPECTED_DIMS} mm (±{TOLERANCE} mm). "
                f"Full extents: dx={dx:.3f} dy={dy:.3f} dz={dz:.3f}"
            )

        # Holes proxy check (spec §8: four through-holes exist).
        # A plain solid box = 12 triangles; cylindrical holes add many curved facets.
        MIN_TRIANGLES_WITH_HOLES = 50
        assert tri_count >= MIN_TRIANGLES_WITH_HOLES, (
            f"Expected >= {MIN_TRIANGLES_WITH_HOLES} triangles as proxy for four holes, "
            f"got {tri_count}. A plain box is 12 triangles; the low count suggests "
            f"holes were not cut."
        )
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def test_photo_to_buildable_part():
    fixture = Path(__file__).parent / "fixtures" / "part.png"
    if not fixture.exists():
        pytest.skip("no fixture image")
    # snap FreeCAD cannot read /tmp; use a non-hidden dir under $HOME
    scratch = Path(tempfile.mkdtemp(prefix="cad-agent-photo-test-", dir=Path.home()))
    try:
        script = brain.generate_from_photo(str(fixture), "largest dimension about 100mm")
        result = runner.run_freecad(script, scratch_base=scratch, timeout=180)
        assert result.ok, f"build failed: {result.stderr[-800:]}\n--- script ---\n{script}"
        assert result.stl_path and result.stl_path.stat().st_size > 0
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def test_photo_then_text_refine_still_builds():
    fixture = Path(__file__).parent / "fixtures" / "part.png"
    if not fixture.exists():
        pytest.skip("no fixture image")
    scratch = Path(tempfile.mkdtemp(prefix="cad-agent-refine-test-", dir=Path.home()))
    try:
        first = brain.generate_from_photo(str(fixture), "each arm about 40mm")
        r1 = runner.run_freecad(first, scratch_base=scratch, timeout=180)
        assert r1.ok, f"first build failed: {r1.stderr[-600:]}\n{first}"
        second = brain.generate_from_photo(
            str(fixture), "make sure every arm has two through-holes", prev_script=first)
        r2 = runner.run_freecad(second, scratch_base=scratch, timeout=180)
        assert r2.ok, f"refine build failed: {r2.stderr[-600:]}\n{second}"
        assert r2.stl_path and r2.stl_path.stat().st_size > 0
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
