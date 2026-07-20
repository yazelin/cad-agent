import sys
from cad_agent import brain

def test_build_prompt_new_part():
    p = brain.build_prompt("a 80x40x5 plate", None)
    assert "80x40x5 plate" in p
    assert "UPPERCASE" in p  # system prompt is included

def test_build_prompt_edit_includes_prev():
    p = brain.build_prompt("make it longer", "L = 80.0")
    assert "L = 80.0" in p
    assert "make it longer" in p

def test_build_prompt_edit_carries_feature_discipline():
    # Issue #5: an edit prompt must instruct a feature-level change — touch only
    # the named feature, keep the rest verbatim, and offer the fillet/chamfer API.
    p = brain.build_prompt("add an R3 fillet to the top four edges", "L = 80.0\nW = 40.0")
    assert "L = 80.0" in p
    assert "feature-level edit" in p
    assert "VERBATIM" in p
    assert "makeFillet" in p
    assert "makeChamfer" in p

def test_build_prompt_new_part_has_no_edit_discipline():
    # a fresh part is a full generation, not a feature edit — no "keep verbatim" block
    p = brain.build_prompt("an 80x40x5 plate", None)
    assert "feature-level edit" not in p

def test_preserved_line_ratio_minimal_edit_vs_rewrite():
    prev = ("L = 80\nW = 40\nH = 5\n"
            "shape = Part.makeBox(L, W, H)\nshape.exportStl('out.stl')")
    # minimal feature edit: every base line kept, a fillet step + new param added
    minimal = "FILLET_R = 3\n" + prev.replace(
        "shape.exportStl('out.stl')",
        "part = shape.makeFillet(FILLET_R, shape.Edges)\npart.exportStl('out.stl')")
    assert brain.preserved_line_ratio(prev, minimal) >= 0.8
    # full rewrite: renamed vars / restructured — most base lines gone
    rewrite = "A = 80\nB = 40\nC = 5\nbox = Part.makeBox(A, B, C)\nbox.exportStl('out.stl')"
    assert brain.preserved_line_ratio(prev, rewrite) < 0.5

def test_preserved_line_ratio_empty_prev_is_one():
    assert brain.preserved_line_ratio("", "L = 1") == 1.0

def test_strip_fences():
    assert brain.strip_fences("```python\nL = 1\n```") == "L = 1"
    assert brain.strip_fences("L = 1") == "L = 1"

def test_strip_fences_extracts_block_from_surrounding_prose():
    # real claude output: prose before and after a fenced code block (the
    # em-dash in the trailing prose previously leaked into the script)
    raw = (
        "Here is the script:\n\n"
        "```python\nL = 80.0\nshape.exportStl('out.stl')\n```\n\n"
        "Change L -- it is the length."
    )
    assert brain.strip_fences(raw) == "L = 80.0\nshape.exportStl('out.stl')"

def test_generate_uses_injected_cmd():
    # fake brain: ignores prompt, prints fixed code
    fake = [sys.executable, "-c", "print('L = 1')"]
    assert brain.generate("anything", claude_cmd=fake) == "L = 1"

def test_strip_fences_trims_trailing_prose_without_fence():
    raw = ("import Part\nshape = Part.makeBox(1,1,1)\n"
           "shape.exportStl('out.stl')\nshape.exportStep('out.step')\n\n"
           "skipped: fillets -- add when you have real measurements.")
    out = brain.strip_fences(raw)
    assert out.endswith("shape.exportStep('out.step')")
    assert "skipped" not in out

def test_build_photo_prompt_includes_path_and_hint():
    p = brain.build_photo_prompt("/tmp/part.png", "base is 90mm")
    assert "/tmp/part.png" in p
    assert "base is 90mm" in p
    assert "100 mm" in p  # the no-scale fallback instruction is present

def test_build_photo_prompt_without_hint():
    p = brain.build_photo_prompt("/tmp/part.png", None)
    assert "/tmp/part.png" in p

def test_generate_from_photo_uses_injected_cmd():
    fake = [sys.executable, "-c", "print('import Part')"]
    assert brain.generate_from_photo("/tmp/part.png", claude_cmd=fake) == "import Part"

def test_build_photo_prompt_with_prev_script_includes_all_three():
    p = brain.build_photo_prompt("/tmp/part.png", "add two holes to the upright arm",
                                 prev_script="import Part\nshape = Part.makeBox(1,1,1)")
    assert "/tmp/part.png" in p
    assert "add two holes to the upright arm" in p
    assert "Part.makeBox(1,1,1)" in p          # the current script is included
    assert "Current script" in p

def test_build_photo_prompt_without_prev_script_has_no_current_section():
    p = brain.build_photo_prompt("/tmp/part.png", "arms 40mm", prev_script=None)
    assert "Current script" not in p
    assert "arms 40mm" in p

def test_generate_from_photo_passes_prev_script(monkeypatch):
    captured = {}
    def fake_run(cmd, **kw):
        captured["input"] = kw.get("input")
        class R: stdout = "import Part"
        return R()
    monkeypatch.setattr(brain.subprocess, "run", fake_run)
    out = brain.generate_from_photo("/tmp/p.png", "add holes", prev_script="OLD_SCRIPT")
    assert out == "import Part"
    assert "OLD_SCRIPT" in captured["input"] and "add holes" in captured["input"]


def test_strip_fences_trims_leading_prose_without_fence():
    raw = ("Here's the revised script:\n\n"
           "import Part\nshape = Part.makeBox(1,1,1)\n"
           "shape.exportStl('out.stl')\nshape.exportStep('out.step')")
    out = brain.strip_fences(raw)
    assert out.startswith("import Part")
    assert "Here's" not in out
    assert out.endswith("shape.exportStep('out.step')")
