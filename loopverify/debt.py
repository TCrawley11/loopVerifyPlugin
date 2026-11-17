"""The debt check: workaround markers need a dated justification, comment blocks stay short.

A marker (`# noqa`, `# type: ignore`, TODO, a swallowed exception, `time.sleep`) is allowed when
a comment on its line or the comment line right above it says

    # debt(2026-12-31): why this is needed, in at least three words

with a date no further ahead than the configured horizon. The check runs at HEAD and at the
merge base like any differential check, so only debt a change adds fails. Expired markers are
the exception: base and HEAD both have them, so the change pays or renews those in files it
touches, and the rest are listed without failing.

Languages plug in as adapters; only Python exists so far.
"""

import os
import re
import subprocess
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Protocol

from .ratchet import Finding

JUSTIFICATION = re.compile(r"\bdebt\((?P<date>[^)]*)\):?(?P<reason>.*)")
MIN_REASON_WORDS = 3
SKIPPED_DIRS = frozenset({".git", ".venv", "venv", "node_modules", "__pycache__", "build", "dist"})

MARKER = "debt-marker"
INVALID = "debt-justification"
EXPIRED = "debt-expired"
COMMENT = "debt-comment"


@dataclass(frozen=True)
class DebtRules:
    today: date
    comment_max_lines: int = 3
    expiry_max_days: int = 90


@dataclass(frozen=True)
class Marker:
    """A workaround at one line, with the comment text that may justify it."""

    kind: str
    line: int
    source: str
    justification: str | None


class Adapter(Protocol):
    @property
    def suffixes(self) -> tuple[str, ...]: ...

    def scan(self, path: str, text: str, rules: DebtRules) -> list[Finding]: ...


def judge(justification: str | None, rules: DebtRules) -> tuple[str, str] | None:
    """None when `justification` makes a marker acceptable, else (finding code, why not)."""
    if justification is None:
        return MARKER, "needs `# debt(YYYY-MM-DD): reason`"
    match = JUSTIFICATION.search(justification)
    if match is None:
        return MARKER, "needs `# debt(YYYY-MM-DD): reason`"
    try:
        until = date.fromisoformat(match["date"].strip())
    except ValueError:
        return INVALID, f"`{match['date']}` is not a YYYY-MM-DD date"
    reason = match["reason"].strip()
    if len(reason.split()) < MIN_REASON_WORDS:
        return INVALID, f"give a reason of at least {MIN_REASON_WORDS} words"
    if until < rules.today:
        return EXPIRED, f"expired on {until.isoformat()}: pay it off or renew it"
    if (until - rules.today).days > rules.expiry_max_days:
        return INVALID, f"expires more than {rules.expiry_max_days} days ahead"
    return None


def marker_findings(
    check: str, path: str, markers: Iterable[Marker], rules: DebtRules
) -> list[Finding]:
    findings = []
    for marker in markers:
        verdict = judge(marker.justification, rules)
        if verdict is not None:
            code, why = verdict
            message = f"{marker.kind} {why}: {marker.source}"
            findings.append(Finding(check, path, code, message, marker.line))
    return findings


def scan_tree(check: str, root: Path, cwd: str, rules: DebtRules) -> list[Finding]:
    """Debt findings for every file an adapter handles under `root / cwd`."""
    from . import debt_python

    adapters: tuple[Adapter, ...] = (debt_python.PythonAdapter(check),)
    findings: list[Finding] = []
    for path in _files(root, cwd):
        adapter = next((a for a in adapters if path.endswith(a.suffixes)), None)
        if adapter is None:
            continue
        try:
            text = (root / path).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        findings.extend(adapter.scan(path, text, rules))
    return findings


def _files(root: Path, cwd: str) -> Iterator[str]:
    """Paths relative to `root`: the files git would commit in a checkout, every file in an
    extracted tree (which holds only committed files)."""
    if (root / ".git").exists():
        listing = subprocess.run(
            ["git", "-C", str(root), "ls-files", "-z", "-co", "--exclude-standard", "--", cwd],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        yield from sorted(path for path in listing.split("\0") if path)
        return
    start = root / cwd
    for folder, dirs, files in os.walk(start):
        dirs[:] = sorted(d for d in dirs if d not in SKIPPED_DIRS)
        for name in sorted(files):
            yield (Path(folder) / name).relative_to(root).as_posix()
