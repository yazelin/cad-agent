import asyncio
import io
import pytest
from fastapi.testclient import TestClient
from cad_agent.server import app
import cad_agent.server as srv
from cad_agent.runner import RunResult
from pathlib import Path


@pytest.fixture(autouse=True)
def _reset_state():
    srv._state.clear()
    srv._state.update({"prev_script": None, "last_workdir": None, "last_image": None})
    srv._busy = False
    srv._clients.clear()
    yield


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
    r = TestClient(app).post("/build", data={"message": "a plate"})
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
    TestClient(app).post("/build", data={"message": "a plate"})
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
    r = TestClient(app).post("/build", data={"message": "a plate"})
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
    r = TestClient(app).post("/build", data={"message": "a plate"})
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

def _ok_result(tmp_path):
    stl = tmp_path / "out.stl"; stl.write_text("solid x\nendsolid x\n")
    return RunResult(True, False, "", "", stl, None, tmp_path)


def test_emit_fans_out_to_all_clients():
    async def go():
        q1, q2 = asyncio.Queue(), asyncio.Queue()
        srv._clients.update({q1, q2})
        await srv._emit({"type": "x"})
        assert q1.get_nowait() == {"type": "x"}
        assert q2.get_nowait() == {"type": "x"}
    asyncio.run(go())


def test_build_emits_status_script_model_sequence(tmp_path, monkeypatch):
    ok = _ok_result(tmp_path)
    events = []
    async def record(ev): events.append(ev)
    monkeypatch.setattr(srv, "_emit", record)
    monkeypatch.setattr(srv.brain, "generate",
        lambda m, p=None: "W = 10\nshape.exportStl('out.stl')")
    monkeypatch.setattr(srv.runner, "run_freecad", lambda s, **k: ok)
    monkeypatch.setattr(srv, "_write_handoff", lambda wd: None)
    TestClient(app).post("/build", data={"message": "a plate"})
    seq = [(e["type"], e.get("stage")) for e in events]
    assert seq == [("status", "thinking"), ("script", None),
                   ("status", "building"), ("model", None)]
    assert events[-1]["params"] == [{"name": "W", "value": 10.0}]


def test_build_emits_retry_stage(tmp_path, monkeypatch):
    ok = _ok_result(tmp_path)
    calls = {"n": 0}
    def run(script, **k):
        calls["n"] += 1
        return RunResult(False, False, "", "boom", None, None, tmp_path) if calls["n"] == 1 else ok
    events = []
    async def record(ev): events.append(ev)
    monkeypatch.setattr(srv, "_emit", record)
    monkeypatch.setattr(srv.brain, "generate", lambda m, p=None: "W = 1")
    monkeypatch.setattr(srv.runner, "run_freecad", run)
    monkeypatch.setattr(srv, "_write_handoff", lambda wd: None)
    TestClient(app).post("/build", data={"message": "x"})
    assert {"type": "status", "stage": "retry", "attempt": 1} in events


def test_build_returns_409_while_busy():
    srv._busy = True
    assert TestClient(app).post("/build", data={"message": "x"}).status_code == 409


def test_build_clears_busy_after_run(tmp_path, monkeypatch):
    monkeypatch.setattr(srv.brain, "generate", lambda m, p=None: "W = 1")
    monkeypatch.setattr(srv.runner, "run_freecad",
        lambda s, **k: RunResult(False, False, "", "boom", None, None, tmp_path))
    TestClient(app).post("/build", data={"message": "x"})
    assert srv._busy is False


def test_stl_returns_404_when_no_build(monkeypatch):
    """I2: GET /stl must return 404 when no successful build has completed."""
    srv._state["last_workdir"] = None
    r = TestClient(app).get("/stl")
    assert r.status_code == 404

def test_build_from_photo_routes_to_vision(tmp_path, monkeypatch):
    stl = tmp_path / "out.stl"; stl.write_text("solid x\nendsolid x\n")
    seen = {}
    def fake_photo(path, hint=None, prev=None):
        seen["path"] = path; seen["hint"] = hint
        return "import Part"
    monkeypatch.setattr(srv.brain, "generate_from_photo", fake_photo)
    monkeypatch.setattr(srv.runner, "run_freecad",
        lambda script, **k: RunResult(True, False, "", "", stl, None, tmp_path))
    monkeypatch.setattr(srv, "_write_handoff", lambda wd: None)
    srv._state["prev_script"] = None
    r = TestClient(app).post("/build",
        data={"message": "base 90mm"},
        files={"image": ("p.png", io.BytesIO(b"\x89PNG fake"), "image/png")})
    assert r.json() == {"ok": True}
    assert seen["hint"] == "base 90mm" and seen["path"].endswith(".png")

def test_new_image_upload_resets_prev_script(tmp_path, monkeypatch):
    """I2: uploading a new image with stale prev_script must call generate_from_photo
    with prev=None (fresh part, not anchored to the old script)."""
    stl = tmp_path / "out.stl"; stl.write_text("solid x\nendsolid x\n")
    received_prev = {}

    def fake_photo(path, hint=None, prev=None):
        received_prev["prev"] = prev
        return "import Part"

    monkeypatch.setattr(srv.brain, "generate_from_photo", fake_photo)
    monkeypatch.setattr(srv.runner, "run_freecad",
        lambda script, **k: RunResult(True, False, "", "", stl, None, tmp_path))
    monkeypatch.setattr(srv, "_write_handoff", lambda wd: None)
    # Stale state: a previous build left prev_script set
    srv._state["prev_script"] = "OLD"
    r = TestClient(app).post("/build",
        data={"message": "new part"},
        files={"image": ("p.png", io.BytesIO(b"\x89PNG fake"), "image/png")})
    assert r.json() == {"ok": True}
    assert received_prev["prev"] is None, (
        f"expected prev=None on fresh upload, got {received_prev['prev']!r}"
    )


def test_build_requires_message_or_image():
    srv._state["last_image"] = None
    assert TestClient(app).post("/build", data={"message": ""}).status_code == 400


def test_build_rejects_whitespace_only_message():
    # M1: whitespace-only message with no image must return 400
    srv._state["last_image"] = None
    assert TestClient(app).post("/build", data={"message": "   "}).status_code == 400


# NOTE: the former test_build_photo_dir_cleaned_up_after_build was removed —
# the session now RETAINS the photo after a build (cleaned only on replace/reset);
# retention is covered by test_text_edit_after_photo_reuses_remembered_image.


def test_text_edit_after_photo_reuses_remembered_image(tmp_path, monkeypatch):
    stl = tmp_path / "out.stl"; stl.write_text("solid x\nendsolid x\n")
    calls = []
    monkeypatch.setattr(srv.brain, "generate_from_photo",
        lambda image, hint=None, prev=None: (calls.append((image, hint, prev)) or "import Part"))
    monkeypatch.setattr(srv.runner, "run_freecad",
        lambda script, **k: RunResult(True, False, "", "", stl, None, tmp_path))
    monkeypatch.setattr(srv, "_write_handoff", lambda wd: None)
    # simulate a prior photo build having remembered an image + script
    srv._state["last_image"] = str(stl)  # any existing file path
    srv._state["prev_script"] = "OLD"
    # a TEXT-ONLY build (no image field) must still route to vision with the remembered image
    r = TestClient(app).post("/build", data={"message": "add two holes to the upright arm"})
    assert r.json() == {"ok": True}
    assert calls and calls[0][0] == str(stl)            # remembered image used
    assert calls[0][1] == "add two holes to the upright arm"  # hint
    assert calls[0][2] == "OLD"                          # prev_script carried


def test_reset_clears_session(tmp_path, monkeypatch):
    # Use a proper file path so _clear_last_image only removes its own parent dir
    img_dir = tmp_path / "photo-abc"; img_dir.mkdir()
    fake_img = img_dir / "photo.png"; fake_img.write_bytes(b"x")
    srv._state["last_image"] = str(fake_img)  # dir-ish; _clear is best-effort
    srv._state["prev_script"] = "OLD"; srv._state["last_workdir"] = tmp_path
    assert TestClient(app).post("/reset").json() == {"ok": True}
    assert srv._state["last_image"] is None
    assert srv._state["prev_script"] is None
    # after reset, a text build routes to generate (text), not vision
    used = {}
    monkeypatch.setattr(srv.brain, "generate", lambda m, p=None: used.setdefault("g", True) or "import Part")
    monkeypatch.setattr(srv.runner, "run_freecad",
        lambda s, **k: RunResult(False, False, "", "boom", None, None, tmp_path))
    TestClient(app).post("/build", data={"message": "a plate"})
    assert used.get("g") is True


def test_session_reports_image_name(tmp_path):
    srv._state["last_image"] = None
    assert TestClient(app).get("/session").json() == {"image": None}
    p = tmp_path / "photo.png"; p.write_bytes(b"x")
    srv._state["last_image"] = str(p)
    assert TestClient(app).get("/session").json() == {"image": "photo.png"}


def test_missing_remembered_file_falls_back_to_text(tmp_path, monkeypatch):
    srv._state["last_image"] = str(tmp_path / "gone.png")  # does not exist
    srv._state["prev_script"] = None
    used = {}
    monkeypatch.setattr(srv.brain, "generate", lambda m, p=None: used.setdefault("g", True) or "x")
    monkeypatch.setattr(srv.runner, "run_freecad",
        lambda s, **k: RunResult(False, False, "", "boom", None, None, tmp_path))
    TestClient(app).post("/build", data={"message": "a plate"})
    assert used.get("g") is True and srv._state["last_image"] is None
