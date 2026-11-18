"""The `lverify_review` tool: the agent asks for verify plus an independent review of HEAD.

As a tool, Hermes shows its normal progress line while it runs, on every surface.
"""

import json
import shlex
from collections.abc import Callable
from pathlib import Path
from typing import Any

from . import locate, records, review_flow

NAME = "lverify_review"
TOOLSET = "loopverify"
SCHEMA: dict[str, Any] = {
    "name": NAME,
    "description": (
        "Run lverify verify on HEAD of a git repo, then an independent review by another model. "
        "Needed before `gh pr create`, or a push to a branch with a PR, in repos connected to "
        "lverify. Commit everything first. On sign-off it returns the section to put in the PR "
        "body. On rejection, fix the blocking findings or ask the user; never overrule them "
        "yourself."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "repo": {
                "type": "string",
                "description": "The repo folder as the terminal sees it, e.g. /workspace/ap1. "
                "Default: the terminal's current folder.",
            },
            "base": {
                "type": "string",
                "description": "The branch the change is compared against. Default: origin/HEAD.",
            },
        },
    },
}


def make_handler(
    ctx: Any,
    store: Callable[[Path], records.Store],
    verify_command: str,
    env_for: Callable[[str | None], Any],
) -> Callable[..., str]:
    def handle(args: dict[str, Any], task_id: str | None = None, **_: Any) -> str:
        env = env_for(task_id)
        repo = args.get("repo") or "."
        where = locate.place(f"cd {shlex.quote(repo)} && true", None, env)
        if where is None:
            return _reply(
                "Review not run: cannot map this repo folder to the host. Pass `repo` as the "
                "folder the terminal sees, under the mounted workspace."
            )
        request = review_flow.Request(
            host=Path(where.host),
            sandbox=where.sandbox,
            base=args.get("base") or review_flow.DEFAULT_BASE,
            task_id=task_id,
            platform="",
        )
        return _reply(review_flow.run(ctx, request, store, verify_command))

    return handle


def _reply(text: str) -> str:
    return json.dumps({"report": text})
