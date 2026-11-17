import json
import sys
from datetime import date
from pathlib import Path

import pytest

from loopverify import checks
from loopverify.config import CheckSpec


def script(code: str) -> tuple[str, ...]:
    return (sys.executable, "-c", code)


def emit(stdout: str, exit_code: int) -> tuple[str, ...]:
    return script(f"import sys; sys.stdout.write({stdout!r}); sys.exit({exit_code})")


def run(tmp_path: Path, parser: str, command: tuple[str, ...], cwd: str = ".", timeout=60):
    (tmp_path / cwd).mkdir(parents=True, exist_ok=True)
    spec = CheckSpec("c", parser, command, cwd, timeout)
    return checks.run(spec, tmp_path, tmp_path / "cache", date(2026, 11, 17))


def ruff_item(filename: str, code: str | None = "F401", row: int = 3) -> dict:
    return {"filename": filename, "code": code, "message": "m", "location": {"row": row}}


def test_ruff_findings_get_paths_relative_to_the_repo_root(tmp_path):
    out = json.dumps([ruff_item(str(tmp_path / "pkg" / "a.py"))])
    result = run(tmp_path, "ruff", emit(out, 1), cwd="pkg")
    (finding,) = result.findings
    assert (finding.path, finding.code, finding.line, result.error) == ("pkg/a.py", "F401", 3, None)


def test_a_ruff_syntax_error_without_a_code_is_marked_as_a_parse_error(tmp_path):
    out = json.dumps([ruff_item("a.py", code=None)])
    (finding,) = run(tmp_path, "ruff", emit(out, 1)).findings
    assert finding.code == "invalid-syntax"


@pytest.mark.parametrize(
    ("stdout", "exit_code"),
    [("[]", 2), ("not json", 1), ('{"a": 1}', 1), (json.dumps([{"code": "F1"}]), 1)],
)
def test_ruff_crashes_and_unexpected_output_are_errors(tmp_path, stdout, exit_code):
    result = run(tmp_path, "ruff", emit(stdout, exit_code))
    assert result.error is not None
    assert result.findings == ()


def mypy_line(file: str, code: str = "arg-type", severity: str = "error") -> str:
    item = {"file": file, "line": 7, "message": "m", "code": code, "severity": severity}
    return json.dumps(item)


def test_mypy_error_lines_become_findings_and_notes_are_skipped(tmp_path):
    out = "\n".join([mypy_line("a.py"), mypy_line("a.py", severity="note"), "Found 1 error"])
    result = run(tmp_path, "mypy", emit(out, 1), cwd="src")
    assert [(f.path, f.code, f.line) for f in result.findings] == [("src/a.py", "arg-type", 7)]


def test_mypy_blocking_parse_errors_exit_2_but_still_count_as_findings(tmp_path):
    result = run(tmp_path, "mypy", emit(mypy_line("a.py", code="syntax"), 2))
    assert result.error is None
    assert [f.code for f in result.findings] == ["syntax"]


@pytest.mark.parametrize(("stdout", "exit_code"), [("", 2), ("a.py:1: error: x", 1), ("{bad", 1)])
def test_mypy_crashes_and_non_json_output_are_errors(tmp_path, stdout, exit_code):
    assert run(tmp_path, "mypy", emit(stdout, exit_code)).error is not None


def test_an_exit_code_check_fails_with_one_finding_naming_the_code(tmp_path):
    assert run(tmp_path, "exit-code", emit("", 0)).findings == ()
    (finding,) = run(tmp_path, "exit-code", emit("", 5)).findings
    assert finding.message == "exited with 5"


def test_a_missing_command_or_a_timeout_is_an_error(tmp_path):
    assert "could not start" in run(tmp_path, "exit-code", ("no-such-tool-xyz",)).error
    slow = run(tmp_path, "exit-code", script("import time; time.sleep(5)"), timeout=1)
    assert slow.error == "timed out after 1s"


def test_tool_caches_go_to_the_given_cache_dir_not_the_tree(tmp_path):
    code = "import os; print(os.environ['RUFF_CACHE_DIR'], os.environ['MYPY_CACHE_DIR'])"
    result = run(tmp_path, "exit-code", script(code))
    assert str(tmp_path / "cache" / "ruff") in result.output_tail
    assert str(tmp_path / "cache" / "mypy") in result.output_tail
