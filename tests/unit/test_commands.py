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
    assert reply.startswith(f"Review of {sha} against main\nSIGN-OFF")
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
    assert "REJECTED, blocking findings: 1," in reply
    assert "repro: pytest -k add" in reply


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


def test_review_rejects_extra_arguments(lverify):
    assert lverify("review a b c") == "Usage: /lverify review [repo] [base]"
