import json
import shutil
import sys

import pytest

from loopverify import cli, verify
from loopverify.errors import SetupError

from .conftest import commit_all, git, ruff_check, write

BASE = 'base = "main"\n'
TESTS = f"""
[[check]]
name = "tests"
parser = "exit-code"
command = ["{sys.executable}", "-c", "import pathlib, sys; sys.exit(pathlib.Path('FAIL').exists())"]
"""
MYPY = f"""
[[check]]
name = "types"
parser = "mypy"
command = ["{sys.executable}", "-m", "mypy", "-O", "json", "--strict", "."]
"""


def results(outcome):
    return {result.name: result for result in outcome.report.results}


def test_only_findings_the_change_adds_fail(project, remote):
    remote.set_config(BASE + ruff_check())
    git(project, "checkout", "-q", "main")
    write(project, {"a.py": "import os\n"})
    commit_all(project, "old debt")
    git(project, "checkout", "-q", "feat")
    git(project, "merge", "-q", "main")
    write(project, {"b.py": "import sys\n"})
    commit_all(project, "new debt")

    lint = results(verify.verify(project))["lint"]
    assert [f.path for f in lint.new] == ["b.py"]
    assert (lint.head_count, lint.gone, lint.passed) == (2, 0, False)


def test_a_change_that_keeps_old_debt_and_adds_none_passes(project, remote):
    remote.set_config(BASE + ruff_check())
    git(project, "checkout", "-q", "main")
    write(project, {"a.py": "import os\n"})
    commit_all(project, "old debt")
    git(project, "checkout", "-q", "feat")
    git(project, "merge", "-q", "main")
    write(project, {"b.py": "X = 1\n"})
    commit_all(project, "clean change")
    assert verify.verify(project).report.passed


def test_debt_a_teammate_merged_into_the_base_is_not_blamed_on_the_change(project, remote):
    remote.set_config(BASE + ruff_check())
    write(project, {"b.py": "X = 1\n"})
    commit_all(project, "my change")
    git(project, "checkout", "-q", "main")
    write(project, {"teammate.py": "import json\n"})
    commit_all(project, "teammate debt")
    git(project, "checkout", "-q", "feat")
    git(project, "merge", "-q", "--no-edit", "main")
    lint = results(verify.verify(project))["lint"]
    assert lint.passed and lint.head_count == 1


def test_fixing_old_debt_is_reported_as_gone(project, remote):
    remote.set_config(BASE + ruff_check())
    git(project, "checkout", "-q", "main")
    write(project, {"a.py": "import os\n"})
    commit_all(project, "old debt")
    git(project, "checkout", "-q", "feat")
    git(project, "merge", "-q", "main")
    write(project, {"a.py": "X = 1\n"})
    commit_all(project, "fix")
    lint = results(verify.verify(project))["lint"]
    assert (lint.passed, lint.gone) == (True, 1)


def test_a_check_dir_that_does_not_exist_at_the_base_makes_everything_new(project, remote):
    remote.set_config(BASE + ruff_check(cwd="pkg"))
    write(project, {"pkg/a.py": "import os\n"})
    commit_all(project, "new package")
    lint = results(verify.verify(project))["lint"]
    assert [f.path for f in lint.new] == ["pkg/a.py"]


def test_the_base_is_run_once_then_served_from_the_cache(project, remote):
    remote.set_config(BASE + ruff_check())
    write(project, {"b.py": "X = 1\n"})
    commit_all(project, "change")
    verify.verify(project)
    home = project / ".git" / "lverify"
    shutil.rmtree(home / "base-tree")
    assert verify.verify(project).report.passed
    assert not (home / "base-tree").exists()


def test_failing_tests_fail_even_when_they_also_fail_at_the_base(project, remote):
    remote.set_config(BASE + TESTS)
    git(project, "checkout", "-q", "main")
    write(project, {"FAIL": ""})
    commit_all(project, "broken base")
    git(project, "checkout", "-q", "feat")
    git(project, "merge", "-q", "main")
    tests = results(verify.verify(project))["tests"]
    assert not tests.passed
    assert tests.new[0].message == "exited with 1"


def test_a_new_mypy_error_fails_and_an_old_one_does_not(project, remote):
    remote.set_config(BASE + MYPY)
    git(project, "checkout", "-q", "main")
    write(project, {"a.py": 'def f() -> int:\n    return "old"\n'})
    commit_all(project, "old type error")
    git(project, "checkout", "-q", "feat")
    git(project, "merge", "-q", "main")
    write(project, {"b.py": 'def g() -> int:\n    return "new"\n'})
    commit_all(project, "new type error")
    types = results(verify.verify(project))["types"]
    assert [(f.path, f.code) for f in types.new] == [("b.py", "return-value")]


def test_a_clean_tree_is_stamped_with_the_full_report(project, remote):
    remote.set_config(BASE + ruff_check())
    write(project, {"b.py": "X = 1\n"})
    sha = commit_all(project, "change")
    outcome = verify.verify(project)
    assert outcome.stamp == project / ".git" / "lverify" / "verify" / f"{sha}.json"
    stamp = json.loads(outcome.stamp.read_text())
    assert (stamp["sha"], stamp["passed"], stamp["version"]) == (sha, True, 1)
    assert git(project, "status", "--porcelain") == ""


def test_uncommitted_changes_are_checked_but_not_stamped(project, remote):
    remote.set_config(BASE + ruff_check())
    write(project, {"b.py": "import os\n"})
    outcome = verify.verify(project)
    assert outcome.stamp is None
    assert not outcome.report.passed
    assert "not stamped" in verify.render(outcome)


def test_an_unknown_base_is_a_setup_error(project, remote):
    remote.set_config('base = "origin/nope"\n' + ruff_check())
    with pytest.raises(SetupError, match="fetch it first"):
        verify.verify(project)


def test_cli_exit_codes_are_pass_fail_and_setup(project, remote, capsys):
    remote.set_config(BASE + ruff_check())
    write(project, {"b.py": "X = 1\n"})
    commit_all(project, "clean")
    assert cli.main(["verify", "--repo", str(project)]) == cli.EXIT_PASS
    write(project, {"c.py": "import os\n"})
    commit_all(project, "debt")
    capsys.readouterr()
    assert cli.main(["verify", "--repo", str(project), "--json"]) == cli.EXIT_FAIL
    assert json.loads(capsys.readouterr().out)["passed"] is False
    git(project, "config", "--unset", "lverify.stateRepo")
    assert cli.main(["verify", "--repo", str(project)]) == cli.EXIT_SETUP
    assert "lverify init" in capsys.readouterr().err


def test_init_connects_the_repo_and_says_when_the_config_is_missing(project, remote, capsys):
    git(project, "config", "--unset", "lverify.stateRepo")
    assert cli.main(["init", "--repo", str(project), "--state-repo", remote.url]) == 0
    out = capsys.readouterr().out
    assert "project github.com/Owner/proj" in out
    assert "add github.com/Owner/proj/config.toml to the state repo" in out
    assert git(project, "config", "--local", "lverify.stateRepo") == remote.url
