import io
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
