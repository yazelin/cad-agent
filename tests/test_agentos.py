from fastapi.testclient import TestClient
import cad_agent.server as srv
from cad_agent.server import app
from cad_agent.runner import RunResult

def test_agentos_build_success(tmp_path, monkeypatch):
    stl = tmp_path / "out.stl"; stl.write_text("solid x")
    step = tmp_path / "out.step"; step.write_text("ISO")
    monkeypatch.setattr(srv.brain, "generate", lambda m, p=None: "import Part")
    monkeypatch.setattr(srv.runner, "run_freecad",
        lambda s, **k: RunResult(True, False, "", "", stl, step, tmp_path))
    r = TestClient(app).post("/agentos/build", json={"message": "a 10mm cube"})
    body = r.json()
    assert body["ok"] is True
    assert body["stl_path"] == str(stl) and body["step_path"] == str(step)
    assert body["script"] == "import Part" and body["error"] is None

def test_agentos_build_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(srv.brain, "generate", lambda m, p=None: "bad")
    monkeypatch.setattr(srv.runner, "run_freecad",
        lambda s, **k: RunResult(False, False, "", "boom", None, None, tmp_path))
    r = TestClient(app).post("/agentos/build", json={"message": "x"})
    body = r.json()
    assert body["ok"] is False and body["stl_path"] is None
    assert "boom" in body["error"]

import json
from pathlib import Path

def test_write_descriptor_content(tmp_path):
    p = tmp_path / ".mori" / "cad-agent.json"
    srv.write_descriptor(host="127.0.0.1", port=8099, path=p)
    d = json.loads(p.read_text())
    assert d == {"contract_version": 1, "host": "127.0.0.1", "port": 8099,
                 "inference_path": "/agentos/build"}

def test_write_descriptor_best_effort_on_bad_path(tmp_path):
    # an unwritable path must not raise
    bad = tmp_path / "afile"
    bad.write_text("x")
    srv.write_descriptor(path=bad / "nope" / "cad-agent.json")  # parent is a file
