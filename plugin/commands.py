"""The /lverify slash command: routes `/lverify <subcommand> [args]` to its handler."""

import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import p0, records, repo, reviewer

DEFAULT_BASE = "origin/HEAD"
REPORT_FILE = "p0-report.html"
EXCERPT_CHARS = 300
OVERRULE_USAGE = "/lverify overrule [repo] <finding>[,<finding>...] <reason>"
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

    def store(target: Path) -> records.Store:
        return records.Store(target, records.load_key(data_dir))

    subcommands = {
        "report": Subcommand(
            "report",
            "open the HTML report in the browser",
            lambda call: p0.open_report(log, data_dir / REPORT_FILE, call.platform),
        ),
        "review": Subcommand(
            "review [repo] [base]",
            f"independent review of HEAD against base (default {DEFAULT_BASE})",
            lambda call: _review(ctx, call, store),
        ),
        "overrule": Subcommand(
            OVERRULE_USAGE.removeprefix("/lverify "),
            "you decide that blocking findings of HEAD's review do not block; the reason "
            "goes into the PR body",
            lambda call: _overrule(ctx, call, store),
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


def _review(ctx: Any, call: Call, store: Callable[[Path], records.Store]) -> str:
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
        reviews = store(target)
        previous = reviews.previous_round(sha, base)
    except repo.RepoError as exc:
        return f"Review failed: {exc}"

    name = f"{reviewer.PROVIDER}:{reviewer.MODEL}"
    status = _Status(ctx, call.platform)
    status.show(f"loopverify: {name} is reviewing {sha[:10]} against {base}")
    started = time.monotonic()
    try:
        verdict = reviewer.review(
            ctx.llm,
            reviewer.ReviewInput(diff=diff, previous=previous),
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
    record = reviews.save_review(reviewer.to_record(verdict, sha, base, seconds))
    header = f"Review of {sha[:10]} against {base} ({_duration(seconds)}, {_token_usage(verdict)})"
    reply = f"{header}\n\n{reviewer.render(verdict)}\n\nSaved to {record}"
    if not verdict.signed_off:
        reply += f"\nTo overrule a finding you disagree with: {OVERRULE_USAGE}"
    return reply


def _duration(seconds: float) -> str:
    minutes, rest = divmod(round(seconds), 60)
    return f"{minutes} min {rest} s" if minutes else f"{rest} s"


def _overrule(ctx: Any, call: Call, store: Callable[[Path], records.Store]) -> str:
    words = call.args.split()
    where = "."
    if words and not _is_numbers(words[0]):
        where, words = words[0], words[1:]
    if len(words) < 2 or not _is_numbers(words[0]):
        return f"Usage: {OVERRULE_USAGE}"
    numbers = sorted({int(n) for n in words[0].split(",")})
    target = Path(ctx.session_cwd() or ".") / where
    by = f"you on {call.platform or 'cli'}"
    try:
        sha = repo.head_sha(target)
        standing = store(target).overrule(sha, numbers, " ".join(words[1:]), by)
    except (repo.RepoError, records.RecordError) as exc:
        return f"Overrule failed: {exc}"
    state = (
        "cleared: no blocking findings left"
        if standing.cleared
        else f"still blocked by {len(standing.open_findings)} finding(s)"
    )
    return f"{sha[:10]} is {state}.\n\n{records.render_overrules(standing.overrules)}"


def _is_numbers(word: str) -> bool:
    return all(part.isdigit() for part in word.split(","))


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
