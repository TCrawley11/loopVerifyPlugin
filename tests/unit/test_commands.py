import json
import subprocess

import pytest

from plugin import commands, repo


@pytest.fixture
def lverify(ctx, log, tmp_path):
    return commands.make_handler(ctx, log, tmp_path / "data")


@pytest.mark.parametrize("raw_args", ["", "  ", "help", "nope extra"])
def test_unknown_subcommand_shows_usage_for_every_subcommand(lverify, raw_args):
    reply = lverify(raw_args)
    assert reply.startswith("Usage:")
    for name in ("report", "review", "p0"):
        assert f"/lverify {name}" in reply


def test_review_sends_only_the_committed_diff_and_reports_the_sha(lverify, ctx, repo_with_change):
    reply = lverify("review repo main", platform="cli")
    sha = repo.head_sha(repo_with_change)
    header, blank, outcome = reply.splitlines()[:3]
    assert header.startswith(f"Review of {sha[:10]} against main (")
    assert header.endswith(" s, 1,200 tokens in, 300 out)")
    assert (blank, outcome) == (
        "",
        "SIGN-OFF by openrouter:google/gemini-4-carbon (0 blocking findings, 0 notes)",
    )


def test_every_verdict_is_saved_per_commit_in_the_repos_git_dir(lverify, repo_with_change):
    reply = lverify("review repo main")
    sha = repo.head_sha(repo_with_change)
    record = repo_with_change / ".git" / "lverify" / "reviews" / f"{sha}.json"
    assert reply.endswith(f"Saved to {record}")
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
        "findings": [{"title": "add subtracts", "detail": "x.py:2", "repro": "pytest -k add"}],
    }
    reply = lverify("review repo main")
    assert "REJECTED by openrouter:google/gemini-4-carbon (1 blocking finding, 0 notes)" in reply
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
    saved = tmp_path / "data" / commands.LAST_REPLY_FILE
    assert saved.read_text() == ctx.llm.text
    assert f"Full reply: {saved}" in reply
    assert ("Looks fine to me. " * 40)[: commands.EXCERPT_CHARS] in reply


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
    first, last = ctx.posts
    assert first.startswith("loopverify: openrouter:google/gemini-4-carbon is reviewing ")
    assert last.startswith("loopverify: review of ") and " signed off in " in last


def test_review_rejects_extra_arguments(lverify):
    assert lverify("review a b c") == "Usage: /lverify review [repo] [base]"
