import json
import stat

import pytest

from plugin import records, reviewer

from .test_commands import commit

BUG = {
    "title": "add subtracts",
    "detail": "x.py:2",
    "repro": "pytest -k add",
    "impact": "breaks-intended-use",
    "scope": "new-or-changed",
}
KEY = b"k" * 32


def rejected(sha, *findings, base="main"):
    verdict = reviewer.Review(
        provider=reviewer.PROVIDER,
        model=reviewer.MODEL,
        summary="Broken.",
        findings=tuple(reviewer.Finding(**f) for f in findings),
        notes=(),
    )
    return reviewer.to_record(verdict, sha, base, 1.0)


@pytest.fixture
def store(repo_with_change):
    return records.Store(repo_with_change, KEY)


@pytest.fixture
def sha(repo_with_change):
    from plugin import repo

    return repo.head_sha(repo_with_change)


def test_the_key_is_created_once_and_readable_only_by_the_owner(tmp_path):
    key = records.load_key(tmp_path / "data")
    assert len(key) == 32
    assert records.load_key(tmp_path / "data") == key
    mode = stat.S_IMODE((tmp_path / "data" / records.KEY_FILE).stat().st_mode)
    assert mode == 0o600


def test_a_sealed_review_reads_back(store, sha):
    store.save_review(rejected(sha, BUG))
    assert store.review(sha)["findings"][0]["title"] == "add subtracts"


@pytest.mark.parametrize("forge", ["edit", "unsealed", "other key"])
def test_a_record_the_host_did_not_seal_is_ignored(store, sha, repo_with_change, forge):
    path = store.save_review(rejected(sha, BUG))
    if forge == "edit":
        record = json.loads(path.read_text())
        record["signed_off"], record["findings"] = True, []
        path.write_text(json.dumps(record))
    elif forge == "unsealed":
        path.write_text(json.dumps({**rejected(sha), "signed_off": True}))
    else:
        records.Store(repo_with_change, b"x" * 32).save_review(
            {**rejected(sha), "signed_off": True}
        )
    assert store.review(sha) is None


def test_overruling_every_blocking_finding_clears_head(store, sha):
    store.save_review(rejected(sha, BUG, {**BUG, "title": "second"}))
    standing = store.overrule(sha, [1], "intended: subtraction is the spec", "you on cli")
    assert not standing.cleared
    assert [f.title for f in standing.open_findings] == ["second"]
    standing = store.overrule(sha, [2], "out of scope for this chunk", "you on cli")
    assert standing.cleared
    assert [(o.number, o.title, o.reason) for o in standing.overrules] == [
        (1, "add subtracts", "intended: subtraction is the spec"),
        (2, "second", "out of scope for this chunk"),
    ]


def test_overruling_again_replaces_the_reason(store, sha):
    store.save_review(rejected(sha, BUG))
    store.overrule(sha, [1], "first reason", "you on cli")
    (only,) = store.overrule(sha, [1], "better reason", "you on cli").overrules
    assert only.reason == "better reason"


@pytest.mark.parametrize(
    ("numbers", "reason", "message"),
    [
        ([2], "r", "no blocking finding 2"),
        ([0], "r", "no blocking finding 0"),
        ([1], " ", "reason"),
    ],
)
def test_an_overrule_needs_a_real_finding_and_a_reason(store, sha, numbers, reason, message):
    store.save_review(rejected(sha, BUG))
    with pytest.raises(records.RecordError, match=message):
        store.overrule(sha, numbers, reason, "you on cli")


def test_an_overrule_needs_a_sealed_review_of_head(store, sha):
    with pytest.raises(records.RecordError, match="no sealed review"):
        store.overrule(sha, [1], "r", "you on cli")


def test_a_forged_overrule_does_not_clear_head(store, sha):
    store.save_review(rejected(sha, BUG))
    forged = {
        "sha": sha,
        "overrules": [{"number": 1, "title": "t", "reason": "r", "by": "agent", "at": "now"}],
    }
    (store.dir / f"{sha}.overrules.json").write_text(json.dumps(forged))
    assert not store.standing(sha).cleared


def test_the_next_round_gets_overruled_findings_as_decided_not_open(store, sha, repo_with_change):
    store.save_review(rejected(sha, BUG, {**BUG, "title": "second"}))
    store.overrule(sha, [1], "intended", "you on cli")
    commit(repo_with_change, "y.py", "Y = 1\n")
    previous = store.previous_round(store_head(repo_with_change), "main")
    assert [f.title for f in previous.findings] == ["second"]
    assert [(f.title, reason) for f, reason in previous.overruled] == [
        ("add subtracts", "intended")
    ]


def test_a_forged_rejection_does_not_start_a_round(store, sha, repo_with_change):
    (store.dir).mkdir(parents=True)
    (store.dir / f"{sha}.json").write_text(json.dumps(rejected(sha, BUG)))
    commit(repo_with_change, "y.py", "Y = 1\n")
    assert store.previous_round(store_head(repo_with_change), "main") is None


def store_head(path):
    from plugin import repo

    return repo.head_sha(path)
