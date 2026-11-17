"""Which findings a change introduces: the policy at the heart of `lverify verify`."""

from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

# A file that does not parse hides every other finding in it, so a parse error is never
# accepted as pre-existing, even when the base has the same one.
ALWAYS_NEW_CODES = frozenset({"syntax", "invalid-syntax"})


@dataclass(frozen=True)
class Finding:
    """One problem a tool reported. Equality ignores `line`, so an edit above a finding
    does not turn it into a new one."""

    check: str
    path: str
    code: str
    message: str
    line: int = field(default=0, compare=False)

    def describe(self) -> str:
        return f"{self.path}:{self.line}: {self.code} {self.message}"


def new_findings(head: Sequence[Finding], base: Iterable[Finding]) -> list[Finding]:
    """The findings in `head` that `base` does not account for, in `head` order.

    Each finding in `base` excuses one equal finding in `head`. A change that adds a second
    copy of an existing problem therefore still fails.
    """
    allowance = Counter(base)
    introduced = []
    for finding in head:
        if finding.code in ALWAYS_NEW_CODES or allowance[finding] == 0:
            introduced.append(finding)
        else:
            allowance[finding] -= 1
    return introduced


def gone_count(head: Iterable[Finding], base: Iterable[Finding]) -> int:
    """How many base findings the change removed."""
    return sum((Counter(base) - Counter(head)).values())
