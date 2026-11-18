"""The /lverify slash command: routes `/lverify <subcommand> [args]` to its handler."""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import locate, p0, records, repo, review_flow, verify_run

DEFAULT_BASE = review_flow.DEFAULT_BASE
REPORT_FILE = "p0-report.html"
OVERRULE_USAGE = review_flow.OVERRULE_USAGE


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


def make_handler(
    ctx: Any,
    log: p0.EvidenceLog,
    data_dir: Path,
    env_for: Callable[[str | None], Any] = locate.active_env,
) -> Callable[..., str]:
    """Build the handler Hermes calls for /lverify. `env_for(task_id)` returns the session's
    terminal environment."""

    def store(target: Path) -> records.Store:
        return records.Store(target, records.load_key(data_dir))

    verify_command = str(ctx.get_config("verify_command", verify_run.DEFAULT_COMMAND))

    subcommands = {
        "report": Subcommand(
            "report",
            "open the HTML report in the browser",
            lambda call: p0.open_report(log, data_dir / REPORT_FILE, call.platform),
        ),
        "review": Subcommand(
            "review [repo] [base]",
            f"independent review of HEAD against base (default {DEFAULT_BASE})",
            lambda call: _review(ctx, call, store, verify_command, env_for),
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


def _review(
    ctx: Any,
    call: Call,
    store: Callable[[Path], records.Store],
    verify_command: str,
    env_for: Callable[[str | None], Any],
) -> str:
    words = call.args.split()
    if len(words) > 2:
        return "Usage: /lverify review [repo] [base]"
    host = Path(ctx.session_cwd() or ".") / (words[0] if words else ".")
    env = env_for(call.session_id or None)
    request = review_flow.Request(
        host=host,
        sandbox=locate.to_sandbox(str(host), env),
        base=words[1] if len(words) == 2 else DEFAULT_BASE,
        task_id=call.session_id or None,
        platform=call.platform,
    )
    return review_flow.run(ctx, request, store, verify_command)


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
    reply = f"{sha[:10]} is {state}.\n\n{records.render_overrules(standing.overrules)}"
    if standing.cleared:
        reply += f"\n\nPut this section in the PR body:\n\n{standing.signoff_section()}"
    return reply


def _is_numbers(word: str) -> bool:
    return all(part.isdigit() for part in word.split(","))
