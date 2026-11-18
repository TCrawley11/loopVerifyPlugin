"""The gate's policy. `gate.decide` is the core of P1.3; these tests define it."""

import pytest

from plugin import gate, records, reviewer

SHA = "a" * 40
BUG = reviewer.Finding("add subtracts", "x.py:2", "pytest -k add")
BODY = f"Adds a thing.\n\n## Review sign-off\n\nlverify-signoff: {SHA}\n"


def standing(*, verify=True, clean=True, open_findings=(), overrules=(), sha=SHA):
    review = {
        "sha": SHA,
        "verify": {"passed": verify, "clean": clean, "sha": sha, "checks": []},
    }
    return records.Standing(review=review, overrules=overrules, open_findings=open_findings)


CLEARED = standing()


def push(*, has_pr=False, guarded=False, paths=()):
    return gate.Push("feat", SHA, has_pr, guarded, paths)


def blocked(action, state):
    return gate.decide(action, state) is not None


def test_a_signed_off_verified_commit_with_the_line_in_the_body_may_open_a_pr():
    assert gate.decide(gate.PrCreate(SHA, BODY, "feat"), CLEARED) is None


def test_all_findings_overruled_counts_as_cleared():
    overruled = records.Overrule(1, "add subtracts", "intended", "you on cli", "now")
    assert gate.decide(gate.PrCreate(SHA, BODY), standing(overrules=(overruled,))) is None


@pytest.mark.parametrize(
    ("state", "says"),
    [
        (None, "lverify_review"),
        (standing(verify=False), "verify"),
        (standing(clean=False), "verify"),
        (standing(sha="b" * 40), "verify"),
        (standing(open_findings=(BUG,)), "add subtracts"),
    ],
)
def test_a_pr_needs_a_verified_cleared_record_for_its_commit(state, says):
    reason = gate.decide(gate.PrCreate(SHA, BODY), state)
    assert reason is not None
    assert says in reason


@pytest.mark.parametrize(
    ("body", "says"),
    [
        (None, "--body"),
        ("Adds a thing.", f"lverify-signoff: {SHA}"),
        ("lverify-signoff: " + "b" * 40, f"lverify-signoff: {SHA}"),
    ],
)
def test_a_pr_body_must_carry_the_sign_off_line_for_its_commit(body, says):
    reason = gate.decide(gate.PrCreate(SHA, body), CLEARED)
    assert reason is not None
    assert says in reason


def test_a_push_to_a_branch_without_a_pr_is_free():
    assert gate.decide(push(), None) is None


def test_a_push_to_a_pr_branch_needs_the_same_record_as_a_pr():
    assert blocked(push(has_pr=True), None)
    assert blocked(push(has_pr=True), standing(open_findings=(BUG,)))
    assert blocked(push(has_pr=True), standing(verify=False))
    assert gate.decide(push(has_pr=True), CLEARED) is None


def test_a_guarded_repo_never_pushes_lverify_files():
    reason = gate.decide(push(guarded=True, paths=(".lverify/DECISIONS.md",)), CLEARED)
    assert reason is not None
    assert ".lverify/DECISIONS.md" in reason
    assert gate.decide(push(guarded=True), None) is None
    assert gate.decide(push(guarded=False, paths=("lverify.toml",)), None) is None


def test_an_unreadable_command_is_blocked_with_how_to_write_it():
    reason = gate.decide(gate.Unreadable("bash -c 'gh pr create'"), CLEARED)
    assert reason is not None
    assert "plain" in reason
