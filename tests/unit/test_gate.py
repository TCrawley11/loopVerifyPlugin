import pytest

from plugin import gate

PROBE = f"{gate.PROBE_MARKER}=1 gh pr create --title probe --body probe"


def test_blocks_gh_pr_create_carrying_the_probe_marker():
    assert gate.decide("terminal", {"command": PROBE}) == {
        "action": "block",
        "message": gate.BLOCK_MESSAGE,
    }


def test_blocks_when_gh_pr_create_is_chained_after_other_commands():
    command = f"cd repo && git push -u origin feat && {PROBE}"
    assert gate.decide("terminal", {"command": command}) is not None


def test_lets_a_real_gh_pr_create_through_until_p1():
    assert gate.decide("terminal", {"command": "gh pr create --fill"}) is None


@pytest.mark.parametrize(
    "command",
    ["gh pr list", "gh pr view 3", "echo gh-pr-create", f"echo {gate.PROBE_MARKER}"],
)
def test_ignores_commands_that_do_not_open_a_pr(command):
    assert gate.decide("terminal", {"command": command}) is None


def test_ignores_other_tools_even_with_a_matching_command():
    assert gate.decide("write_file", {"command": PROBE}) is None


@pytest.mark.parametrize("args", [None, [], "gh pr create", {}, {"command": None}, {"command": 7}])
def test_never_raises_on_malformed_args(args):
    # A pre_tool_call hook that raises makes Hermes block the tool call.
    assert gate.decide("terminal", args) is None
