import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
ACTION = yaml.safe_load((ROOT / "action.yml").read_text())
EXAMPLE_TEXT = (ROOT / "examples" / "triage.yml").read_text()


def load_workflow(text):
    workflow = yaml.safe_load(text)
    workflow["_on"] = workflow.get("on", workflow.get(True))  # YAML 1.1 parses the key `on` as True
    return workflow


def test_action_is_a_composite_with_the_inputs_other_repos_need():
    assert ACTION["runs"]["using"] == "composite"
    inputs = ACTION["inputs"]
    assert inputs["github-token"]["required"] and inputs["slack-webhook-url"]["required"]
    assert not inputs["anthropic-api-key"]["required"]
    assert inputs["run-id"]["default"] == "${{ github.event.workflow_run.id }}"
    assert inputs["repo"]["default"] == "${{ github.repository }}"
    assert inputs["mode"]["default"] == "hybrid"
    assert inputs["model"]["default"] == "" and inputs["effort"]["default"] == ""


def test_every_script_step_names_its_shell():
    for step in ACTION["runs"]["steps"]:
        if "run" in step:
            assert step["shell"] == "bash", step.get("name")


def test_inputs_reach_scripts_only_through_environment_variables():
    # Interpolating `${{ ... }}` straight into a script lets a crafted value run as shell code.
    for step in ACTION["runs"]["steps"]:
        if "run" in step:
            assert "${{" not in step["run"], step.get("name")


def test_action_runs_the_agent_and_always_saves_the_record():
    steps = ACTION["runs"]["steps"]
    notify = next(s for s in steps if "triage notify" in s.get("run", ""))
    assert "--out" in notify["run"] and notify["env"]["PYTHONPATH"] == "${{ github.action_path }}"
    upload = next(s for s in steps if str(s.get("uses", "")).startswith("actions/upload-artifact"))
    assert upload["if"] == "always()" and upload["with"]["name"] == "diagnosis-${{ inputs.run-id }}"
    assert steps.index(upload) > steps.index(notify)


def test_secrets_are_not_printed_by_the_action():
    text = (ROOT / "action.yml").read_text()
    assert "echo" not in text and "set -x" not in text


def test_this_repo_triage_workflow_uses_its_own_action():
    workflow = load_workflow((ROOT / ".github" / "workflows" / "triage.yml").read_text())
    steps = workflow["jobs"]["triage"]["steps"]
    action_step = next(s for s in steps if s.get("uses") == "./")
    assert set(action_step["with"]) == {"github-token", "anthropic-api-key", "slack-webhook-url"}


def test_example_for_other_repos_is_valid_and_complete():
    workflow = load_workflow(EXAMPLE_TEXT)
    assert workflow["_on"]["workflow_run"]["types"] == ["completed"]
    assert workflow["permissions"] == {"actions": "read", "contents": "read"}
    job = workflow["jobs"]["triage"]
    assert job["if"] == "github.event.workflow_run.conclusion == 'failure'"
    step = job["steps"][0]
    assert re.fullmatch(r"ron1120/ci-triage-agent@\S+", step["uses"])
    required = {name for name, spec in ACTION["inputs"].items() if spec["required"]}
    assert required <= set(step["with"])
    assert not any("checkout" in str(s.get("uses", "")) for s in job["steps"])


def test_readme_shows_the_example_verbatim():
    assert EXAMPLE_TEXT.strip() in (ROOT / "README.md").read_text()
