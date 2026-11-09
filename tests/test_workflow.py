"""The forge workflow and the page agree on names, inputs and file locations."""
from pathlib import Path

import yaml

ROOT = Path(__file__).parent.parent


def test_forge_workflow_contract():
    wf = yaml.safe_load((ROOT / ".github/workflows/forge.yml").read_text())
    inputs = wf[True]["workflow_dispatch"]["inputs"]  # YAML reads the key `on` as True
    assert set(inputs) == {"url", "goal", "name", "max_steps"}
    assert wf["run-name"].startswith("Build ${{ inputs.name }} from")
    steps = [s["name"] for s in wf["jobs"]["build"]["steps"]]
    page = (ROOT / "docs/index.html").read_text()
    for step in steps:
        assert f'"{step}"' in page, f"page doesn't map workflow step {step!r} to a stage"
    assert 'const WORKFLOW = "forge.yml"' in page
    assert "`Build ${name} from`" in page
    for script in (s.get("run", "") for s in wf["jobs"]["build"]["steps"]):
        assert "${{ inputs." not in script, "inputs must reach scripts via env vars only"
