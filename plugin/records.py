"""Review records and the human's overrules, kept per commit in `<git dir>/lverify/reviews`.

The git dir is shared with the sandbox, so an agent there could write a record that looks like a
sign-off or an overrule. Every record is therefore sealed with an HMAC whose key lives only in
the plugin's data dir on the host, which the sandbox does not mount. A record whose seal does not
check out is ignored.
"""

import hashlib
import hmac
import json
import os
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import repo, reviewer

KEY_FILE = "seal.key"
SEAL_FIELD = "seal"


class RecordError(Exception):
    """A record is missing, unsealed or does not allow the request; safe to show the user."""


@dataclass(frozen=True)
class Overrule:
    number: int
    title: str
    reason: str
    by: str
    at: str


@dataclass(frozen=True)
class Standing:
    """Where HEAD stands: its review, the findings the human overruled, and what is still open."""

    review: dict[str, Any]
    overrules: tuple[Overrule, ...]
    open_findings: tuple[reviewer.Finding, ...]

    @property
    def cleared(self) -> bool:
        return not self.open_findings

    @property
    def verify_passed(self) -> bool:
        """The plugin's own verify run for this commit passed on a clean tree."""
        verify = self.review.get("verify") or {}
        return (
            verify.get("passed") is True
            and verify.get("clean") is True
            and verify.get("sha") == self.review["sha"]
        )

    def signoff_section(self) -> str:
        """The PR body section for this commit; the gate looks for its `lverify-signoff` line."""
        review, verify = self.review, self.review.get("verify") or {}
        outcome = (
            "SIGN-OFF" if review["signed_off"] else "REJECTED, every blocking finding overruled"
        )
        checks = ", ".join(
            f"{c['name']} {'pass' if c['passed'] else 'FAIL'}" for c in verify.get("checks", [])
        )
        lines = [
            "## Review sign-off",
            "",
            f"lverify-signoff: {review['sha']}",
            "",
            f"- Verify: {'PASS' if self.verify_passed else 'FAIL'} ({checks})",
            f"- Reviewer: {review['provider']}:{review['model']}, round {review.get('round', 1)},"
            f" against {review['base']}",
            f"- Verdict: {outcome}, {len(review['findings'])} blocking finding(s),"
            f" {len(review['notes'])} note(s), {review['seconds']:.0f} s,"
            f" {review['input_tokens']:,} tokens in, {review['output_tokens']:,} out",
            f"- Reviewer's summary: {review['summary']}",
        ]
        lines += [
            f"- Overruled finding {o.number} ({o.title}) by {o.by} at {o.at}: {o.reason}"
            for o in self.overrules
        ]
        return "\n".join(lines)


def load_key(data_dir: Path) -> bytes:
    """The host-only sealing key, created on first use."""
    path = data_dir / KEY_FILE
    if not path.exists():
        data_dir.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(secrets.token_bytes(32))
    return path.read_bytes()


class Store:
    def __init__(self, target: Path, key: bytes) -> None:
        self.target = target
        self.key = key
        self.dir = repo.lverify_dir(target) / "reviews"

    def save_review(self, record: dict[str, Any]) -> Path:
        return self._write(self.dir / f"{record['sha']}.json", record)

    def save_failed_reply(self, sha: str, raw: str) -> Path:
        path = self.dir / f"{sha}.failed.txt"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(raw, encoding="utf-8")
        return path

    def review(self, sha: str) -> dict[str, Any] | None:
        return self._read(self.dir / f"{sha}.json")

    def overrules(self, sha: str) -> tuple[Overrule, ...]:
        record = self._read(self.dir / f"{sha}.overrules.json")
        return tuple(Overrule(**item) for item in record["overrules"]) if record else ()

    def standing(self, sha: str) -> Standing:
        review = self.review(sha)
        if review is None:
            raise RecordError(f"{sha[:10]} has no sealed review: run /lverify review first")
        overrules = self.overrules(sha)
        overruled = {item.number for item in overrules}
        findings = _findings(review)
        open_findings = tuple(f for n, f in enumerate(findings, 1) if n not in overruled)
        return Standing(review=review, overrules=overrules, open_findings=open_findings)

    def overrule(self, sha: str, numbers: list[int], reason: str, by: str) -> Standing:
        """Record the human's decision that blocking findings `numbers` of HEAD's review do not
        block, with the reason that goes into the PR body."""
        if not reason.strip():
            raise RecordError("give a reason: it goes into the PR body next to the finding")
        findings = _findings(self.standing(sha).review)
        unknown = [n for n in numbers if not 1 <= n <= len(findings)]
        if unknown:
            raise RecordError(
                f"no blocking finding {', '.join(map(str, unknown))} in the review of "
                f"{sha[:10]}; it has {len(findings)}"
            )
        at = datetime.now(UTC).isoformat(timespec="seconds")
        kept = [item for item in self.overrules(sha) if item.number not in numbers]
        added = [
            Overrule(number=n, title=findings[n - 1].title, reason=reason.strip(), by=by, at=at)
            for n in numbers
        ]
        overrules = sorted([*kept, *added], key=lambda item: item.number)
        record = {"sha": sha, "overrules": [item.__dict__ for item in overrules]}
        self._write(self.dir / f"{sha}.overrules.json", record)
        return self.standing(sha)

    def pr_branches(self) -> frozenset[str]:
        """Branches the gate let a PR through for; pushes to them are gated too."""
        record = self._read(self.dir.parent / "pr-branches.json")
        return frozenset(record["branches"]) if record else frozenset()

    def add_pr_branch(self, branch: str) -> None:
        branches = sorted(self.pr_branches() | {branch})
        self._write(self.dir.parent / "pr-branches.json", {"branches": branches})

    def previous_round(self, sha: str, base: str) -> reviewer.Previous | None:
        """The newest rejected review of an ancestor of HEAD against the same base, if any,
        without the findings the human overruled. A rebase breaks the ancestry, so the next
        review starts again at round 1."""
        candidates = []
        for path in sorted(self.dir.glob("*.json")) if self.dir.is_dir() else ():
            record = self._read(path)
            if record is None or "summary" not in record:
                continue
            if record.get("signed_off") or record.get("base") != base or record["sha"] == sha:
                continue
            if repo.is_ancestor(self.target, record["sha"], sha):
                distance = repo.commits_between(self.target, record["sha"], sha)
                candidates.append((distance, record))
        if not candidates:
            return None
        _, record = min(candidates, key=lambda candidate: candidate[0])
        overrules = self.overrules(record["sha"])
        changes = repo.diff_between(self.target, record["sha"], sha)
        return reviewer.previous_from_record(
            record,
            changes,
            overruled={item.number: item.reason for item in overrules},
        )

    def _write(self, path: Path, record: dict[str, Any]) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        sealed = {**record, SEAL_FIELD: self._seal(record)}
        path.write_text(json.dumps(sealed, indent=2), encoding="utf-8")
        return path

    def _read(self, path: Path) -> dict[str, Any] | None:
        if not path.exists():
            return None
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return None
        if not isinstance(record, dict):
            return None
        seal = record.pop(SEAL_FIELD, None)
        if not isinstance(seal, str) or not hmac.compare_digest(seal, self._seal(record)):
            return None
        return record

    def _seal(self, record: dict[str, Any]) -> str:
        body = json.dumps(record, sort_keys=True, separators=(",", ":")).encode()
        return hmac.new(self.key, body, hashlib.sha256).hexdigest()


def render_overrules(overrules: tuple[Overrule, ...]) -> str:
    return "\n\n".join(
        f"Overruled finding {item.number}: {item.title}\n"
        f"{reviewer.INDENT}By {item.by} at {item.at}: {item.reason}"
        for item in overrules
    )


def _findings(review: dict[str, Any]) -> tuple[reviewer.Finding, ...]:
    return reviewer.previous_from_record(review, "").findings
