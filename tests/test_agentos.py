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
