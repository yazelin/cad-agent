# cad-agent

Describe a part in plain language; an AI writes a parametric FreeCAD script, runs
it headless, and shows the resulting 3D model live in your browser. Say "make it
longer" and it edits a parameter and rebuilds.

This is the modeling stage of a two-tool pipeline: **FreeCAD builds the part**
(manufacturable, parametric), and Blender renders the product image (a planned
follow-up). cad-agent runs fully standalone.

## How it works

```
browser (three.js viewer + chat + SSE)
  | localhost
FastAPI backend
  |- brain:  claude -p (subprocess)  -> a parametric FreeCAD Python script
  |- runner: freecadcmd (sandboxed scratch dir, timeout, scrubbed env)
  |            the script exports out.stl + out.step
  |- SSE:    pushes the script text, then the STL url, to the browser
browser reloads the mesh on each build (live, step-by-step)
```

The script keeps every dimension as an UPPERCASE variable at the top, so an edit
("the legs are too long") is one parameter change and a re-run, not a rewrite.
When a build fails, the error is fed back to the model to self-repair (up to 2
retries).

## Requirements

- Python 3.11+
- [FreeCAD](https://www.freecad.org/) providing a headless binary
  (`freecadcmd` from apt, or `freecad.cmd` from the snap — auto-detected)
- The [`claude`](https://docs.claude.com/en/docs/claude-code) CLI, logged in
  (the brain runs `claude -p`, so it uses your subscription, not the API)

## Install

```bash
git clone <this-repo> cad-agent && cd cad-agent
python -m venv .venv && . .venv/bin/activate   # or: uv venv && . .venv/bin/activate
pip install -e ".[dev]"                          # or: uv pip install -e ".[dev]"
```

## Run

```bash
python -m cad_agent
# open http://127.0.0.1:8099, describe a part, click 建
```

## Configuration

| Env var | Default | Purpose |
|---|---|---|
| `CAD_AGENT_FREECAD` | auto-detect | Force the FreeCAD binary name/path |
| `CAD_AGENT_SCRATCH` | `~/cad-agent-scratch` | Where scripts run and STL/STEP land |

The default scratch is a non-hidden directory under `$HOME` on purpose: the snap
FreeCAD has a private `/tmp` and its sandbox cannot read dotfiles, so neither
`/tmp` nor a `~/.dotdir` scratch is visible to `freecad.cmd`.

## Safety

The generated Python is run, so it is sandboxed lightly for personal single-user
use: a per-run scratch directory as the working dir, a timeout, and a scrubbed
environment (secrets are never passed to the FreeCAD subprocess). `claude -p`
runs with file/shell tools disabled and in a throwaway directory so it cannot
write to your repo. For multi-user or untrusted use, tighten this with
bubblewrap (`--net=none`, restricted filesystem).

## Tests

```bash
pytest            # unit tests run anywhere; the real end-to-end
                  # acceptance test auto-skips when FreeCAD is absent
```

## License

MIT (c) 林亞澤
