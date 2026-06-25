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
