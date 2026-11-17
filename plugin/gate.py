"""PR gate: decides whether a tool call may run.

P0 scope: prove that a pre_tool_call hook can block `gh pr create`. The gate blocks only
when the command carries PROBE_MARKER, so enabling the plugin cannot stop a real PR before
the real condition (verify stamp and reviewer sign-off for HEAD) lands in P1.
"""

import re
from typing import Any

PROBE_MARKER = "LVERIFY_P0_PROBE"
BLOCK_MESSAGE = (
    "loopverify gate: blocked `gh pr create` (P0 probe). "
    "From P1 this needs a verify stamp and a reviewer sign-off for HEAD."
)

_PR_CREATE = re.compile(r"\bgh\s+pr\s+create\b")


def opens_pr(tool_name: str, args: Any) -> bool:
    """True when the tool call is a terminal command that runs `gh pr create`."""
    if tool_name != "terminal" or not isinstance(args, dict):
        return False
    command = args.get("command")
    return isinstance(command, str) and bool(_PR_CREATE.search(command))


def decide(tool_name: str, args: Any) -> dict[str, str] | None:
    """The pre_tool_call directive for one tool call: a block, or None to let it run."""
    if opens_pr(tool_name, args) and PROBE_MARKER in args["command"]:
        return {"action": "block", "message": BLOCK_MESSAGE}
    return None
