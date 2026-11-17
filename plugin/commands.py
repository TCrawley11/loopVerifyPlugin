"""The /lverify slash command: routes `/lverify <subcommand> [args]` to its handler."""

import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import p0, repo, reviewer

DEFAULT_BASE = "origin/HEAD"
REPORT_FILE = "p0-report.html"
LAST_REPLY_FILE = "review-last-reply.txt"
EXCERPT_CHARS = 300
# The classic CLI prints a plugin's live message only after the next turn, so a status line
# there would arrive after the review it describes.
NO_LIVE_STATUS = frozenset({"", "cli"})


@dataclass(frozen=True)
class Call:
    """One invocation of /lverify: the text after the subcommand and who sent it."""

    args: str
    platform: str
    session_id: str


@dataclass(frozen=True)
class Subcommand:
    usage: str
    summary: str
    run: Callable[[Call], str]


def make_handler(ctx: Any, log: p0.EvidenceLog, data_dir: Path) -> Callable[..., str]:
    """Build the handler Hermes calls for /lverify."""
    subcommands = {
        "report": Subcommand(
            "report",
            "open the HTML report in the browser",
            lambda call: p0.open_report(log, data_dir / REPORT_FILE, call.platform),
        ),
        "review": Subcommand(
            "review [repo] [base]",
            f"independent review of HEAD against base (default {DEFAULT_BASE})",
            lambda call: _review(ctx, call, data_dir / LAST_REPLY_FILE),
        ),
        "p0": Subcommand(
            "p0",
            "run the P0 platform probes and show the evidence",
            lambda call: p0.run_probes(ctx, log, call.platform, call.session_id),
        ),
    }

    def handle(raw_args: str, *, platform: str = "", session_id: str = "") -> str:
        name, _, rest = raw_args.strip().partition(" ")
        subcommand = subcommands.get(name)
        if subcommand is None:
            return _usage(subcommands)
        return subcommand.run(Call(args=rest.strip(), platform=platform, session_id=session_id))

    return handle


def _usage(subcommands: dict[str, Subcommand]) -> str:
    lines = [f"/lverify {sub.usage}: {sub.summary}" for sub in subcommands.values()]
    return "\n".join(["Usage:", *lines])


def _review(ctx: Any, call: Call, last_reply: Path) -> str:
    words = call.args.split()
    if len(words) > 2:
        return "Usage: /lverify review [repo] [base]"
    if not hasattr(ctx, "redact_text"):
        return (
            "Review not run: this Hermes cannot redact secrets for plugins (no ctx.redact_text), "
            "and the diff would leave the machine unredacted. Run `hermes update`, then "
            "restart the Hermes gateway."
        )
    target = Path(ctx.session_cwd() or ".") / (words[0] if words else ".")
    base = words[1] if len(words) == 2 else DEFAULT_BASE
    try:
        sha = repo.head_sha(target)
        diff = repo.diff_since(target, base)
    except repo.RepoError as exc:
        return f"Review failed: {exc}"

    name = f"{reviewer.PROVIDER}:{reviewer.MODEL}"
    status = _Status(ctx, call.platform)
    status.show(f"loopverify: {name} is reviewing {sha[:10]} against {base}")
    started = time.monotonic()
    try:
        verdict = reviewer.review(
            ctx.llm,
            reviewer.ReviewInput(diff=diff),
            lambda text: ctx.redact_text(text, force=True),
        )
    except reviewer.ReviewError as exc:
        status.show(f"loopverify: review of {sha[:10]} failed")
        reply = f"Review of {sha[:10]} by {name} failed after {_since(started)}: {exc}"
        if exc.raw:
            last_reply.parent.mkdir(parents=True, exist_ok=True)
            last_reply.write_text(exc.raw, encoding="utf-8")
            reply += f"\nStart of the reply:\n{exc.raw[:EXCERPT_CHARS]}\nFull reply: {last_reply}"
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
    record = repo.lverify_dir(target) / "reviews" / f"{sha}.json"
    record.parent.mkdir(parents=True, exist_ok=True)
    record.write_text(json.dumps(reviewer.to_record(verdict, sha, base, seconds), indent=2))
    header = f"Review of {sha[:10]} against {base} ({_duration(seconds)}, {_token_usage(verdict)})"
    return f"{header}\n\n{reviewer.render(verdict)}\n\nSaved to {record}"


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
