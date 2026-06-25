import asyncio
import json
import shutil
import uuid
from pathlib import Path
from fastapi import FastAPI, HTTPException, UploadFile, File, Form
from fastapi.responses import HTMLResponse, FileResponse, StreamingResponse
from . import brain, runner

app = FastAPI()
WEB = Path(__file__).parent / "web"

_state: dict = {"prev_script": None, "last_workdir": None}
_events: "asyncio.Queue[dict]" = asyncio.Queue()
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


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return (WEB / "index.html").read_text()


@app.get("/stl")
def stl() -> FileResponse:
    if _state["last_workdir"] is None:
        raise HTTPException(status_code=404, detail="no successful build yet")
    return FileResponse(_state["last_workdir"] / "out.stl", media_type="model/stl")


async def _emit(ev: dict) -> None:
    await _events.put(ev)


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
    if not message and image is None:
        raise HTTPException(status_code=400, detail="describe a part or upload a photo")
    image_path = await _save_photo(image) if image is not None else None

    result = None
    prev_for_this_iter = _state["prev_script"]
    gen_message = message
    for attempt in range(MAX_RETRIES + 1):
        if attempt == 0 and image_path is not None:
            script = await asyncio.to_thread(
                brain.generate_from_photo, str(image_path), message or None)
        else:
            script = await asyncio.to_thread(brain.generate, gen_message, prev_for_this_iter)
        await _emit({"type": "script", "script": script})
        result = await asyncio.to_thread(runner.run_freecad, script)
        if result.ok:
            _state["prev_script"] = script
            _state["last_workdir"] = result.workdir
            _write_handoff(result.workdir)
            await _emit({"type": "model", "stl": "/stl"})
            return {"ok": True}
        prev_for_this_iter = script
        gen_message = (f"{message or 'the photographed part'}\n\nThe previous attempt "
                       f"failed with:\n{result.stderr or 'timeout'}\nFix it.")
    await _emit({"type": "error", "stderr": result.stderr or "timeout"})
    return {"ok": False}


@app.get("/events")
async def events() -> StreamingResponse:
    async def gen():
        while True:
            ev = await _events.get()
            yield f"data: {json.dumps(ev)}\n\n"
    return StreamingResponse(gen(), media_type="text/event-stream")
