"""Runs loopverify inside a real Hermes, each scenario in a fresh interpreter.

These tests need the interpreter of a Hermes checkout (they are skipped elsewhere):

    <hermes-venv>/bin/python -m pytest tests/integration
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("hermes_cli.plugins", reason="needs the interpreter of a Hermes checkout")

REPO = Path(__file__).resolve().parents[2]
RESULT_PREFIX = "RESULT "

PREAMBLE = """
import json
from hermes_cli.plugins import discover_plugins, get_plugin_command_handler, get_plugin_manager
from model_tools import handle_function_call

discover_plugins()

def emit(value):
    print("RESULT " + json.dumps(value))
"""


@pytest.fixture
def hermes_home(tmp_path):
    """An isolated Hermes home with this checkout installed and enabled as a user plugin."""
    home = tmp_path / ".hermes"
    (home / "plugins").mkdir(parents=True)
    (home / "plugins" / "loopverify").symlink_to(REPO, target_is_directory=True)
    (home / "config.yaml").write_text("plugins:\n  enabled:\n    - loopverify\n")
    return home


@pytest.fixture
def in_hermes(hermes_home):
    """Run a script in a fresh Hermes interpreter and return what it passed to emit()."""

    def run(script: str):
        done = subprocess.run(
            [sys.executable, "-c", PREAMBLE + script],
            env={**os.environ, "HERMES_HOME": str(hermes_home)},
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        results = [line for line in done.stdout.splitlines() if line.startswith(RESULT_PREFIX)]
        assert results, f"script emitted no result.\nstdout: {done.stdout}\nstderr: {done.stderr}"
        return json.loads(results[-1].removeprefix(RESULT_PREFIX))

    return run
