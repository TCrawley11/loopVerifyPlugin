"""The reviewer call as Hermes makes it: trust gate, then an OpenRouter request for Gemini."""

from plugin import repo

GRANT_LIKE_THE_CONSENT_SCREEN = """
from hermes_cli.plugin_capabilities import record_consent

capabilities = ["llm.provider_override", "llm.model_override"]
record_consent("loopverify", capabilities, capabilities)
"""

REVIEW_WITH_PROVIDER_CALL_RECORDED = """
from types import SimpleNamespace
import agent.auxiliary_client as auxiliary_client

sent = {{}}

def provider_call(**kwargs):
    sent.update(kwargs)
    verdict = json.dumps({{"summary": "Reviewed.", "findings": []}})
    message = SimpleNamespace(content=verdict)
    return SimpleNamespace(model=kwargs["model"], choices=[SimpleNamespace(message=message)])

auxiliary_client.call_llm = provider_call
reply = get_plugin_command_handler("lverify")("review {repo} main")
emit({{
    "reply": reply,
    "provider": sent.get("provider"),
    "model": sent.get("model"),
    "response_format": (sent.get("extra_body") or {{}}).get("response_format", {{}}).get("type"),
    "system": sent["messages"][0]["content"] if sent else None,
    "user_blocks": [part["text"] for part in sent["messages"][1]["content"]] if sent else None,
}})
"""


def test_without_consent_hermes_refuses_the_model_override(in_hermes, repo_with_change):
    result = in_hermes(REVIEW_WITH_PROVIDER_CALL_RECORDED.format(repo=repo_with_change))
    assert result["reply"].startswith("Review not allowed:")
    assert "hermes plugins enable loopverify" in result["reply"]
    assert result["provider"] is None


def test_after_consent_the_review_goes_to_gemini_4_carbon_on_openrouter(
    in_hermes, repo_with_change
):
    result = in_hermes(
        GRANT_LIKE_THE_CONSENT_SCREEN
        + REVIEW_WITH_PROVIDER_CALL_RECORDED.format(repo=repo_with_change)
    )
    sha = repo.head_sha(repo_with_change)
    assert result["reply"].startswith(
        f"Review of {sha} against main\nSIGN-OFF by openrouter:google/gemini-4-carbon"
    )
    assert result["provider"] == "openrouter"
    assert result["model"] == "google/gemini-4-carbon"
    assert result["response_format"] == "json_schema"
    assert "find what is wrong" in result["system"]


def test_the_request_carries_the_diff_and_nothing_from_the_session(in_hermes, repo_with_change):
    result = in_hermes(
        GRANT_LIKE_THE_CONSENT_SCREEN
        + REVIEW_WITH_PROVIDER_CALL_RECORDED.format(repo=repo_with_change)
    )
    instructions, spec, verify_output, diff = result["user_blocks"]
    assert instructions.startswith("Report each problem as a finding")
    assert spec == "CHUNK SPEC\n(none provided)"
    assert verify_output == "VERIFY OUTPUT\n(none provided)"
    assert diff.startswith("DIFF\ndiff --git a/x.py b/x.py")
    assert "+    return a - b" in diff
