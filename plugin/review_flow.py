"""One review of HEAD, shared by the `lverify_review` tool and `/lverify review`.

Deterministic first: the plugin runs verify in the session's sandbox, and only a pass on a
clean tree goes to the reviewer. The verify result and the verdict are sealed into one record
per commit, which is what the PR gate reads.
"""

import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import records, repo, reviewer, verify_run

DEFAULT_BASE = "origin/HEAD"
EXCERPT_CHARS = 300
OVERRULE_USAGE = "/lverify overrule [repo] <finding>[,<finding>...] <reason>"
# The classic CLI prints a plugin's live message only after the next turn, so a status line
# there would arrive after the review it describes.
NO_LIVE_STATUS = frozenset({"", "cli"})


@dataclass(frozen=True)
class Request:
    host: Path
    sandbox: str | None
    base: str
    task_id: str | None
    platform: str


def run(
    ctx: Any,
    request: Request,
    store: Callable[[Path], records.Store],
    verify_command: str,
) -> str:
    if not hasattr(ctx, "redact_text"):
        return (
            "Review not run: this Hermes cannot redact secrets for plugins (no ctx.redact_text), "
            "and the diff would leave the machine unredacted. Run `hermes update`, then "
            "restart the Hermes gateway."
        )
    target, base = request.host, request.base
    try:
        sha = repo.head_sha(target)
        diff = repo.diff_since(target, base)
        reviews = store(target)
        previous = reviews.previous_round(sha, base)
    except repo.RepoError as exc:
        return f"Review failed: {exc}"
    if request.sandbox is None:
        return (
            f"Review not run: cannot tell where {target} is in the terminal sandbox, so verify "
            "cannot run first. Run a terminal command in this session, then try again."
        )

    status = _Status(ctx, request.platform)
    status.show(f"loopverify: verifying {sha[:10]} against {base}")
    try:
        checked = verify_run.run(ctx, request.sandbox, request.task_id, verify_command)
    except verify_run.VerifyError as exc:
        status.show(f"loopverify: verify of {sha[:10]} could not run")
        return f"Review not run: verify could not run: {exc}"
    if checked.sha != sha:
        return f"Review not run: verify checked {checked.sha[:10]}, but HEAD is {sha[:10]}."
    if not checked.clean:
        return "Review not run: the working tree has uncommitted changes; commit them first."
    if not checked.passed:
        status.show(f"loopverify: verify of {sha[:10]} failed")
        return (
            f"Review not run: verify failed for {sha[:10]}: {checked.describe()}. "
            "Run `lverify verify` in the repo for the details."
        )

    name = f"{reviewer.PROVIDER}:{reviewer.MODEL}"
    status.show(f"loopverify: {name} is reviewing {sha[:10]} against {base}")
    started = time.monotonic()
    try:
        verdict = reviewer.review(
            ctx.llm,
            reviewer.ReviewInput(diff=diff, verify_output=checked.describe(), previous=previous),
            lambda text: ctx.redact_text(text, force=True),
        )
    except reviewer.ReviewError as exc:
        status.show(f"loopverify: review of {sha[:10]} failed")
        reply = f"Review of {sha[:10]} by {name} failed after {_since(started)}: {exc}"
        if exc.raw:
            raw = reviews.save_failed_reply(sha, exc.raw)
            reply += f"\nStart of the reply:\n{exc.raw[:EXCERPT_CHARS]}\nFull reply: {raw}"
        return reply
    except PermissionError as exc:
        status.show(f"loopverify: review of {sha[:10]} not allowed")
        return (
            f"Review not allowed: {exc}\n"
            "Run `hermes plugins enable loopverify` in a terminal and grant "
            "llm.provider_override and llm.model_override."
        )
    seconds = time.monotonic() - started
    outcome = "signed off" if verdict.signed_off else "rejected"
    status.show(f"loopverify: review of {sha[:10]} {outcome} in {seconds:.0f} s")
    record = reviews.save_review(
        reviewer.to_record(verdict, sha, base, seconds, verify=checked.to_record())
    )
    header = (
        f"Review of {sha[:10]} against {base} ({_duration(seconds)}, {_token_usage(verdict)})\n"
        f"Verify: {checked.describe()}"
    )
    reply = f"{header}\n\n{reviewer.render(verdict)}\n\nSaved to {record}"
    if verdict.signed_off:
        reply += (
            f"\n\nPut this section in the PR body:\n\n{reviews.standing(sha).signoff_section()}"
        )
    else:
        reply += f"\nTo overrule a finding you disagree with: {OVERRULE_USAGE}"
    return reply


def _duration(seconds: float) -> str:
    minutes, rest = divmod(round(seconds), 60)
    return f"{minutes} min {rest} s" if minutes else f"{rest} s"


def _token_usage(verdict: reviewer.Review) -> str:
    if not verdict.input_tokens:
        return "token usage not reported, possibly a cached reply"
    return f"{verdict.input_tokens:,} tokens in, {verdict.output_tokens:,} out"


class _Status:
    """One live status line, edited in place, on surfaces that can show it while we wait."""

    def __init__(self, ctx: Any, platform: str) -> None:
        self.ctx = ctx
        self.live = platform not in NO_LIVE_STATUS
        self.handle: Any = None

    def show(self, text: str) -> None:
        if not self.live:
            return
        if self.handle is None:
            self.handle = self.ctx.post_message(text)
        else:
            self.handle.edit(text)


def _since(started: float) -> str:
    return f"{time.monotonic() - started:.0f} s"
