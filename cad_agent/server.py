import asyncio
import json
import shutil
from pathlib import Path
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, FileResponse, StreamingResponse
from pydantic import BaseModel
from . import brain, runner

app = FastAPI()
WEB = Path(__file__).parent / "web"

_state: dict = {"prev_script": None, "last_workdir": None}
_events: "asyncio.Queue[dict]" = asyncio.Queue()
MAX_RETRIES = 2

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


class BuildReq(BaseModel):
    message: str


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


@app.post("/build")
async def build(req: BuildReq) -> dict:
    message = req.message
    result = None
    # prev_for_this_iter tracks the script to pass to the next generate call.
    # On the first attempt use the session's last successful script; on retries
    # use the script that just failed so the AI can perform §6 self-repair.
    prev_for_this_iter = _state["prev_script"]
    for attempt in range(MAX_RETRIES + 1):
        script = await asyncio.to_thread(brain.generate, message, prev_for_this_iter)
        await _emit({"type": "script", "script": script})
        result = await asyncio.to_thread(runner.run_freecad, script)
        if result.ok:
            _state["prev_script"] = script
            _state["last_workdir"] = result.workdir
            _write_handoff(result.workdir)
            await _emit({"type": "model", "stl": "/stl"})
            return {"ok": True}
        # Feed the failed script back as prev on the next iteration so the AI
        # sees what it produced and can repair it (§6 self-repair contract).
        prev_for_this_iter = script
        message = (f"{req.message}\n\nThe previous attempt failed with:\n"
                   f"{result.stderr or 'timeout'}\nFix it.")
    await _emit({"type": "error", "stderr": result.stderr or "timeout"})
    return {"ok": False}


@app.get("/events")
async def events() -> StreamingResponse:
    async def gen():
        while True:
            ev = await _events.get()
            yield f"data: {json.dumps(ev)}\n\n"
    return StreamingResponse(gen(), media_type="text/event-stream")
