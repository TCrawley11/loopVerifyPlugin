from types import SimpleNamespace

import pytest

from plugin import reviewer

DIFF = "diff --git a/x.py b/x.py\n+def add(a, b):\n+    return a - b\n"
BUG = {"title": "add subtracts", "detail": "x.py:2 returns a - b", "repro": "pytest -k add"}
NIT = {"title": "name is vague", "detail": "x.py:1", "repro": ""}


class FakeLlm:
    def __init__(self, parsed, provider=reviewer.PROVIDER, model=reviewer.MODEL, error=None):
        self.result = SimpleNamespace(provider=provider, model=model, parsed=parsed)
        self.error = error
        self.calls = []

    def complete_structured(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.result


def verdict(*findings):
    return {"summary": "Reviewed.", "findings": list(findings)}


def run(llm, inp=None):
    return reviewer.review(llm, inp or reviewer.ReviewInput(diff=DIFF), lambda text: text)


def test_calls_gemini_4_carbon_through_openrouter():
    llm = FakeLlm(verdict())
    run(llm)
    (call,) = llm.calls
    assert call["provider"] == "openrouter"
    assert call["model"] == "google/gemini-4-carbon"
    assert call["json_schema"] == reviewer.VERDICT_SCHEMA


def test_reviewer_sees_only_spec_verify_output_and_diff():
    llm = FakeLlm(verdict())
    run(llm, reviewer.ReviewInput(diff=DIFF, spec="add two ints", verify_output="3 passed"))
    texts = [block["text"] for block in llm.calls[0]["input"]]
    assert texts == ["CHUNK SPEC\nadd two ints", "VERIFY OUTPUT\n3 passed", f"DIFF\n{DIFF}"]


def test_brief_is_adversarial():
    llm = FakeLlm(verdict())
    run(llm)
    assert "find what is wrong" in llm.calls[0]["system_prompt"]
    assert "not to confirm that it works" in llm.calls[0]["system_prompt"]


def test_every_block_is_redacted_before_it_leaves_the_machine():
    llm = FakeLlm(verdict())
    inp = reviewer.ReviewInput(diff="KEY=sk-1", spec="KEY=sk-2", verify_output="KEY=sk-3")
    reviewer.review(llm, inp, lambda text: text.replace("sk-", "REDACTED-"))
    assert all("sk-" not in block["text"] for block in llm.calls[0]["input"])


def test_no_findings_is_a_sign_off():
    result = run(FakeLlm(verdict()))
    assert result.signed_off
    assert (result.provider, result.model) == ("openrouter", "google/gemini-4-carbon")


def test_a_finding_with_a_repro_blocks():
    result = run(FakeLlm(verdict(BUG)))
    assert not result.signed_off
    assert [finding.repro for finding in result.findings] == ["pytest -k add"]


def test_a_finding_without_a_repro_is_only_a_note():
    result = run(FakeLlm(verdict(NIT, {**BUG, "repro": "   "})))
    assert result.signed_off
    assert result.findings == ()
    assert [note.title for note in result.notes] == ["name is vague", "add subtracts"]


@pytest.mark.parametrize(
    "parsed",
    [
        None,
        "looks good to me",
        {"summary": "ok"},
        {"findings": []},
        {"summary": "ok", "findings": "none"},
        {"summary": "ok", "findings": ["add subtracts"]},
        {"summary": "ok", "findings": [{"title": "t", "detail": "d"}]},
        {"summary": "ok", "findings": [{"title": "t", "detail": "d", "repro": None}]},
    ],
)
def test_a_malformed_verdict_is_an_error_never_a_sign_off(parsed):
    with pytest.raises(reviewer.ReviewError):
        run(FakeLlm(parsed))


def test_a_schema_violation_reported_by_hermes_is_an_error():
    with pytest.raises(reviewer.ReviewError, match="malformed verdict"):
        run(FakeLlm(None, error=ValueError("did not match schema")))


@pytest.mark.parametrize(
    ("provider", "model"),
    [("anthropic", "claude-fable-5-2"), ("openrouter", "anthropic/claude-fable-5-2")],
)
def test_a_verdict_from_another_model_is_rejected(provider, model):
    with pytest.raises(reviewer.ReviewError, match="expected openrouter:google/gemini-4-carbon"):
        run(FakeLlm(verdict(), provider=provider, model=model))


def test_a_trust_denial_propagates_so_the_command_can_explain_it():
    with pytest.raises(PermissionError):
        run(FakeLlm(None, error=PermissionError("cannot override the provider")))


@pytest.mark.parametrize("diff", ["", "  \n", "x" * (reviewer.MAX_DIFF_CHARS + 1)])
def test_an_empty_or_oversized_diff_is_refused_without_calling_the_model(diff):
    llm = FakeLlm(verdict())
    with pytest.raises(reviewer.ReviewError):
        run(llm, reviewer.ReviewInput(diff=diff))
    assert llm.calls == []


def test_render_names_the_outcome_the_model_and_each_repro():
    signed = reviewer.render(run(FakeLlm(verdict(NIT))))
    rejected = reviewer.render(run(FakeLlm(verdict(BUG))))
    assert signed.startswith("SIGN-OFF by openrouter:google/gemini-4-carbon")
    assert "[note] name is vague" in signed
    assert rejected.startswith(
        "REJECTED, blocking findings: 1, by openrouter:google/gemini-4-carbon"
    )
    assert "repro: pytest -k add" in rejected
