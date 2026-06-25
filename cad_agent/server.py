from pathlib import Path
from fastapi import FastAPI
from fastapi.responses import HTMLResponse

app = FastAPI()
WEB = Path(__file__).parent / "web"

@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return (WEB / "index.html").read_text()
