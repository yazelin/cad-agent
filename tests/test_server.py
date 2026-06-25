from fastapi.testclient import TestClient
from cad_agent.server import app
import cad_agent.server as srv
from cad_agent.runner import RunResult
from pathlib import Path

def test_index_serves_html():
    r = TestClient(app).get("/")
    assert r.status_code == 200
    assert "cad-agent" in r.text

def test_build_success_sets_prev_and_returns_ok(tmp_path, monkeypatch):
    stl = tmp_path / "out.stl"; stl.write_text("solid x\nendsolid x\n")
    monkeypatch.setattr(srv.brain, "generate", lambda msg, prev=None: "L = 1\nshape.exportStl('out.stl')")
    monkeypatch.setattr(srv.runner, "run_freecad",
        lambda script, **k: RunResult(True, False, "", "", stl, None, tmp_path))
    srv._state["prev_script"] = None
    r = TestClient(app).post("/build", json={"message": "a plate"})
    assert r.json() == {"ok": True}
    assert srv._state["prev_script"] == "L = 1\nshape.exportStl('out.stl')"

def test_build_retries_on_failure(tmp_path, monkeypatch):
    calls = {"n": 0}
    def fake_gen(msg, prev=None):
        calls["n"] += 1
        return f"attempt {calls['n']}"
    fail = RunResult(False, False, "", "boom", None, None, tmp_path)
    monkeypatch.setattr(srv.brain, "generate", fake_gen)
    monkeypatch.setattr(srv.runner, "run_freecad", lambda script, **k: fail)
    srv._state["prev_script"] = None
    r = TestClient(app).post("/build", json={"message": "a plate"})
    assert r.json() == {"ok": False}
    assert calls["n"] == 3  # initial + 2 retries
