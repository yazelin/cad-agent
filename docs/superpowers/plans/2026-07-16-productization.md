# cad-agent 產品化第一輪 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 cad-agent MVP 打磨到「自己天天用+公開展示」的產品等級:進度回饋、參數面板、下載、歷史、質感 UI、README 門面、CI。

**Architecture:** 後端維持 FastAPI 單檔 `server.py` + 新增純函式 `params.py`;SSE 由單 queue 改 per-connection fan-out;參數重建走 server-side 代換、不經 claude。前端拆 `index.html` + `style.css` + `app.js`,vanilla ES module,零 build step。

**Tech Stack:** FastAPI、three.js(CDN importmap)、pytest、GitHub Actions。

## Global Constraints

- 零新增 runtime 依賴(pyproject dependencies 不變);零前端 build step。
- Python >= 3.11。
- `/agentos/build` 行為與回傳格式完全不動。
- UI 文字一律正體中文;全案禁用 emoji。
- README footer 三連結固定:GitHub `https://github.com/yazelin/cad-agent`、Facebook `https://www.facebook.com/yaze.lin.gm`、Buy Me a Coffee `https://buymeacoffee.com/yazelin`。
- 每個 task 完成即 commit(訊息附 `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`)。
- spec:`docs/superpowers/specs/2026-07-16-productization-design.md`。

---

### Task 1: params.py — 參數 parse / substitute

**Files:**
- Create: `cad_agent/params.py`
- Test: `tests/test_params.py`

**Interfaces:**
- Produces: `parse_params(script: str) -> list[dict]`(有序 `[{"name": str, "value": float}]`);`substitute(script: str, new: dict[str, float]) -> str`(non-finite 值 raise `ValueError`)。

- [ ] **Step 1: 寫失敗測試** `tests/test_params.py`:

```python
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
```

- [ ] **Step 2: 跑測試確認失敗** — `pytest tests/test_params.py -q`,預期 import error(模組不存在)。
- [ ] **Step 3: 實作** `cad_agent/params.py`:

```python
import math
import re

# Column-0 UPPERCASE numeric assignment, e.g. `WIDTH = 100` / `HOLE_D = 8.5  # 孔徑`.
# The generation prompts contract that every tunable dimension is such a line.
_PARAM_RE = re.compile(
    r"^(?P<name>[A-Z][A-Z0-9_]*)\s*=\s*(?P<value>-?\d+(?:\.\d+)?)\s*(?P<trail>#.*)?$"
)


def parse_params(script: str) -> list[dict]:
    """Ordered [{name, value}] from column-0 UPPERCASE numeric assignments.

    A name assigned twice keeps its first position, last value (what the
    script actually runs with).
    """
    out: list[dict] = []
    index: dict[str, int] = {}
    for line in script.splitlines():
        m = _PARAM_RE.match(line)
        if not m:
            continue
        name, value = m.group("name"), float(m.group("value"))
        if name in index:
            out[index[name]]["value"] = value
        else:
            index[name] = len(out)
            out.append({"name": name, "value": value})
    return out


def _fmt(v: float) -> str:
    return str(int(v)) if float(v).is_integer() else repr(float(v))


def substitute(script: str, new: dict[str, float]) -> str:
    """Rewrite the numeric literal of matching param lines; keep trailing comments."""
    for v in new.values():
        if not math.isfinite(float(v)):
            raise ValueError("param values must be finite numbers")
    lines = script.splitlines()
    for i, line in enumerate(lines):
        m = _PARAM_RE.match(line)
        if m and m.group("name") in new:
            trail = f"  {m.group('trail')}" if m.group("trail") else ""
            lines[i] = f"{m.group('name')} = {_fmt(new[m.group('name')])}{trail}"
    return "\n".join(lines)
```

- [ ] **Step 4: 跑測試確認全綠** — `pytest tests/test_params.py -q`。
- [ ] **Step 5: Commit** — `feat(params): parse/substitute UPPERCASE dimension variables`。

---

### Task 2: server — SSE fan-out、狀態事件、busy 鎖、model 事件帶 params

**Files:**
- Modify: `cad_agent/server.py`
- Test: `tests/test_server.py`

**Interfaces:**
- Consumes: `params.parse_params`(Task 1)。
- Produces: SSE 事件協定 —
  `{"type":"status","stage":"thinking"|"retry"|"building","attempt":int}`、
  `{"type":"script","script":str}`、
  `{"type":"model","stl":"/stl","params":[{name,value}]}`(Task 4 再加 `history_id`/`label`)、
  `{"type":"error","stderr":str}`;
  module state `_clients: set[asyncio.Queue]`、`_busy: bool`;`/build` busy 時回 409。

- [ ] **Step 1: 失敗測試**(加入 `tests/test_server.py`;fixture 同步重設新 state):

fixture 改為:

```python
@pytest.fixture(autouse=True)
def _reset_state():
    srv._state.clear()
    srv._state.update({"prev_script": None, "last_workdir": None, "last_image": None})
    srv._busy = False
    srv._clients.clear()
    yield
```

新測試:

```python
import asyncio

def test_emit_fans_out_to_all_clients():
    async def go():
        q1, q2 = asyncio.Queue(), asyncio.Queue()
        srv._clients.update({q1, q2})
        await srv._emit({"type": "x"})
        assert q1.get_nowait() == {"type": "x"}
        assert q2.get_nowait() == {"type": "x"}
    asyncio.run(go())

def _ok_result(tmp_path):
    stl = tmp_path / "out.stl"; stl.write_text("solid x\nendsolid x\n")
    return RunResult(True, False, "", "", stl, None, tmp_path)

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
```

- [ ] **Step 2: 跑測試確認失敗** — `pytest tests/test_server.py -q`(`_busy`/`_clients` 不存在、事件序不符)。
- [ ] **Step 3: 實作**(`server.py`):
  - module state 改:移除 `_events`,新增 `_clients: "set[asyncio.Queue[dict]]" = set()`、`_busy = False`,並 `from . import brain, params, runner`。
  - `_emit` 改 fan-out:

```python
async def _emit(ev: dict) -> None:
    for q in list(_clients):
        await q.put(ev)
```

  - `/events` 每連線一個 queue,斷線移除:

```python
@app.get("/events")
async def events() -> StreamingResponse:
    q: "asyncio.Queue[dict]" = asyncio.Queue()
    _clients.add(q)

    async def gen():
        try:
            while True:
                ev = await q.get()
                yield f"data: {json.dumps(ev)}\n\n"
        finally:
            _clients.discard(q)

    return StreamingResponse(gen(), media_type="text/event-stream")
```

  - `/build`:進門先 `global _busy` + busy 檢查回 409(在參數驗證之前),驗證通過後 `_busy = True`,主體包 `try/finally` 復位;迴圈內發狀態事件;model 事件帶 params:

```python
@app.post("/build")
async def build(message: str = Form(""), image: UploadFile | None = File(None)) -> dict:
    global _busy
    if _busy:
        raise HTTPException(status_code=409, detail="build in progress")
    if not message.strip() and image is None and _state["last_image"] is None:
        raise HTTPException(status_code=400, detail="describe a part or upload a photo")
    if image is not None:
        new_path = str(await _save_photo(image))
        _clear_last_image()
        _state["last_image"] = new_path
        _state["prev_script"] = None
    _busy = True
    try:
        active_image = _state["last_image"]
        if active_image and not Path(active_image).exists():
            active_image = None
            _state["last_image"] = None
        result = None
        prev_for_this_iter = _state["prev_script"]
        gen_hint = message or None
        for attempt in range(MAX_RETRIES + 1):
            await _emit({"type": "status",
                         "stage": "retry" if attempt else "thinking",
                         "attempt": attempt})
            if active_image is not None:
                script = await asyncio.to_thread(
                    brain.generate_from_photo, active_image, gen_hint, prev_for_this_iter)
            else:
                script = await asyncio.to_thread(
                    brain.generate, gen_hint or message, prev_for_this_iter)
            await _emit({"type": "script", "script": script})
            await _emit({"type": "status", "stage": "building", "attempt": attempt})
            result = await asyncio.to_thread(runner.run_freecad, script)
            if result.ok:
                _state["prev_script"] = script
                _state["last_workdir"] = result.workdir
                _write_handoff(result.workdir)
                await _emit({"type": "model", "stl": "/stl",
                             "params": params.parse_params(script)})
                return {"ok": True}
            prev_for_this_iter = script
            gen_hint = (f"{message or 'the part'}\n\nThe previous attempt failed with:\n"
                        f"{result.stderr or 'timeout'}\nFix it.")
        await _emit({"type": "error", "stderr": result.stderr or "timeout"})
        return {"ok": False}
    finally:
        _busy = False
```

- [ ] **Step 4: 跑測試** — `pytest tests/test_server.py tests/test_agentos.py -q` 全綠(既有測試不得壞)。
- [ ] **Step 5: Commit** — `feat(server): status events, per-client SSE fan-out, busy lock (409)`。

---

### Task 3: /step 下載端點 + 下載檔名

**Files:**
- Modify: `cad_agent/server.py`(`/stl` 加 filename、新增 `/step`)
- Test: `tests/test_server.py`

**Interfaces:**
- Produces: `GET /step` → 200 FileResponse(`filename="cad-agent.step"`)/404;`GET /stl` 多 `filename="cad-agent.stl"`。

- [ ] **Step 1: 失敗測試**:

```python
def test_step_404_when_no_build():
    srv._state["last_workdir"] = None
    assert TestClient(app).get("/step").status_code == 404

def test_step_404_when_step_file_missing(tmp_path):
    srv._state["last_workdir"] = tmp_path
    assert TestClient(app).get("/step").status_code == 404

def test_step_serves_file_with_download_filename(tmp_path):
    (tmp_path / "out.step").write_text("ISO-10303-21;")
    srv._state["last_workdir"] = tmp_path
    r = TestClient(app).get("/step")
    assert r.status_code == 200
    assert "cad-agent.step" in r.headers["content-disposition"]

def test_stl_download_filename(tmp_path):
    (tmp_path / "out.stl").write_text("solid x\nendsolid x\n")
    srv._state["last_workdir"] = tmp_path
    assert "cad-agent.stl" in TestClient(app).get("/stl").headers["content-disposition"]
```

- [ ] **Step 2: 確認失敗** — `pytest tests/test_server.py -q -k step_or_stl`(用 `-k "step or stl"`)。
- [ ] **Step 3: 實作**:

```python
@app.get("/stl")
def stl() -> FileResponse:
    if _state["last_workdir"] is None:
        raise HTTPException(status_code=404, detail="no successful build yet")
    return FileResponse(_state["last_workdir"] / "out.stl",
                        media_type="model/stl", filename="cad-agent.stl")


@app.get("/step")
def step() -> FileResponse:
    if _state["last_workdir"] is None:
        raise HTTPException(status_code=404, detail="no successful build yet")
    path = _state["last_workdir"] / "out.step"
    if not path.exists():
        raise HTTPException(status_code=404, detail="no step file for this build")
    return FileResponse(path, media_type="application/step", filename="cad-agent.step")
```

- [ ] **Step 4: 跑測試全綠**;`Content-Disposition: attachment` 不影響 three.js 的 fetch 載入(僅提示瀏覽器存檔名)。
- [ ] **Step 5: Commit** — `feat(server): GET /step + download filenames`。

---

### Task 4: 建置歷史

**Files:**
- Modify: `cad_agent/server.py`
- Test: `tests/test_server.py`

**Interfaces:**
- Produces: `_history: list[dict]`(`{id:int, script:str, workdir:str, label:str, ts:float}`);`_record_history(script, workdir, label) -> dict`;`GET /history` → `[{id,label,ts}]`;`GET /history/{hid}/stl`;`POST /history/{hid}/restore`;model 事件加 `history_id`、`label`。

- [ ] **Step 1: 失敗測試**(fixture 再加 `srv._history.clear()`):

```python
def test_history_records_each_successful_build(tmp_path, monkeypatch):
    ok = _ok_result(tmp_path)
    monkeypatch.setattr(srv.brain, "generate", lambda m, p=None: "W = 1")
    monkeypatch.setattr(srv.runner, "run_freecad", lambda s, **k: ok)
    monkeypatch.setattr(srv, "_write_handoff", lambda wd: None)
    c = TestClient(app)
    c.post("/build", data={"message": "第一版底板"})
    c.post("/build", data={"message": "加四個孔"})
    h = c.get("/history").json()
    assert [(e["id"], e["label"]) for e in h] == [(1, "第一版底板"), (2, "加四個孔")]
    assert all(set(e) == {"id", "label", "ts"} for e in h)

def test_history_restore_switches_state_and_emits_model(tmp_path, monkeypatch):
    ok = _ok_result(tmp_path)
    scripts = iter(["A = 1", "A = 2"])
    monkeypatch.setattr(srv.brain, "generate", lambda m, p=None: next(scripts))
    monkeypatch.setattr(srv.runner, "run_freecad", lambda s, **k: ok)
    monkeypatch.setattr(srv, "_write_handoff", lambda wd: None)
    c = TestClient(app)
    c.post("/build", data={"message": "v1"})
    c.post("/build", data={"message": "v2"})
    events = []
    async def record(ev): events.append(ev)
    monkeypatch.setattr(srv, "_emit", record)
    assert c.post("/history/1/restore").json() == {"ok": True}
    assert srv._state["prev_script"] == "A = 1"
    assert events[-1]["type"] == "model" and events[-1]["history_id"] == 1

def test_history_stl_serves_that_versions_file(tmp_path, monkeypatch):
    ok = _ok_result(tmp_path)
    monkeypatch.setattr(srv.brain, "generate", lambda m, p=None: "W = 1")
    monkeypatch.setattr(srv.runner, "run_freecad", lambda s, **k: ok)
    monkeypatch.setattr(srv, "_write_handoff", lambda wd: None)
    c = TestClient(app)
    c.post("/build", data={"message": "v1"})
    assert c.get("/history/1/stl").status_code == 200
    assert c.get("/history/99/stl").status_code == 404

def test_history_restore_unknown_id_404():
    assert TestClient(app).post("/history/7/restore").status_code == 404
```

- [ ] **Step 2: 確認失敗** — `pytest tests/test_server.py -q -k history`。
- [ ] **Step 3: 實作**:top 加 `import time`;module state 加 `_history: list[dict] = []`;

```python
def _record_history(script: str, workdir: Path, label: str) -> dict:
    entry = {"id": len(_history) + 1, "script": script, "workdir": str(workdir),
             "label": (label.strip() or "未命名")[:60], "ts": time.time()}
    _history.append(entry)
    return entry


def _history_entry(hid: int) -> dict:
    for e in _history:
        if e["id"] == hid:
            return e
    raise HTTPException(status_code=404, detail="no such history entry")


@app.get("/history")
def history() -> list[dict]:
    return [{"id": e["id"], "label": e["label"], "ts": e["ts"]} for e in _history]


@app.get("/history/{hid}/stl")
def history_stl(hid: int) -> FileResponse:
    e = _history_entry(hid)
    path = Path(e["workdir"]) / "out.stl"
    if not path.exists():
        raise HTTPException(status_code=404, detail="stl file no longer on disk")
    return FileResponse(path, media_type="model/stl", filename="cad-agent.stl")


@app.post("/history/{hid}/restore")
async def history_restore(hid: int) -> dict:
    e = _history_entry(hid)
    _state["prev_script"] = e["script"]
    _state["last_workdir"] = Path(e["workdir"])
    await _emit({"type": "model", "stl": "/stl",
                 "params": params.parse_params(e["script"]),
                 "history_id": e["id"], "label": e["label"]})
    return {"ok": True}
```

`/build` 成功分支改為:

```python
            if result.ok:
                _state["prev_script"] = script
                _state["last_workdir"] = result.workdir
                _write_handoff(result.workdir)
                entry = _record_history(
                    script, result.workdir,
                    message or ("照片建模" if active_image else "建置"))
                await _emit({"type": "model", "stl": "/stl",
                             "params": params.parse_params(script),
                             "history_id": entry["id"], "label": entry["label"]})
                return {"ok": True}
```

歷史 in-memory、重啟即清空(個人工具的刻意 ceiling;要持久化時改存 scratch JSON)。`/reset` 不清歷史(回復靠它)。Task 2 的事件序測試斷言 model 事件 `params` 欄位不受影響(用 `events[-1]["params"]`,不是全等比對)。

- [ ] **Step 4: 跑測試全綠** — `pytest tests/test_server.py -q`。
- [ ] **Step 5: Commit** — `feat(server): in-memory build history + restore/stl endpoints`。

---

### Task 5: /rebuild(參數直改重建)+ /session 擴充

**Files:**
- Modify: `cad_agent/server.py`
- Test: `tests/test_server.py`

**Interfaces:**
- Produces: `POST /rebuild {"params": {NAME: number}}` → busy 409 / 無腳本 400 / 成功 `{"ok": true}`(更新 prev_script、發 model 事件、記歷史,label 形如 `參數 HOLE_D=6.5`);失敗 `{"ok": false}` 且 prev_script 不變。`GET /session` → `{"image", "params", "has_model"}`。

- [ ] **Step 1: 失敗測試**:

```python
def test_rebuild_substitutes_and_runs_without_claude(tmp_path, monkeypatch):
    ok = _ok_result(tmp_path)
    seen = {}
    def run(script, **k):
        seen["script"] = script
        return ok
    monkeypatch.setattr(srv.runner, "run_freecad", run)
    monkeypatch.setattr(srv.brain, "generate",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("claude must not be called")))
    monkeypatch.setattr(srv, "_write_handoff", lambda wd: None)
    srv._state["prev_script"] = "W = 10\nH = 5"
    r = TestClient(app).post("/rebuild", json={"params": {"W": 12.5}})
    assert r.json() == {"ok": True}
    assert seen["script"].splitlines()[0] == "W = 12.5"
    assert srv._state["prev_script"].splitlines()[0] == "W = 12.5"
    h = TestClient(app).get("/history").json()
    assert h and h[-1]["label"] == "參數 W=12.5"

def test_rebuild_failure_keeps_old_script(tmp_path, monkeypatch):
    monkeypatch.setattr(srv.runner, "run_freecad",
        lambda s, **k: RunResult(False, False, "", "boom", None, None, tmp_path))
    srv._state["prev_script"] = "W = 10"
    r = TestClient(app).post("/rebuild", json={"params": {"W": 99}})
    assert r.json() == {"ok": False}
    assert srv._state["prev_script"] == "W = 10"

def test_rebuild_400_without_script():
    assert TestClient(app).post("/rebuild", json={"params": {"W": 1}}).status_code == 400

def test_rebuild_409_while_busy():
    srv._state["prev_script"] = "W = 1"
    srv._busy = True
    assert TestClient(app).post("/rebuild", json={"params": {"W": 2}}).status_code == 409

def test_session_reports_params_and_model_flag(tmp_path):
    srv._state["prev_script"] = "W = 10"
    srv._state["last_workdir"] = tmp_path
    s = TestClient(app).get("/session").json()
    assert s["params"] == [{"name": "W", "value": 10.0}]
    assert s["has_model"] is True and s["image"] is None
```

並修改既有 `test_session_reports_image_name` 的兩個全等斷言為欄位斷言:`r.json()["image"] == None` / `== "photo.png"`。

- [ ] **Step 2: 確認失敗** — `pytest tests/test_server.py -q -k "rebuild or session"`。
- [ ] **Step 3: 實作**:

```python
class RebuildReq(BaseModel):
    params: dict[str, float]


@app.post("/rebuild")
async def rebuild(req: RebuildReq) -> dict:
    global _busy
    if _busy:
        raise HTTPException(status_code=409, detail="build in progress")
    if not _state["prev_script"]:
        raise HTTPException(status_code=400, detail="no script to rebuild yet")
    try:
        script = params.substitute(_state["prev_script"], req.params)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    _busy = True
    try:
        await _emit({"type": "status", "stage": "building", "attempt": 0})
        result = await asyncio.to_thread(runner.run_freecad, script)
        if result.ok:
            _state["prev_script"] = script
            _state["last_workdir"] = result.workdir
            _write_handoff(result.workdir)
            label = "參數 " + ", ".join(
                f"{k}={v:g}" for k, v in sorted(req.params.items()))
            entry = _record_history(script, result.workdir, label)
            await _emit({"type": "model", "stl": "/stl",
                         "params": params.parse_params(script),
                         "history_id": entry["id"], "label": entry["label"]})
            return {"ok": True}
        await _emit({"type": "error", "stderr": result.stderr or "timeout"})
        return {"ok": False}
    finally:
        _busy = False
```

`/session` 改:

```python
@app.get("/session")
def session() -> dict:
    img = _state.get("last_image")
    script = _state.get("prev_script")
    return {"image": Path(img).name if img else None,
            "params": params.parse_params(script) if script else [],
            "has_model": _state.get("last_workdir") is not None}
```

- [ ] **Step 4: 跑測試全綠** — `pytest tests/ -q --ignore=tests/test_acceptance.py`。
- [ ] **Step 5: Commit** — `feat(server): POST /rebuild param-only rebuild + richer /session`。

---

### Task 6: 前端重造(拆檔 + 深色工程主控台)

**Files:**
- Modify: `cad_agent/server.py`(mount 靜態目錄,一行)
- Rewrite: `cad_agent/web/index.html`
- Create: `cad_agent/web/style.css`、`cad_agent/web/app.js`
- Test: `tests/test_server.py`(靜態檔可取得)

**Interfaces:**
- Consumes: Task 2-5 的全部端點與 SSE 事件協定。
- Produces: 使用者可見的完整 UI。

- [ ] **Step 1: 失敗測試**:

```python
def test_web_static_served():
    assert TestClient(app).get("/web/style.css").status_code == 200
    assert TestClient(app).get("/web/app.js").status_code == 200
```

- [ ] **Step 2: server mount**(`server.py`,`app = FastAPI()` 之後):

```python
from fastapi.staticfiles import StaticFiles
app.mount("/web", StaticFiles(directory=WEB), name="web")
```

- [ ] **Step 3: 全新 `index.html`**(骨架;正體中文、無 emoji、favicon 為內嵌 SVG 立方體):

```html
<!doctype html>
<html lang="zh-Hant">
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>cad-agent — 用一句話建 3D 零件</title>
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'><path fill='none' stroke='%234db8ff' stroke-width='1.6' d='M12 2 21 7v10l-9 5-9-5V7z M12 2v20 M3 7l9 5 9-5'/></svg>">
<link rel="stylesheet" href="/web/style.css">
<body>
<main id="view"></main>
<aside id="side">
  <header id="brand">
    <span id="brand-name">cad-agent</span>
    <span id="status" class="idle">待命</span>
  </header>
  <section id="log" aria-live="polite"></section>
  <div id="examples">
    <div class="hint">描述一個零件,AI 會寫參數化 FreeCAD 腳本並即時建給你看。試試:</div>
    <button class="chip">M8 六角螺帽</button>
    <button class="chip">100x60x5 mm 四孔安裝板</button>
    <button class="chip">L 型支架,兩臂各兩孔</button>
  </div>
  <section id="params" hidden>
    <div class="sec-head">參數<button id="rebuild" class="btn small" disabled>重建</button></div>
    <div id="param-rows"></div>
  </section>
  <section id="history" hidden>
    <div class="sec-head">歷史</div>
    <div id="history-rows"></div>
  </section>
  <div id="actions">
    <a id="dl-stl" class="btn" href="/stl" download hidden>下載 STL</a>
    <a id="dl-step" class="btn" href="/step" download hidden>下載 STEP</a>
    <button id="to-render" class="btn" hidden title="用 render-studio 渲染最近建好的模型">送去渲染</button>
  </div>
  <div id="photoline" hidden></div>
  <div id="composer">
    <label class="btn" id="photo-btn" title="拍照/上傳實物照,AI 逆向建模">照片<input type="file" id="photo" accept="image/*" hidden></label>
    <input id="msg" placeholder="描述零件;或上傳照片後補充尺寸備註" autocomplete="off">
    <button id="go" class="btn primary">建</button>
    <button id="reset" class="btn" title="清除照片與目前模型,從頭開始">重設</button>
  </div>
  <footer id="links">
    <a href="https://github.com/yazelin/cad-agent" target="_blank" rel="noopener">GitHub</a>
    <a href="https://www.facebook.com/yaze.lin.gm" target="_blank" rel="noopener">Facebook</a>
    <a href="https://buymeacoffee.com/yazelin" target="_blank" rel="noopener">Buy Me a Coffee</a>
  </footer>
</aside>
<script type="importmap">
{ "imports": {
  "three": "https://unpkg.com/three@0.160.0/build/three.module.js",
  "three/addons/": "https://unpkg.com/three@0.160.0/examples/jsm/"
}}
</script>
<script type="module" src="/web/app.js"></script>
```

- [ ] **Step 4: `style.css`**(深色工程主控台;tokens 起點如下,實作時以 frontend-design skill + 截圖迭代微調):

```css
:root {
  --bg: #101318; --panel: #161a21; --line: #262c37;
  --text: #dfe4ec; --muted: #8a93a6;
  --accent: #4db8ff; --ok: #5dd39e; --err: #ff7a76;
}
* { box-sizing: border-box; }
body { margin: 0; display: flex; height: 100vh; background: var(--bg);
       color: var(--text); font: 14px/1.5 system-ui, "Noto Sans TC", sans-serif; }
#view { flex: 1; min-width: 0; }
#side { width: 400px; display: flex; flex-direction: column;
        background: var(--panel); border-left: 1px solid var(--line); }
#brand { display: flex; align-items: center; justify-content: space-between;
         padding: 12px 14px; border-bottom: 1px solid var(--line); }
#brand-name { font-weight: 600; letter-spacing: .04em; }
#status { font-size: 12px; padding: 2px 10px; border-radius: 999px;
          border: 1px solid var(--line); color: var(--muted); }
#status.busy { color: var(--accent); border-color: var(--accent);
               animation: pulse 1.2s ease-in-out infinite; }
#status.ok { color: var(--ok); border-color: var(--ok); }
#status.err { color: var(--err); border-color: var(--err); }
@keyframes pulse { 50% { opacity: .45; } }
#log { flex: 1; overflow-y: auto; padding: 10px 14px; }
.entry { margin: 6px 0; white-space: pre-wrap; word-break: break-word; }
.entry.u { border-left: 2px solid var(--accent); padding-left: 8px; }
.entry.s { color: var(--muted); font-size: 13px; }
.entry.e { color: var(--err); font: 12px/1.4 ui-monospace, monospace;
           background: #2a1d1d; border-radius: 6px; padding: 8px; }
details.script { font-size: 12px; color: var(--muted); margin: 6px 0; }
details.script pre { font: 11px/1.45 ui-monospace, monospace; background: #0d1014;
                     border: 1px solid var(--line); border-radius: 6px;
                     padding: 8px; overflow-x: auto; max-height: 240px; }
#examples { padding: 0 14px 10px; }
#examples .hint { color: var(--muted); font-size: 13px; margin-bottom: 8px; }
.chip { display: inline-block; margin: 0 6px 6px 0; padding: 4px 12px;
        background: transparent; border: 1px solid var(--line); color: var(--text);
        border-radius: 999px; cursor: pointer; font-size: 13px; }
.chip:hover { border-color: var(--accent); color: var(--accent); }
section#params, section#history { border-top: 1px solid var(--line); padding: 10px 14px; }
.sec-head { display: flex; justify-content: space-between; align-items: center;
            font-size: 12px; letter-spacing: .08em; color: var(--muted);
            text-transform: uppercase; margin-bottom: 8px; }
.param { display: grid; grid-template-columns: 1fr 110px; gap: 8px;
         align-items: center; margin: 4px 0; font: 12px/1.4 ui-monospace, monospace; }
.param input { background: var(--bg); border: 1px solid var(--line); color: var(--text);
               border-radius: 6px; padding: 4px 8px; font: inherit; text-align: right; }
.param input:focus { outline: none; border-color: var(--accent); }
.hrow { display: flex; justify-content: space-between; align-items: center;
        gap: 8px; margin: 4px 0; font-size: 13px; color: var(--muted); }
.hrow span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
#actions { display: flex; gap: 8px; padding: 10px 14px; border-top: 1px solid var(--line); }
#photoline { padding: 0 14px 6px; color: var(--muted); font-size: 12px; }
#composer { display: flex; gap: 8px; padding: 0 14px 12px; }
#msg { flex: 1; min-width: 0; background: var(--bg); border: 1px solid var(--line);
       color: var(--text); border-radius: 8px; padding: 8px 10px; }
#msg:focus { outline: none; border-color: var(--accent); }
.btn { background: transparent; border: 1px solid var(--line); color: var(--text);
       border-radius: 8px; padding: 8px 12px; cursor: pointer; font-size: 13px;
       text-decoration: none; text-align: center; }
.btn:hover { border-color: var(--accent); color: var(--accent); }
.btn.primary { background: var(--accent); border-color: var(--accent); color: #0b1016;
               font-weight: 600; }
.btn.primary:disabled { opacity: .5; cursor: default; }
.btn.small { padding: 2px 10px; font-size: 12px; }
.btn:disabled { opacity: .5; cursor: default; }
#links { display: flex; gap: 14px; padding: 8px 14px 12px; border-top: 1px solid var(--line); }
#links a { color: var(--muted); font-size: 12px; text-decoration: none; }
#links a:hover { color: var(--accent); }
```

- [ ] **Step 5: `app.js`**(完整行為;SSE 驅動狀態,fetch 生命週期鎖按鈕):

```js
import * as THREE from "three";
import { STLLoader } from "three/addons/loaders/STLLoader.js";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";

const $ = (id) => document.getElementById(id);

// ---------- three.js viewer ----------
const view = $("view");
const scene = new THREE.Scene();
scene.background = new THREE.Color(0x101318);
const cam = new THREE.PerspectiveCamera(50, 1, 0.1, 5000);
cam.position.set(120, 120, 120);
const renderer = new THREE.WebGLRenderer({ antialias: true });
view.appendChild(renderer.domElement);
const controls = new OrbitControls(cam, renderer.domElement);
controls.enableDamping = true;
scene.add(new THREE.HemisphereLight(0xffffff, 0x30343c, 1.1));
const key = new THREE.DirectionalLight(0xffffff, 1.2);
key.position.set(1, 1.2, 0.8); scene.add(key);
const fill = new THREE.DirectionalLight(0x88aaff, 0.35);
fill.position.set(-1, 0.6, -1); scene.add(fill);
scene.add(new THREE.GridHelper(200, 20, 0x2a3140, 0x1d222c));
function size() {
  cam.aspect = view.clientWidth / view.clientHeight;
  cam.updateProjectionMatrix();
  renderer.setSize(view.clientWidth, view.clientHeight);
}
window.addEventListener("resize", size);
size();
(function loop() { requestAnimationFrame(loop); controls.update(); renderer.render(scene, cam); })();

let mesh = null;
const loader = new STLLoader();
function loadStl(url) {
  loader.load(url + "?t=" + Date.now(), (geo) => {
    clearModel();
    geo.center();
    mesh = new THREE.Mesh(geo, new THREE.MeshStandardMaterial({
      color: 0x9fb6d4, metalness: 0.15, roughness: 0.5 }));
    scene.add(mesh);
  }, undefined, () => {});
}
function clearModel() {
  if (mesh) { scene.remove(mesh); mesh.geometry.dispose(); mesh = null; }
}

// ---------- panel helpers ----------
const log = $("log");
function line(cls, text) {
  const div = document.createElement("div");
  div.className = "entry " + cls;
  div.textContent = text;
  log.appendChild(div);
  log.scrollTop = log.scrollHeight;
  $("examples").hidden = true;
}
function scriptBlock(script) {
  const d = document.createElement("details");
  d.className = "script";
  const s = document.createElement("summary");
  s.textContent = "AI 腳本(點開檢視)";
  const pre = document.createElement("pre");
  pre.textContent = script;
  d.append(s, pre);
  log.appendChild(d);
  log.scrollTop = log.scrollHeight;
}
function setStatus(cls, text) {
  $("status").className = cls;
  $("status").textContent = text;
}
function showActions(on) {
  for (const id of ["dl-stl", "dl-step", "to-render"]) $(id).hidden = !on;
}

// ---------- SSE ----------
const es = new EventSource("/events");
es.onmessage = (e) => {
  const ev = JSON.parse(e.data);
  if (ev.type === "status") {
    if (ev.stage === "thinking") { setStatus("busy", "AI 寫腳本中"); line("s", "AI 寫腳本中…"); }
    else if (ev.stage === "retry") { setStatus("busy", "自我修復中"); line("s", `建置失敗,AI 自我修復中(第 ${ev.attempt} 次)…`); }
    else if (ev.stage === "building") { setStatus("busy", "FreeCAD 建置中"); line("s", "FreeCAD 建置中…"); }
  } else if (ev.type === "script") {
    scriptBlock(ev.script);
  } else if (ev.type === "model") {
    loadStl(ev.stl);
    renderParams(ev.params || []);
    setStatus("ok", "完成");
    if (ev.label) line("s", "模型完成:" + ev.label);
    showActions(true);
    refreshHistory();
  } else if (ev.type === "error") {
    setStatus("err", "失敗");
    line("e", "建置失敗:\n" + (ev.stderr || "").slice(-800));
  }
};

// ---------- session / build ----------
let building = false;
let sessionHasPhoto = false;
function setBusyUI(on) {
  building = on;
  $("go").disabled = on;
  updateRebuildState();
}
async function refreshSession() {
  try {
    const s = await (await fetch("/session")).json();
    sessionHasPhoto = !!s.image;
    $("photoline").hidden = !s.image;
    if (s.image) $("photoline").textContent = "基準照片:" + s.image + "(重設可清除)";
    return s;
  } catch { return null; }
}
async function send() {
  if (building) return;
  const msg = $("msg").value.trim();
  const photo = $("photo").files[0];
  if (!msg && !photo && !sessionHasPhoto) { line("s", "先描述零件,或上傳一張照片。"); return; }
  setBusyUI(true);
  line("u", photo ? "[照片] " + msg : msg);
  $("msg").value = "";
  const fd = new FormData();
  fd.append("message", msg);
  if (photo) fd.append("image", photo);
  try {
    const r = await fetch("/build", { method: "POST", body: fd });
    if (r.status === 409) line("s", "上一個建置還在跑,等它結束再送。");
    else if (!r.ok) { setStatus("err", "失敗"); line("e", "請求失敗:HTTP " + r.status); }
    else { $("photo").value = ""; $("photo-name") && ($("photo-name").textContent = ""); }
  } catch (err) {
    setStatus("err", "失敗"); line("e", "連線失敗:" + err);
  } finally {
    setBusyUI(false);
    refreshSession();
  }
}
$("go").addEventListener("click", send);
$("msg").addEventListener("keydown", (e) => { if (e.key === "Enter") send(); });

// ---------- params ----------
let baseline = {};
function renderParams(list) {
  const box = $("param-rows");
  box.textContent = "";
  baseline = {};
  for (const p of list) {
    baseline[p.name] = p.value;
    const row = document.createElement("label");
    row.className = "param";
    const name = document.createElement("span");
    name.textContent = p.name;
    const input = document.createElement("input");
    input.type = "number"; input.step = "any"; input.value = p.value;
    input.dataset.name = p.name;
    input.addEventListener("input", updateRebuildState);
    row.append(name, input);
    box.appendChild(row);
  }
  $("params").hidden = list.length === 0;
  updateRebuildState();
}
function changedParams() {
  const out = {};
  for (const input of $("param-rows").querySelectorAll("input")) {
    const v = parseFloat(input.value);
    if (Number.isFinite(v) && v !== baseline[input.dataset.name]) out[input.dataset.name] = v;
  }
  return out;
}
function updateRebuildState() {
  $("rebuild").disabled = building || Object.keys(changedParams()).length === 0;
}
$("rebuild").addEventListener("click", async () => {
  const changed = changedParams();
  if (building || !Object.keys(changed).length) return;
  setBusyUI(true);
  line("u", "調整參數:" + Object.entries(changed).map(([k, v]) => k + "=" + v).join(", "));
  try {
    const r = await fetch("/rebuild", { method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ params: changed }) });
    if (r.status === 409) line("s", "上一個建置還在跑。");
  } catch (err) { line("e", "連線失敗:" + err); }
  finally { setBusyUI(false); }
});

// ---------- history ----------
async function refreshHistory() {
  try {
    const list = await (await fetch("/history")).json();
    const box = $("history-rows");
    box.textContent = "";
    for (const e of [...list].reverse()) {
      const row = document.createElement("div");
      row.className = "hrow";
      const label = document.createElement("span");
      label.textContent = "#" + e.id + " " + e.label;
      label.title = e.label;
      const btn = document.createElement("button");
      btn.className = "btn small";
      btn.textContent = "回復";
      btn.addEventListener("click", () => fetch("/history/" + e.id + "/restore", { method: "POST" }));
      row.append(label, btn);
      box.appendChild(row);
    }
    $("history").hidden = list.length === 0;
  } catch {}
}

// ---------- misc ----------
for (const chip of document.querySelectorAll("#examples .chip")) {
  chip.addEventListener("click", () => { $("msg").value = chip.textContent; $("msg").focus(); });
}
$("photo").addEventListener("change", () => {
  const f = $("photo").files[0];
  $("photo-btn").firstChild.textContent = f ? "照片:" + f.name.slice(0, 12) : "照片";
});
$("reset").addEventListener("click", async () => {
  await fetch("/reset", { method: "POST" });
  $("photo").value = ""; $("msg").value = "";
  $("photo-btn").firstChild.textContent = "照片";
  clearModel();
  renderParams([]);
  showActions(false);
  setStatus("idle", "待命");
  line("s", "已重設,從頭開始。");
  refreshSession();
});
$("to-render").addEventListener("click", () =>
  window.open("http://127.0.0.1:8098/?from=cad-agent", "_blank"));

(async function init() {
  const s = await refreshSession();
  if (s && s.has_model) {
    loadStl("/stl");
    renderParams(s.params || []);
    showActions(true);
  }
  refreshHistory();
})();
```

- [ ] **Step 6: 跑測試**(靜態檔測試綠)+ 啟動 server,chrome-devtools 開 `http://127.0.0.1:8099` 截圖,以 frontend-design skill 迭代視覺(只調 CSS 值,不改結構),空狀態與建置後各留一張確認圖。
- [ ] **Step 7: Commit** — `feat(web): dark console UI — progress, params panel, history, downloads`。

---

### Task 7: CI

**Files:**
- Create: `.github/workflows/ci.yml`

- [ ] **Step 1: 寫 workflow**:

```yaml
name: ci
on:
  push:
    branches: [main]
  pull_request:
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
      - run: pip install -e ".[dev]"
      - run: pytest -v
```

(acceptance 測試在無 FreeCAD/claude 環境整檔 auto-skip,已驗證 `tests/test_acceptance.py:13` 的 skipif。)

- [ ] **Step 2: Commit** — `ci: pytest on push/PR`。CI 綠不綠在 Task 10 開 PR 後驗證。

---

### Task 8: Demo 媒材(真實 session 錄製)

**Files:**
- Create: `docs/media/demo.gif`、`docs/media/hero.png`、`docs/media/photo-to-cad.png`(+ 暫存 frames 不進 git)

- [ ] **Step 1:** 啟動 server(`python -m cad_agent`,背景)。
- [ ] **Step 2:** chrome-devtools 開頁、resize 1440x900;依序真實操作並截圖存 scratchpad:空狀態含範例晶片 → 送出「100x60x5 mm 四孔安裝板」→「AI 寫腳本中」狀態 →「FreeCAD 建置中」→ 模型出現(參數面板+下載鈕可見)→ 改 `HOLE_D` 按重建 → 重建完成 → 歷史區兩筆。hero.png 用模型完成那張。
- [ ] **Step 3:** 上傳 `tests/fixtures/part.png` + 提示語做一次照片逆向,截 `photo-to-cad.png`。
- [ ] **Step 4:** ffmpeg 串 GIF(概念指令,frames.txt 用 concat duration 格式):

```bash
ffmpeg -f concat -safe 0 -i frames.txt -vf \
  "fps=10,scale=960:-1:flags=lanczos,split[a][b];[a]palettegen[p];[b][p]paletteuse" \
  docs/media/demo.gif
```

- [ ] **Step 5:** 檢查 demo.gif < 8 MB、播起來順;Commit — `docs(media): real-session demo gif + screenshots`。

---

### Task 9: README 門面

**Files:**
- Modify: `README.md`

- [ ] **Step 1:** 標題下方插入:一行定位 + demo GIF + 徽章 + 正體中文導讀:

```markdown
# cad-agent

**Describe a part in plain language — or upload a photo — and watch AI build it
into a parametric CAD model, live.**

![demo](docs/media/demo.gif)

[![ci](https://github.com/yazelin/cad-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/yazelin/cad-agent/actions/workflows/ci.yml)
[![license: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
![python](https://img.shields.io/badge/python-3.11%2B-blue)

> 正體中文導讀:用一句話(或一張實物照片)描述零件,AI 寫出參數化 FreeCAD
> 腳本、無頭建置、瀏覽器即時看 3D 結果。尺寸都是腳本頂端的大寫變數——在參數
> 面板直接改數字,幾秒重建,不必再等 AI。可下載 STL/STEP,有建置歷史可回上一
> 版。給自己用的日常工具,也是「AI 寫 CAD」的可跑示範。
```

- [ ] **Step 2:** Photo to CAD 節加 `![photo to CAD](docs/media/photo-to-cad.png)`;Features 節(How it works 前)列六點:進度回饋、參數面板、STL/STEP 下載、歷史、照片逆向、self-repair。
- [ ] **Step 3:** 文末 License 前加推廣 footer:

```markdown
## 作者與支持

- 原始碼 GitHub:<https://github.com/yazelin/cad-agent>
- Facebook:<https://www.facebook.com/yaze.lin.gm>
- Buy Me a Coffee:<https://buymeacoffee.com/yazelin>
```

- [ ] **Step 4:** Commit — `docs(readme): demo gif, badges, zh-TW intro, features, promo footer`。

---

### Task 10: 端到端驗證 + PR

- [ ] **Step 1:** `pytest -v` 全綠(本機含真 FreeCAD+claude 的 acceptance)。
- [ ] **Step 2:** 手動清單(chrome-devtools 走過):真實建置成功、狀態階段依序出現、參數改值秒級重建、STL/STEP 下載檔案開得起來(檔頭正確)、歷史回復舊版、兩個分頁同時收到事件、建置中再送回 409 提示。
- [ ] **Step 3:** push branch、`gh pr create`、`gh pr merge --auto --squash`,等 CI 綠自動合併。
- [ ] **Step 4:** tag `v0.2.0` 留待 yazelin 驗收後再打。
