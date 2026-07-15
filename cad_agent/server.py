import asyncio
import json
import shutil
import time
import uuid
from pathlib import Path
from fastapi import FastAPI, HTTPException, UploadFile, File, Form
from fastapi.responses import HTMLResponse, FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from . import brain, params, runner

app = FastAPI()
WEB = Path(__file__).parent / "web"
app.mount("/web", StaticFiles(directory=WEB), name="web")

_state: dict = {"prev_script": None, "last_workdir": None, "last_image": None}
_clients: "set[asyncio.Queue[dict]]" = set()
_busy = False  # ponytail: one build at a time, module-level flag; queue builds if it ever hurts
# ponytail: history is in-memory, gone on restart; persist to scratch JSON if that ever matters
_history: list[dict] = []
MAX_RETRIES = 2
MAX_IMAGE_BYTES = 25 * 1024 * 1024
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp"}

# Shared handoff to the render-studio app: the latest successful STL is copied
# here so render-studio can render it. Non-hidden dir; both apps' servers use it.
HANDOFF_STL = Path.home() / "3d-pipeline" / "latest.stl"


def _write_handoff(workdir: Path) -> None:
    # best-effort: a handoff failure must never fail a build
    try:
        HANDOFF_STL.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(workdir / "out.stl", HANDOFF_STL)
    except OSError:
        pass


def write_descriptor(host: str = "127.0.0.1", port: int = 8099,
                     path: "Path | None" = None) -> None:
    # Service descriptor for AgentOS http-service (json) skills to find this app.
    # Best-effort: a write failure must never crash the server.
    target = path or (Path.home() / ".mori" / "cad-agent.json")
    desc = {"contract_version": 1, "host": host, "port": port,
            "inference_path": "/agentos/build"}
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(desc))
    except OSError:
        pass


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


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return (WEB / "index.html").read_text()


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


@app.post("/reset")
def reset() -> dict:
    _clear_last_image()
    _state["prev_script"] = None
    _state["last_workdir"] = None
    return {"ok": True}


@app.get("/session")
def session() -> dict:
    img = _state.get("last_image")
    script = _state.get("prev_script")
    return {"image": Path(img).name if img else None,
            "params": params.parse_params(script) if script else [],
            "has_model": _state.get("last_workdir") is not None}


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


async def _emit(ev: dict) -> None:
    # fan out to every connected tab; a lone asyncio.Queue would make two
    # EventSource connections steal events from each other
    for q in list(_clients):
        await q.put(ev)


def _clear_last_image() -> None:
    img = _state.get("last_image")
    if img:
        shutil.rmtree(Path(img).parent, ignore_errors=True)
    _state["last_image"] = None


async def _save_photo(image: UploadFile) -> Path:
    ext = Path(image.filename or "photo.png").suffix.lower()
    if ext not in IMAGE_EXTS:
        raise HTTPException(status_code=400, detail="image must be png/jpg/jpeg/webp")
    data = await image.read()
    if len(data) > MAX_IMAGE_BYTES:
        raise HTTPException(status_code=400, detail="image too large (max 25 MB)")
    img_dir = runner.DEFAULT_SCRATCH / ("photo-" + uuid.uuid4().hex[:12])
    img_dir.mkdir(parents=True, exist_ok=True)
    path = img_dir / ("photo" + ext)
    path.write_bytes(data)
    return path


@app.post("/build")
async def build(message: str = Form(""), image: UploadFile | None = File(None)) -> dict:
    global _busy
    if _busy:
        raise HTTPException(status_code=409, detail="build in progress")
    if not message.strip() and image is None and _state["last_image"] is None:
        raise HTTPException(status_code=400, detail="describe a part or upload a photo")
    if image is not None:
        new_path = str(await _save_photo(image))  # validates + writes; raises before any clear
        _clear_last_image()                        # only clear AFTER save succeeds
        _state["last_image"] = new_path
        _state["prev_script"] = None              # fresh part — do not anchor on old script
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
                entry = _record_history(
                    script, result.workdir,
                    message or ("照片建模" if active_image else "建置"))
                await _emit({"type": "model", "stl": "/stl",
                             "params": params.parse_params(script),
                             "history_id": entry["id"], "label": entry["label"]})
                return {"ok": True}
            prev_for_this_iter = script
            gen_hint = (f"{message or 'the part'}\n\nThe previous attempt failed with:\n{result.stderr or 'timeout'}\nFix it.")
        await _emit({"type": "error", "stderr": result.stderr or "timeout"})
        return {"ok": False}
    finally:
        _busy = False


class AgentosBuildReq(BaseModel):
    message: str

@app.post("/agentos/build")
async def agentos_build(req: AgentosBuildReq) -> dict:
    # headless one-shot for AgentOS: generate + build, return JSON. No SSE, no
    # session state, no self-repair (the interactive /build keeps those).
    script = await asyncio.to_thread(brain.generate, req.message, None)
    result = await asyncio.to_thread(runner.run_freecad, script)
    if result.ok:
        return {"ok": True, "stl_path": str(result.stl_path),
                "step_path": str(result.step_path) if result.step_path else None,
                "script": script, "error": None}
    return {"ok": False, "stl_path": None, "step_path": None, "script": script,
            "error": (result.stderr or "build failed")[-500:]}


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
