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

def test_build_writes_render_studio_handoff(tmp_path, monkeypatch):
    stl = tmp_path / "out.stl"; stl.write_text("solid x\nendsolid x\n")
    handoff = tmp_path / "handoff" / "latest.stl"
    monkeypatch.setattr(srv, "HANDOFF_STL", handoff)
    monkeypatch.setattr(srv.brain, "generate", lambda msg, prev=None: "L = 1")
    monkeypatch.setattr(srv.runner, "run_freecad",
        lambda script, **k: RunResult(True, False, "", "", stl, None, tmp_path))
    srv._state["prev_script"] = None
    TestClient(app).post("/build", json={"message": "a plate"})
    assert handoff.exists() and handoff.read_text() == stl.read_text()

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

def test_build_retry_feeds_failed_script_as_prev(tmp_path, monkeypatch):
    """T1: On 2nd and 3rd generate calls the prev_script arg must equal the
    script returned by the immediately preceding (failed) generate call.
    §6 self-repair contract: feed the failed script + stderr back so the AI
    can actually repair it — not None / a stale script from a previous request.
    """
    generated = []  # scripts produced per call
    prev_args = []  # prev_script received per call

    def fake_gen(msg, prev=None):
        prev_args.append(prev)
        script = f"attempt {len(generated) + 1}"
        generated.append(script)
        return script

    fail = RunResult(False, False, "", "boom", None, None, tmp_path)
    monkeypatch.setattr(srv.brain, "generate", fake_gen)
    monkeypatch.setattr(srv.runner, "run_freecad", lambda script, **k: fail)
    srv._state["prev_script"] = None
    r = TestClient(app).post("/build", json={"message": "a plate"})
    assert r.json() == {"ok": False}
    assert len(generated) == 3  # initial + 2 retries
    # 1st call: no prior failed script, prev must be None (or existing state)
    assert prev_args[0] is None
    # 2nd call: prev must be the script from the 1st (failed) call
    assert prev_args[1] == generated[0], (
        f"2nd call got prev={prev_args[1]!r}, want {generated[0]!r}"
    )
    # 3rd call: prev must be the script from the 2nd (failed) call
    assert prev_args[2] == generated[1], (
        f"3rd call got prev={prev_args[2]!r}, want {generated[1]!r}"
    )

def test_stl_returns_404_when_no_build(monkeypatch):
    """I2: GET /stl must return 404 when no successful build has completed."""
    srv._state["last_workdir"] = None
    r = TestClient(app).get("/stl")
    assert r.status_code == 404
