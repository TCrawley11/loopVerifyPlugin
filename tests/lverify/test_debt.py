from datetime import date

import pytest

from loopverify import config, debt, verify
from loopverify.debt import DebtRules
from loopverify.debt_python import PythonAdapter
from loopverify.errors import SetupError

from .conftest import commit_all, git, write

TODAY = date(2026, 11, 17)
RULES = DebtRules(TODAY)
BASE = 'base = "main"\n'
DEBT_CHECK = '\n[[check]]\nname = "debt"\nparser = "debt"\n'
REASON = "kept for the plugin loader"


def scan(text, rules=RULES):
    return [(f.code, f.line) for f in PythonAdapter("debt").scan("a.py", text, rules)]


@pytest.mark.parametrize(
    ("justification", "expected"),
    [
        (f"# debt(2026-12-01): {REASON}", None),
        (f"# debt(2026-11-17): {REASON}", None),
        (f"# debt(2027-02-15): {REASON}", None),
        (None, debt.MARKER),
        ("# vendor bug", debt.MARKER),
        (f"# debt(soon): {REASON}", debt.INVALID),
        (f"# debt(2026-13-01): {REASON}", debt.INVALID),
        ("# debt(2026-12-01): flaky", debt.INVALID),
        ("# debt(2026-12-01):", debt.INVALID),
        (f"# debt(2027-02-16): {REASON}", debt.INVALID),
        (f"# debt(2026-11-16): {REASON}", debt.EXPIRED),
    ],
)
def test_judge(justification, expected):
    verdict = debt.judge(justification, RULES)
    assert (verdict and verdict[0]) == expected


def test_the_horizon_and_comment_cap_come_from_the_rules():
    rules = DebtRules(TODAY, comment_max_lines=1, expiry_max_days=10)
    assert debt.judge(f"# debt(2026-11-28): {REASON}", rules)[0] == debt.INVALID
    assert scan("x = 1\n# one\n# two\n", rules) == [(debt.COMMENT, 2)]


@pytest.mark.parametrize(
    "line",
    [
        "import os  # noqa: F401",
        "import os  # NOQA",
        "x: int = 'a'  # type: ignore[assignment]",
        "x = 1  # TODO make configurable",
        "x = 1  # FIXME",
        "x = 1  # HACK around the driver",
    ],
)
def test_comment_markers_need_a_justification(line):
    assert scan(f"{line}\n") == [(debt.MARKER, 1)]
    assert scan(f"{line}  # debt(2026-12-01): {REASON}\n") == []
    assert scan(f"# debt(2026-12-01): {REASON}\n{line}\n") == []


@pytest.mark.parametrize(
    "code",
    [
        "try:\n    f()\nexcept Exception:\n    pass\n",
        "try:\n    f()\nexcept:\n    ...\n",
        "import time\ntime.sleep(1)\n",
        "from time import sleep\nsleep(1)\n",
        "from time import sleep as wait\nwait(1)\n",
    ],
)
def test_code_markers_need_a_justification(code):
    findings = scan(code)
    assert [code for code, _ in findings] == [debt.MARKER]
    line = findings[0][1]
    lines = code.splitlines()
    lines.insert(
        line - 1,
        f"{' ' * (len(lines[line - 1]) - len(lines[line - 1].lstrip()))}"
        f"# debt(2026-12-01): {REASON}",
    )
    assert scan("\n".join(lines) + "\n") == []


@pytest.mark.parametrize(
    "code",
    [
        "try:\n    f()\nexcept KeyError:\n    log()\n",
        "import asyncio\nasyncio.sleep(1)\n",
        "def sleep(s):\n    pass\nsleep(1)\n",
        'def f():\n    """TODO: in a docstring, not a comment."""\n',
        'x = "# noqa in a string"\n',
    ],
)
def test_lookalikes_are_not_markers(code):
    assert scan(code) == []


def test_a_justification_two_lines_up_does_not_count():
    assert scan(f"# debt(2026-12-01): {REASON}\nx = 1\nimport os  # noqa\n") == [(debt.MARKER, 3)]


def test_comment_blocks_over_the_cap_fail_but_headers_and_justifications_do_not():
    assert scan("x = 1\n# a\n# b\n# c\n") == []
    assert scan("x = 1\n# a\n# b\n# c\n# d\n") == [(debt.COMMENT, 2)]
    assert scan("# license\n# a\n# b\n# c\n# d\nx = 1\n") == []
    assert scan(f"x = 1\n# a\n# b\n# c\n# debt(2026-12-01): {REASON}\nimport os  # noqa\n") == []


def test_a_file_that_does_not_parse_is_left_to_lint():
    assert scan("def f(:\n    pass  # noqa\n") == []


def test_debt_config_takes_settings_and_no_command():
    cfg = config.parse(
        {
            "base": "main",
            "check": [{"name": "debt", "parser": "debt", "expiry_max_days": 30}],
        },
        "c",
    )
    assert cfg.checks[0].debt == config.DebtSettings(comment_max_lines=3, expiry_max_days=30)


@pytest.mark.parametrize(
    "extra",
    [{"command": ["x"]}, {"comment_max_lines": 0}, {"expiry_max_days": "90"}, {"timeout": 5}],
)
def test_debt_config_rejects_commands_and_bad_settings(extra):
    with pytest.raises(SetupError):
        config.parse({"base": "main", "check": [{"name": "d", "parser": "debt", **extra}]}, "c")


def test_settings_of_a_tool_check_are_rejected():
    with pytest.raises(SetupError, match="unknown keys"):
        config.parse(
            {
                "base": "main",
                "check": [{"name": "l", "parser": "ruff", "command": ["r"], "expiry_max_days": 9}],
            },
            "c",
        )


def debt_result(project, today=TODAY):
    (result,) = verify.verify(project, today=today).report.results
    return result


def on_main(project, files):
    git(project, "checkout", "-q", "main")
    write(project, files)
    commit_all(project, "main")
    git(project, "checkout", "-q", "feat")
    git(project, "merge", "-q", "main")


def test_new_debt_fails_and_old_debt_does_not(project, remote):
    remote.set_config(BASE + DEBT_CHECK)
    on_main(project, {"old.py": "import os  # noqa\n"})
    write(project, {"new.py": "import os  # noqa\n", "old.py": "x = 1\nimport os  # noqa\n"})
    commit_all(project, "change")
    result = debt_result(project)
    assert [f.path for f in result.new] == ["new.py"]
    assert not result.passed


def test_expired_debt_fails_only_in_files_the_change_touches(project, remote):
    remote.set_config(BASE + DEBT_CHECK)
    marker = f"import os  # noqa  # debt(2026-11-01): {REASON}\n"
    on_main(project, {"touched.py": marker, "untouched.py": marker})
    write(project, {"touched.py": marker + "x = 1\n"})
    commit_all(project, "touch one file")
    result = debt_result(project)
    assert [(f.path, f.code) for f in result.new] == [("touched.py", debt.EXPIRED)]
    assert [f.path for f in result.elsewhere] == ["untouched.py"]
    assert "1 expired in files this change does not touch" in verify.render(
        verify.verify(project, today=TODAY)
    )


def test_renewing_expired_debt_passes(project, remote):
    remote.set_config(BASE + DEBT_CHECK)
    on_main(project, {"a.py": f"import os  # noqa  # debt(2026-11-01): {REASON}\n"})
    write(project, {"a.py": f"import os  # noqa  # debt(2026-12-31): {REASON}\n"})
    commit_all(project, "renew")
    assert debt_result(project).passed


def test_the_debt_check_ignores_untracked_ignored_files(project, remote):
    remote.set_config(BASE + DEBT_CHECK)
    write(project, {".gitignore": ".venv/\n", ".venv/lib.py": "import os  # noqa\n"})
    commit_all(project, "ignore venv")
    assert debt_result(project).passed


def test_the_base_debt_cache_is_per_day(project, remote):
    remote.set_config(BASE + DEBT_CHECK)
    on_main(project, {"a.py": f"import os  # noqa  # debt(2026-11-20): {REASON}\n"})
    write(project, {"b.py": "x = 1\n"})
    commit_all(project, "unrelated change")
    assert debt_result(project, today=TODAY).elsewhere == ()
    later = debt_result(project, today=date(2026, 11, 21))
    assert later.passed
    assert [f.path for f in later.elsewhere] == ["a.py"]


def test_committed_files_in_build_like_folders_are_scanned_at_head_and_base_alike(project, remote):
    remote.set_config(BASE + DEBT_CHECK)
    on_main(project, {"build/gen.py": "import os  # noqa\n", "dist/x.py": "import os  # noqa\n"})
    write(project, {"app.py": "x = 1\n"})
    commit_all(project, "clean change")
    result = debt_result(project)
    assert (result.passed, result.head_count) == (True, 2)


def test_an_except_with_only_a_docstring_and_pass_is_swallowed():
    assert scan('try:\n    f()\nexcept Exception:\n    """ignored"""\n    pass\n') == [
        (debt.MARKER, 3)
    ]
