"""The /lverify slash command: routes `/lverify <subcommand> [args]` to its handler."""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import p0, repo, reviewer

DEFAULT_BASE = "origin/HEAD"
REPORT_FILE = "p0-report.html"


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
            lambda call: _review(ctx, call),
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


def _review(ctx: Any, call: Call) -> str:
    words = call.args.split()
    if len(words) > 2:
        return "Usage: /lverify review [repo] [base]"
    if not hasattr(ctx, "redact_text"):
        return (
            "Review not run: this Hermes cannot redact secrets for plugins (no ctx.redact_text), "
            "and the diff would leave the machine unredacted. Run `hermes update`, then "
            "`hermes gateway restart`."
        )
    target = Path(ctx.session_cwd() or ".") / (words[0] if words else ".")
    base = words[1] if len(words) == 2 else DEFAULT_BASE
    try:
        sha = repo.head_sha(target)
        verdict = reviewer.review(
            ctx.llm,
            reviewer.ReviewInput(diff=repo.diff_since(target, base)),
            lambda text: ctx.redact_text(text, force=True),
        )
    except (repo.RepoError, reviewer.ReviewError) as exc:
        return f"Review failed: {exc}"
    except PermissionError as exc:
        return (
            f"Review not allowed: {exc}\n"
            "Run `hermes plugins enable loopverify` in a terminal and grant "
            "llm.provider_override and llm.model_override."
        )
    return f"Review of {sha} against {base}\n{reviewer.render(verdict)}"
