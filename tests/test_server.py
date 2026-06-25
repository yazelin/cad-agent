from fastapi.testclient import TestClient
from cad_agent.server import app

def test_index_serves_html():
    r = TestClient(app).get("/")
    assert r.status_code == 200
    assert "cad-agent" in r.text
