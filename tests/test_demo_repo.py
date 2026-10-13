import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from triage.dataset import CASE_LABELS

ROOT = Path(__file__).resolve().parent.parent
DEMO = ROOT / "demo-app"
WORKFLOWS = ROOT / ".github" / "workflows"
SAMPLES = ROOT / "samples"
PYTEST_BIN = Path(sys.executable).parent / "pytest"

# Words that would tell the model the answer. None of them may appear in anything the model reads.
LEAK = re.compile(
    r"flak|real[_ ]?bug|real[_ ]?failure|infra|dependency[_ ]?(?:missing|env)|regression|simulat|scenario|"
    r"needs_human_review",
    re.I,
)


def run_demo_tests(case, attempt="1", *extra):
    # Use the pytest executable, as the workflow does: `python -m pytest` would put the cwd on sys.path
    # and hide a missing `pythonpath` setting.
    if not PYTEST_BIN.exists():
        pytest.skip("pytest executable not found next to the interpreter")
    env = {**os.environ, "CASE": case, "GITHUB_RUN_ATTEMPT": attempt}
    return subprocess.run([str(PYTEST_BIN), "-q", "-p", "no:cacheprovider", *extra],
                          cwd=DEMO, env=env, capture_output=True, text=True)


def load(name):
    workflow = yaml.safe_load((WORKFLOWS / name).read_text())
    workflow["_on"] = workflow.get("on", workflow.get(True))  # YAML 1.1 parses the key `on` as True
    return workflow


def test_demo_passes_with_no_case():
    result = run_demo_tests("none")
    assert result.returncode == 0, result.stdout


def test_case_a_fails_deterministically():
    for attempt in ("1", "2"):
        result = run_demo_tests("case_a", attempt)
        assert result.returncode != 0 and "test_apply_discount" in result.stdout


def test_case_b_fails_on_first_attempt_and_passes_on_rerun():
    first = run_demo_tests("case_b", "1")
    assert first.returncode != 0 and "TimeoutError" in first.stdout
    assert run_demo_tests("case_b", "2").returncode == 0


def test_every_dispatchable_case_has_a_label():
    options = load("ci.yml")["_on"]["workflow_dispatch"]["inputs"]["case"]["options"]
    assert set(options) - {"none"} == set(CASE_LABELS)
    assert load("ci.yml")["run-name"].startswith("CI (")


def test_ci_workflow_runs_in_the_demo_app():
    assert load("ci.yml")["jobs"]["test"]["defaults"]["run"]["working-directory"] == "demo-app"


def test_triage_workflow_watches_ci_and_only_reacts_to_failures():
    workflow = load("triage.yml")
    trigger = workflow["_on"]["workflow_run"]
    assert trigger["workflows"] == [load("ci.yml")["name"]]
    assert trigger["types"] == ["completed"]
    assert workflow["jobs"]["triage"]["if"] == "github.event.workflow_run.conclusion == 'failure'"
    assert workflow["permissions"] == {"actions": "read", "contents": "read"}


def test_triage_workflow_never_checks_out_the_failed_runs_code():
    text = (WORKFLOWS / "triage.yml").read_text()
    assert "head_sha" not in text and "head_branch" not in text and "pull_request" not in text
    steps = load("triage.yml")["jobs"]["triage"]["steps"]
    checkouts = [s for s in steps if str(s.get("uses", "")).startswith("actions/checkout")]
    assert len(checkouts) == 1 and "with" not in checkouts[0]


# --- The answer must not leak into the model's input -------------------------------------------------


def test_no_label_words_in_the_workflow_or_run_title():
    # Step scripts and env values are printed into the job log; the run title reaches the model as metadata.
    assert not LEAK.search((WORKFLOWS / "ci.yml").read_text())


@pytest.mark.parametrize("path", sorted(p for p in DEMO.rglob("*") if p.is_file() and p.suffix in {".py", ".txt", ".ini"}))
def test_no_label_words_in_demo_source(path):
    # Tracebacks can print any line of the app or test code.
    assert not LEAK.search(path.read_text()), path


@pytest.mark.parametrize("case", ["none", *CASE_LABELS])
def test_no_label_words_in_demo_test_output(case):
    result = run_demo_tests(case, "1", "-v", "--tb=long")
    assert not LEAK.search(result.stdout + result.stderr)


def test_no_label_words_in_sample_logs_or_metadata():
    for entry in json.loads((SAMPLES / "labels.json").read_text()):
        assert not LEAK.search((SAMPLES / entry["log"]).read_text()), entry["id"]
        assert not LEAK.search(json.dumps(entry.get("meta", {}))), entry["id"]


def test_leak_check_would_catch_the_old_names():
    for old in ("SCENARIO: flaky", "CI (real_bug)", "infra_timeout", "dependency_missing", "# Simulated flake"):
        assert LEAK.search(old), old
