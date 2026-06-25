import asyncio
import json
from pathlib import Path
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, FileResponse, StreamingResponse
from pydantic import BaseModel
from . import brain, runner

app = FastAPI()
WEB = Path(__file__).parent / "web"

_state: dict = {"prev_script": None, "last_workdir": None}
_events: "asyncio.Queue[dict]" = asyncio.Queue()
MAX_RETRIES = 2


class BuildReq(BaseModel):
    message: str


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return (WEB / "index.html").read_text()


@app.get("/stl")
def stl() -> FileResponse:
    return FileResponse(_state["last_workdir"] / "out.stl", media_type="model/stl")


async def _emit(ev: dict) -> None:
    await _events.put(ev)


@app.post("/build")
async def build(req: BuildReq) -> dict:
    message = req.message
    result = None
    for attempt in range(MAX_RETRIES + 1):
        script = await asyncio.to_thread(brain.generate, message, _state["prev_script"])
        await _emit({"type": "script", "script": script})
        result = await asyncio.to_thread(runner.run_freecad, script)
        if result.ok:
            _state["prev_script"] = script
            _state["last_workdir"] = result.workdir
            await _emit({"type": "model", "stl": "/stl"})
            return {"ok": True}
        # feed the error back so the next attempt can self-repair; cap at MAX_RETRIES.
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
