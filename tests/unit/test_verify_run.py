import json

import pytest

from plugin import verify_run

REPORT = {
    "sha": "a" * 40,
    "clean": True,
    "passed": False,
    "results": [
        {"name": "lint", "error": None, "new": []},
        {"name": "debt", "error": None, "new": [{"code": "debt-marker"}]},
        {"name": "types", "error": "timed out", "new": []},
    ],
}


def test_parse_skips_noise_before_the_report():
    run = verify_run.parse("Installed 3 packages in 2ms\n" + json.dumps(REPORT, indent=2))
    assert (run.passed, run.sha, run.clean) == (False, "a" * 40, True)
    assert run.describe() == "FAIL (lint pass, debt FAIL, types FAIL)"
    assert run.to_record()["checks"][1] == {"name": "debt", "passed": False, "new": 1}


@pytest.mark.parametrize("output", ["", "lverify: no verify config", "{not json", '{"x": 1}'])
def test_output_without_a_report_is_an_error(output):
    with pytest.raises(verify_run.VerifyError):
        verify_run.parse(output)


class Ctx:
    def __init__(self, reply):
        self.reply, self.calls = reply, []

    def dispatch_tool(self, name, args, **kwargs):
        self.calls.append((name, args, kwargs))
        return self.reply


def test_run_dispatches_the_terminal_in_the_sandbox_within_its_foreground_limit():
    ctx = Ctx(json.dumps({"output": json.dumps(REPORT), "exit_code": 1}))
    run = verify_run.run(ctx, "/workspace/ap1", "t1", "lverify verify --json")
    ((name, args, kwargs),) = ctx.calls
    assert (name, args["workdir"], args["timeout"], kwargs) == (
        "terminal",
        "/workspace/ap1",
        600,
        {"task_id": "t1"},
    )
    assert not run.passed


@pytest.mark.parametrize(
    "reply",
    ["not json", json.dumps({"output": "boom", "exit_code": 2}), json.dumps({"error": "x"})],
)
def test_a_verify_that_did_not_finish_is_an_error(reply):
    with pytest.raises(verify_run.VerifyError):
        verify_run.run(Ctx(reply), "/w", None, "lverify verify --json")
