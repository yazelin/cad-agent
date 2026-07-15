import pytest
from cad_agent import params

SCRIPT = """import FreeCAD as App
import Part

WIDTH = 100
HEIGHT = 60.5
HOLE_D = 8  # 孔徑
count = 4
  INDENTED = 9

shape = Part.makeBox(WIDTH, HEIGHT, 5)
shape.exportStl('out.stl')
shape.exportStep('out.step')
"""

def test_parse_finds_column0_uppercase_numeric():
    assert params.parse_params(SCRIPT) == [
        {"name": "WIDTH", "value": 100.0},
        {"name": "HEIGHT", "value": 60.5},
        {"name": "HOLE_D", "value": 8.0},
    ]

def test_parse_duplicate_keeps_first_position_last_value():
    assert params.parse_params("A = 1\nB = 2\nA = 3\n") == [
        {"name": "A", "value": 3.0}, {"name": "B", "value": 2.0}]

def test_substitute_rewrites_value_and_keeps_comment():
    out = params.substitute(SCRIPT, {"HOLE_D": 6.5, "WIDTH": 120})
    assert "WIDTH = 120" in out.splitlines()
    assert "HOLE_D = 6.5  # 孔徑" in out
    assert "HEIGHT = 60.5" in out
    assert "count = 4" in out
    assert params.parse_params(out)[0] == {"name": "WIDTH", "value": 120.0}

def test_substitute_int_formatting():
    assert params.substitute("L = 10\nx=1", {"L": 12.0}).splitlines()[0] == "L = 12"

def test_substitute_rejects_non_finite():
    with pytest.raises(ValueError):
        params.substitute("L = 1", {"L": float("nan")})

def test_substitute_unknown_name_is_noop():
    assert params.substitute("L = 1", {"Z": 5}) == "L = 1"
