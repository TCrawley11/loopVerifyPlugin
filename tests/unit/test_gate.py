import json
import subprocess
from types import SimpleNamespace

import pytest

from plugin import gate, locate, records

KEY = b"k" * 32


def git(repo, *args):
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


@pytest.fixture
def connected(repo_with_change):
    git(repo_with_change, "config", "lverify.stateRepo", "https://example.com/state.git")
    return repo_with_change


@pytest.fixture
def store(connected):
    return records.Store(connected, KEY)


def env_at(cwd):
    return SimpleNamespace(cwd=str(cwd))


def actions(command, repo, store, env=None):
    return gate.actions(command, repo, str(repo), env or env_at(repo), store)


def test_the_p0_probe_is_still_blocked():
    hook = gate.make_hook(lambda _: None, lambda _: None)
    probe = f"{gate.PROBE_MARKER}=1 gh pr create --title probe --body probe"
    assert hook(tool_name="terminal", args={"command": probe}) == {
        "action": "block",
        "message": gate.BLOCK_MESSAGE,
    }


@pytest.mark.parametrize(
    ("tool_name", "args"),
    [
        ("terminal", {"command": "ls"}),
        ("terminal", {"command": "gh pr list"}),
        ("write_file", {"command": "gh pr create"}),
        ("terminal", None),
        ("terminal", {"command": 7}),
    ],
)
def test_other_calls_pass_without_looking_for_a_repo(tool_name, args):
    def no_env(_):
        raise AssertionError("looked up the env")

    assert gate.make_hook(lambda _: None, no_env)(tool_name=tool_name, args=args) is None


def test_a_repo_not_connected_to_lverify_is_never_gated(repo_with_change):
    hook = gate.make_hook(
        lambda _: pytest.fail("opened a store"), lambda _: env_at(repo_with_change)
    )
    assert hook(tool_name="terminal", args={"command": "gh pr create --fill"}) is None


def test_a_gated_command_whose_repo_is_unknown_is_blocked():
    hook = gate.make_hook(lambda _: None, lambda _: None)
    reply = hook(tool_name="terminal", args={"command": "git push"})
    assert reply["action"] == "block"
    assert "cd <repo> && <command>" in reply["message"]


def test_pr_create_reads_the_inline_body_and_the_branch(connected, store):
    (action,) = actions("gh pr create --title t --body 'lverify-signoff: x'", connected, store)
    assert action == gate.PrCreate(
        git(connected, "rev-parse", "HEAD"), "lverify-signoff: x", "feat"
    )


def test_pr_create_reads_a_body_file_relative_to_the_command_dir(connected, store):
    (connected / "body.md").write_text("from a file")
    (action,) = actions("cd sub/.. && gh pr create -F body.md", connected, store)
    assert action.body == "from a file"


@pytest.mark.parametrize("flags", ["--fill", "--body-file -", "--body-file missing.md", ""])
def test_a_body_the_gate_cannot_read_is_none(connected, store, flags):
    (action,) = actions(f"gh pr create {flags}", connected, store)
    assert action.body is None


def test_pr_create_with_head_names_that_branch(connected, store):
    git(connected, "branch", "other", "main")
    (action,) = actions("GH_TOKEN=x gh pr create --head other --body b", connected, store)
    assert (action.branch, action.sha) == ("other", git(connected, "rev-parse", "main"))


@pytest.mark.parametrize(
    ("command", "branch", "ref"),
    [
        ("git push", "feat", "HEAD"),
        ("git push -u origin feat", "feat", "feat"),
        ("git push origin HEAD:review", "review", "HEAD"),
        ("git push --force-with-lease origin +main:feat", "feat", "main"),
        ("git -c core.x=1 push origin refs/heads/main", "main", "main"),
    ],
)
def test_pushes_name_the_branch_and_commit(connected, store, command, branch, ref):
    (action,) = actions(command, connected, store)
    assert (action.branch, action.sha) == (branch, git(connected, "rev-parse", ref))
    assert (action.has_pr, action.guarded) == (False, False)


@pytest.mark.parametrize(
    "command",
    ["git push origin --delete feat", "git push origin :feat", "git commit -m 'push it'", "echo"],
)
def test_deletes_and_lookalikes_are_not_gated_actions(connected, store, command):
    assert actions(command, connected, store) == []


@pytest.mark.parametrize(
    "command",
    ["git push --all", "git push --tags", "git -C ../other push", "bash -c 'gh pr create --fill'"],
)
def test_shapes_the_gate_cannot_read_are_unreadable(connected, store, command):
    assert actions(command, connected, store) == [gate.Unreadable(command)]


def test_a_push_knows_whether_its_branch_has_a_pr(connected, store):
    store.add_pr_branch("feat")
    (action,) = actions("git push", connected, store)
    assert action.has_pr


def test_a_guarded_repo_lists_lverify_files_in_the_pushed_commits(connected, store):
    git(connected, "config", "lverify.guardUpstream", "true")
    (connected / ".lverify").mkdir()
    (connected / ".lverify" / "DECISIONS.md").write_text("x")
    (connected / "lverify.toml").write_text("x")
    git(connected, "add", ".")
    git(connected, "commit", "-qm", "leak")
    (action,) = actions("git push", connected, store)
    assert action.guarded
    assert action.lverify_paths == (".lverify/DECISIONS.md", "lverify.toml")


def hook_with(decision, connected, store, monkeypatch):
    seen = []

    def decide(action, standing):
        seen.append((action, standing))
        return decision

    monkeypatch.setattr(gate, "decide", decide)
    return gate.make_hook(lambda _: store, lambda _: env_at(connected)), seen


def test_the_hook_blocks_with_the_reason_decide_gives(connected, store, monkeypatch):
    hook, _ = hook_with("no sign-off for abc", connected, store, monkeypatch)
    reply = hook(tool_name="terminal", args={"command": "git push"})
    assert reply == {"action": "block", "message": "loopverify gate: no sign-off for abc"}


def test_the_hook_passes_the_sealed_standing_of_the_commit(connected, store, monkeypatch):
    from .test_records import BUG, rejected

    sha = git(connected, "rev-parse", "HEAD")
    store.save_review(rejected(sha, BUG))
    hook, seen = hook_with(None, connected, store, monkeypatch)
    hook(tool_name="terminal", args={"command": "git push"})
    ((_, standing),) = seen
    assert standing.review["sha"] == sha
    assert len(standing.open_findings) == 1


def test_an_allowed_pr_records_its_branch_so_later_pushes_are_gated(connected, store, monkeypatch):
    hook, _ = hook_with(None, connected, store, monkeypatch)
    assert hook(tool_name="terminal", args={"command": "gh pr create --body b"}) is None
    assert store.pr_branches() == frozenset({"feat"})


def test_a_command_the_gate_cannot_check_is_blocked(connected, store, monkeypatch):
    hook, _ = hook_with(None, connected, store, monkeypatch)
    reply = hook(tool_name="terminal", args={"command": "git push origin no-such-branch"})
    assert reply["action"] == "block"
    assert "could not check this command" in reply["message"]


@pytest.mark.parametrize(
    ("command", "workdir", "cwd", "expected"),
    [
        ("git push", None, "/workspace/ap1", "/workspace/ap1"),
        ("git push", "/workspace/x", "/workspace/ap1", "/workspace/x"),
        ("cd ap1 && git push", None, "/workspace", "/workspace/ap1"),
        ("cd /workspace/b; git push", None, "/workspace/a", "/workspace/b"),
        ("cd ../b && git push", None, "/workspace/a", "/workspace/b"),
        ("git push", None, None, None),
    ],
)
def test_command_dir(command, workdir, cwd, expected):
    env = SimpleNamespace(cwd=cwd) if cwd else None
    assert locate.command_dir(command, workdir, env) == expected


def docker(cwd="/workspace", host="/home/tygo/code", mount="/workspace"):
    return SimpleNamespace(cwd=cwd, host_cwd=host, host_cwd_mount=mount)


@pytest.mark.parametrize(
    ("sandbox", "env", "host"),
    [
        ("/workspace", docker(), "/home/tygo/code"),
        ("/workspace/ap1", docker(), "/home/tygo/code/ap1"),
        ("/home/pn/x", docker(), None),
        ("/workspacex/ap1", docker(), None),
        ("/workspace/ap1", docker(host=None, mount=None), None),
        ("/home/tygo/code/ap1", SimpleNamespace(cwd="/"), "/home/tygo/code/ap1"),
        ("/workspace/ap1", None, None),
    ],
)
def test_to_host_and_back(sandbox, env, host):
    assert locate.to_host(sandbox, env) == host
    if host:
        assert locate.to_sandbox(host, env) == sandbox


def test_the_hook_reads_the_body_file_through_the_mount(tmp_path):
    (tmp_path / "body.md").write_text("mapped")
    env = docker(cwd="/workspace", host=str(tmp_path))
    assert gate._body(["-F", "body.md"], "/workspace", env) == "mapped"


def test_tool_reply_is_json():
    from plugin import tools

    assert json.loads(tools._reply("x")) == {"report": "x"}


def test_the_review_tool_maps_a_relative_repo_through_the_env(ctx, log, tmp_path, repo_with_change):
    from plugin import tools

    seen = []

    def fake_run(_ctx, request, _store, command):
        seen.append(request)
        return "done"

    import plugin.review_flow as flow

    original, flow.run = flow.run, fake_run
    try:
        handler = tools.make_handler(ctx, lambda _: None, "verify", lambda _: env_at(tmp_path))
        assert json.loads(handler({"repo": "repo"}, task_id="t1")) == {"report": "done"}
        assert json.loads(handler({}, task_id="t1"))["report"] == "done"
    finally:
        flow.run = original
    first, second = seen
    assert (str(first.host), first.sandbox, first.task_id) == (str(repo_with_change),) * 2 + ("t1",)
    assert (first.base, second.sandbox) == ("origin/HEAD", str(tmp_path))


def test_the_review_tool_refuses_a_folder_outside_the_mount(ctx):
    from plugin import tools

    handler = tools.make_handler(ctx, lambda _: None, "verify", lambda _: docker())
    assert "cannot map" in json.loads(handler({"repo": "/home/pn/x"}))["report"]
