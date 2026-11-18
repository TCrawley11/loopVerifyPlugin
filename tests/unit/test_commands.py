import json
import subprocess

import pytest

from plugin import commands, repo, review_flow

from .conftest import FakeEnv

BUG = {
    "title": "add subtracts",
    "detail": "x.py:2",
    "repro": "pytest -k add",
    "impact": "breaks-intended-use",
    "scope": "new-or-changed",
}


def commit(path, name, text):
    (path / name).write_text(text)
    subprocess.run(["git", "-C", str(path), "add", "."], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(path), "commit", "-qm", name], check=True, capture_output=True)


@pytest.fixture
def lverify(ctx, log, tmp_path):
    return commands.make_handler(ctx, log, tmp_path / "data", env_for=lambda _: FakeEnv(ctx.cwd))


@pytest.mark.parametrize("raw_args", ["", "  ", "help", "nope extra"])
def test_unknown_subcommand_shows_usage_for_every_subcommand(lverify, raw_args):
    reply = lverify(raw_args)
    assert reply.startswith("Usage:")
    for name in ("report", "review", "p0"):
        assert f"/lverify {name}" in reply


def test_review_sends_only_the_committed_diff_and_reports_the_sha(lverify, ctx, repo_with_change):
    reply = lverify("review repo main", platform="cli")
    sha = repo.head_sha(repo_with_change)
    header, verified, blank, outcome = reply.splitlines()[:4]
    assert verified == "Verify: PASS (lint pass)"
    assert header.startswith(f"Review of {sha[:10]} against main (")
    assert header.endswith(" s, 1,200 tokens in, 300 out)")
    assert (blank, outcome) == (
        "",
        "SIGN-OFF by openrouter:google/gemini-4-carbon (round 1, 0 blocking findings, 0 notes)",
    )


def test_every_verdict_is_saved_per_commit_in_the_repos_git_dir(lverify, repo_with_change):
    reply = lverify("review repo main")
    sha = repo.head_sha(repo_with_change)
    record = repo_with_change / ".git" / "lverify" / "reviews" / f"{sha}.json"
    assert f"Saved to {record}" in reply
    saved = json.loads(record.read_text())
    assert (saved["sha"], saved["base"], saved["signed_off"]) == (sha, "main", True)
    assert (saved["provider"], saved["model"]) == ("openrouter", "google/gemini-4-carbon")
    assert (
        subprocess.run(
            ["git", "-C", str(repo_with_change), "status", "--porcelain"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        == ""
    )


def test_a_reply_without_usage_says_so_instead_of_claiming_zero_tokens(
    lverify, ctx, repo_with_change, monkeypatch
):
    original = ctx.llm.complete_structured

    def no_usage(**kwargs):
        result = original(**kwargs)
        result.usage = None
        return result

    monkeypatch.setattr(ctx.llm, "complete_structured", no_usage)
    header = lverify("review repo main").splitlines()[0]
    assert header.endswith("token usage not reported, possibly a cached reply)")
    diff_block = ctx.llm.calls[0]["input"][-1]["text"]
    assert "-    return a + b" in diff_block
    assert "+    return a - b" in diff_block


def test_review_redacts_secrets_in_the_diff(lverify, ctx, repo_with_change):
    (repo_with_change / "x.py").write_text("KEY = 'sk-secret'\n")
    subprocess.run(
        ["git", "-C", str(repo_with_change), "commit", "-am", "leak"],
        check=True,
        capture_output=True,
    )
    lverify("review repo main")
    assert "sk-secret" not in ctx.llm.calls[0]["input"][-1]["text"]


def test_review_reports_findings_as_rejection(lverify, ctx, repo_with_change):
    ctx.llm.parsed = {
        "summary": "Broken.",
        "findings": [BUG],
    }
    reply = lverify("review repo main")
    assert "REJECTED by openrouter:google/gemini-4-carbon (round 1, 1 blocking finding," in reply
    assert "    Repro:\n        pytest -k add" in reply


def test_review_outside_a_repo_fails_without_calling_the_model(lverify, ctx):
    reply = lverify("review . main")
    assert reply.startswith("Review failed:")
    assert ctx.llm.calls == []


def test_review_with_an_unknown_base_never_calls_the_model(lverify, ctx, repo_with_change):
    reply = lverify("review repo no-such-ref")
    assert reply.startswith("Review failed:")
    assert ctx.llm.calls == []


def test_review_explains_how_to_grant_the_model_override(lverify, ctx, repo_with_change):
    ctx.llm.error = PermissionError("Plugin 'loopverify' cannot override the provider")
    reply = lverify("review repo main")
    assert reply.startswith("Review not allowed:")
    assert "hermes plugins enable loopverify" in reply


def test_review_refuses_to_send_an_unredacted_diff_on_an_older_hermes(
    lverify, ctx, repo_with_change, monkeypatch
):
    monkeypatch.delattr(type(ctx), "redact_text")
    reply = lverify("review repo main")
    assert reply.startswith("Review not run:")
    assert "hermes update" in reply
    assert ctx.llm.calls == []


def test_a_reply_without_a_verdict_is_saved_and_quoted(lverify, ctx, repo_with_change, tmp_path):
    ctx.llm.parsed, ctx.llm.text = None, "Looks fine to me. " * 40
    reply = lverify("review repo main", platform="cli")
    assert "by openrouter:google/gemini-4-carbon failed after" in reply
    assert "returned no JSON verdict" in reply
    sha = repo.head_sha(repo_with_change)
    saved = repo_with_change / ".git" / "lverify" / "reviews" / f"{sha}.failed.txt"
    assert saved.read_text() == ctx.llm.text
    assert f"Full reply: {saved}" in reply
    assert ("Looks fine to me. " * 40)[: review_flow.EXCERPT_CHARS] in reply


def test_the_classic_cli_gets_no_live_status_because_it_would_print_late(
    lverify, ctx, repo_with_change
):
    lverify("review repo main", platform="cli")
    assert ctx.posts == []


@pytest.mark.parametrize("platform", ["tui", "desktop", "discord"])
def test_other_surfaces_get_one_status_line_edited_when_done(
    lverify, ctx, repo_with_change, platform
):
    lverify("review repo main", platform=platform)
    first, second, last = ctx.posts
    assert first.startswith("loopverify: verifying ")
    assert second.startswith("loopverify: openrouter:google/gemini-4-carbon is reviewing ")
    assert last.startswith("loopverify: review of ") and " signed off in " in last


def test_review_rejects_extra_arguments(lverify):
    assert lverify("review a b c") == "Usage: /lverify review [repo] [base]"


def test_a_review_after_a_rejection_is_the_next_round_and_sees_the_fix(
    lverify, ctx, repo_with_change
):
    ctx.llm.parsed = {"summary": "Broken.", "findings": [BUG]}
    lverify("review repo main")
    rejected = repo.head_sha(repo_with_change)
    commit(repo_with_change, "x.py", "def add(a, b):\n    return b + a\n")
    ctx.llm.parsed = {"summary": "Fixed.", "findings": []}
    reply = lverify("review repo main")
    assert "(round 2, 0 blocking findings" in reply
    texts = [block["text"] for block in ctx.llm.calls[1]["input"]]
    assert texts[2].startswith(f"PREVIOUS REVIEW (round 1, of {rejected})\n1. add subtracts")
    assert "+    return b + a" in texts[3]


def test_a_signed_off_review_or_another_base_does_not_start_a_new_round(
    lverify, ctx, repo_with_change
):
    lverify("review repo main")
    commit(repo_with_change, "y.py", "Y = 1\n")
    ctx.llm.parsed = {"summary": "Broken.", "findings": [BUG]}
    lverify("review repo HEAD~2")
    commit(repo_with_change, "z.py", "Z = 1\n")
    assert "(round 1," in lverify("review repo main")


def test_a_rebased_branch_starts_again_at_round_one(lverify, ctx, repo_with_change):
    ctx.llm.parsed = {"summary": "Broken.", "findings": [BUG]}
    lverify("review repo main")
    subprocess.run(
        ["git", "-C", str(repo_with_change), "commit", "-q", "--amend", "-m", "reworded"],
        check=True,
        capture_output=True,
    )
    assert "(round 1," in lverify("review repo main")


def test_a_rejection_tells_you_how_to_overrule(lverify, ctx, repo_with_change):
    ctx.llm.parsed = {"summary": "Broken.", "findings": [BUG]}
    reply = lverify("review repo main")
    assert reply.endswith(f"To overrule a finding you disagree with: {commands.OVERRULE_USAGE}")


def test_overrule_clears_head_and_names_who_decided_and_why(lverify, ctx, repo_with_change):
    ctx.llm.parsed = {"summary": "Broken.", "findings": [BUG]}
    lverify("review repo main")
    reply = lverify("overrule repo 1 subtraction is what the spec asks for", platform="discord")
    sha = repo.head_sha(repo_with_change)
    assert reply.startswith(f"{sha[:10]} is cleared: no blocking findings left.")
    assert "Overruled finding 1: add subtracts" in reply
    assert ": subtraction is what the spec asks for" in reply
    assert "By you on discord at " in reply


def test_overrule_in_the_session_dir_needs_no_repo_argument(lverify, ctx, repo_with_change):
    ctx.cwd = str(repo_with_change)
    ctx.llm.parsed = {"summary": "Broken.", "findings": [BUG, {**BUG, "title": "second"}]}
    lverify("review . main")
    reply = lverify("overrule 2 not in this chunk")
    assert "is still blocked by 1 finding(s)" in reply


@pytest.mark.parametrize("args", ["", "repo", "repo 1", "repo one reason", "1"])
def test_overrule_without_a_finding_and_a_reason_shows_usage(lverify, args):
    assert lverify(f"overrule {args}") == f"Usage: {commands.OVERRULE_USAGE}"


def test_overrule_before_any_review_explains_what_to_do(lverify, repo_with_change):
    reply = lverify("overrule repo 1 reason")
    assert reply.startswith("Overrule failed:")
    assert "run /lverify review first" in reply


def test_verify_runs_first_in_the_sandbox_of_the_session(lverify, ctx, repo_with_change):
    lverify("review repo main", session_id="s1")
    (call,) = ctx.dispatched
    assert (call["workdir"], call["task_id"]) == (str(repo_with_change), "s1")
    assert "lverify verify --json" in call["command"]
    assert "VERIFY OUTPUT\nPASS (lint pass)" in [b["text"] for b in ctx.llm.calls[0]["input"]]


def test_failing_verify_stops_before_the_reviewer_and_saves_nothing(lverify, ctx, repo_with_change):
    ctx.verify_passes = False
    reply = lverify("review repo main")
    assert reply.startswith("Review not run: verify failed for ")
    assert "lint FAIL" in reply
    assert ctx.llm.calls == []
    assert not (repo_with_change / ".git" / "lverify" / "reviews").exists()


def test_uncommitted_changes_stop_the_review(lverify, ctx, repo_with_change):
    ctx.verify_clean = False
    assert lverify("review repo main").startswith(
        "Review not run: the working tree has uncommitted"
    )
    assert ctx.llm.calls == []


def test_verify_that_cannot_run_is_reported(lverify, ctx, repo_with_change, monkeypatch):
    monkeypatch.setattr(
        ctx,
        "dispatch_tool",
        lambda *a, **k: json.dumps({"output": "uvx: not found", "exit_code": 127}),
    )
    reply = lverify("review repo main")
    assert reply.startswith("Review not run: verify could not run:")
    assert "uvx: not found" in reply


def test_no_sandbox_means_no_review(ctx, log, tmp_path, repo_with_change):
    lverify = commands.make_handler(ctx, log, tmp_path / "data", env_for=lambda _: None)
    assert "cannot tell where" in lverify("review repo main")
    assert ctx.llm.calls == []


def test_a_sign_off_keeps_the_verify_result_and_gives_the_pr_body_section(
    lverify, ctx, repo_with_change
):
    reply = lverify("review repo main")
    sha = repo.head_sha(repo_with_change)
    saved = json.loads((repo_with_change / ".git/lverify/reviews" / f"{sha}.json").read_text())
    assert (saved["verify"]["passed"], saved["verify"]["sha"]) == (True, sha)
    assert (
        f"Put this section in the PR body:\n\n## Review sign-off\n\nlverify-signoff: {sha}" in reply
    )
