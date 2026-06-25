import sys
from cad_agent.runner import run_freecad

def test_collects_stl(tmp_path):
    # plain-python fixture: write out.stl to cwd
    r = run_freecad("open('out.stl','w').write('solid x\\nendsolid x\\n')",
                    cmd=[sys.executable], scratch_base=tmp_path)
    assert r.ok
    assert r.stl_path and r.stl_path.read_text().startswith("solid")

def test_timeout_is_killed(tmp_path):
    r = run_freecad("import time; time.sleep(5)",
                    cmd=[sys.executable], timeout=0.5, scratch_base=tmp_path)
    assert r.timed_out and not r.ok

def test_secrets_are_scrubbed(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "leak-me")
    r = run_freecad(
        "import os; open('out.stl','w').write(os.environ.get('ANTHROPIC_API_KEY','NONE'))",
        cmd=[sys.executable], scratch_base=tmp_path)
    assert r.stl_path.read_text() == "NONE"

def test_failure_when_no_stl(tmp_path):
    r = run_freecad("pass", cmd=[sys.executable], scratch_base=tmp_path)
    assert not r.ok and r.stl_path is None
