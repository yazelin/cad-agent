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

def test_generate_uses_injected_cmd():
    # fake brain: ignores prompt, prints fixed code
    fake = [sys.executable, "-c", "print('L = 1')"]
    assert brain.generate("anything", claude_cmd=fake) == "L = 1"
