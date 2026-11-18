"""The PR gate: a pre_tool_call hook on terminal commands.

In a repo connected to lverify (`lverify init`), `gh pr create` and a `git push` to a branch
that has a PR only run when HEAD has the plugin's own passing verify and a reviewer sign-off
(or every blocking finding overruled by the human), and the PR body carries the sign-off line.
In a repo with the upstream guard on, no push may carry lverify files. Other repos and other
commands pass untouched.

`decide` is the policy; everything else here turns a command into the facts it needs.
"""

import re
import shlex
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from . import locate, records

PROBE_MARKER = "LVERIFY_P0_PROBE"
BLOCK_MESSAGE = (
    "loopverify gate: blocked `gh pr create` (P0 probe). "
    "From P1 this needs a verify stamp and a reviewer sign-off for HEAD."
)
SIGNOFF_LINE = "lverify-signoff: {sha}"
GUARD_KEY = "lverify.guardUpstream"
STATE_KEY = "lverify.stateRepo"
GIT_TIMEOUT_SECONDS = 30

_GATED = re.compile(r"\bgh\s+pr\s+create\b|\bgit\b.*\bpush\b")
_PR_CREATE = re.compile(r"\bgh\s+pr\s+create\b")
_SEPARATORS = frozenset({"&&", "||", ";", "|", "&", "\n"})
_PUSH_FLAGS_WITH_VALUE = frozenset({"--repo", "-o", "--push-option", "--receive-pack", "--exec"})


@dataclass(frozen=True)
class PrCreate:
    sha: str
    body: str | None
    branch: str = ""


@dataclass(frozen=True)
class Push:
    branch: str
    sha: str
    has_pr: bool
    guarded: bool
    lverify_paths: tuple[str, ...]


@dataclass(frozen=True)
class Unreadable:
    """A gated command whose shape the gate cannot read, such as one inside `bash -c`."""

    command: str


Action = PrCreate | Push | Unreadable


def decide(action: Action, standing: records.Standing | None) -> str | None:
    """None to let `action` run, else why it is blocked and what to do instead.

    PrCreate: needs a sealed record for `sha` whose verify passed and that has no open blocking
    findings, and a body containing SIGNOFF_LINE for `sha`.
    Push: a guarded repo never pushes lverify files; a push to a branch with a PR needs the same
    record as PrCreate (the body rule does not apply). Any other push runs.
    Unreadable: blocked, with how to write the command so the gate can read it.
    """
    raise NotImplementedError("the core of P1.3: see tests/unit/test_gate_decide.py")


def opens_pr(tool_name: str, args: Any) -> bool:
    """True when the tool call is a terminal command that runs `gh pr create`."""
    if tool_name != "terminal" or not isinstance(args, dict):
        return False
    command = args.get("command")
    return isinstance(command, str) and bool(_PR_CREATE.search(command))


def make_hook(
    store: Callable[[Path], records.Store], env_for: Callable[[str | None], Any]
) -> Callable[..., dict[str, str] | None]:
    """The pre_tool_call hook. `env_for(task_id)` returns the session's terminal environment."""

    def hook(
        tool_name: str = "", args: Any = None, task_id: str | None = None, **_: Any
    ) -> dict[str, str] | None:
        if tool_name != "terminal" or not isinstance(args, dict):
            return None
        command = args.get("command")
        if not isinstance(command, str) or not _GATED.search(command):
            return None
        if PROBE_MARKER in command and _PR_CREATE.search(command):
            return _block(BLOCK_MESSAGE)
        env = env_for(task_id)
        where = locate.place(command, args.get("workdir"), env)
        if where is None:
            return _block(
                "loopverify gate: cannot tell which repo this command runs in. Run it as "
                "`cd <repo> && <command>`; if this is the session's first terminal command, run "
                "any other command (such as `pwd`) first."
            )
        host = Path(where.host)
        if not _connected(host):
            return None
        try:
            reviews = store(host)
            for action in actions(command, host, where.sandbox, env, reviews):
                reason = decide(action, _standing(reviews, action))
                if reason is not None:
                    return _block(f"loopverify gate: {reason}")
                if isinstance(action, PrCreate):
                    reviews.add_pr_branch(action.branch)
        except (records.RecordError, OSError, subprocess.SubprocessError) as exc:
            return _block(f"loopverify gate: could not check this command, so it is blocked: {exc}")
        return None

    return hook


def actions(
    command: str, host: Path, sandbox_dir: str, env: Any, reviews: records.Store
) -> list[Action]:
    """The gated actions in `command`, resolved against the repo at `host`."""
    found: list[Action] = []
    for words in _segments(command):
        if words[:3] == ["gh", "pr", "create"]:
            branch = _option(words[3:], "--head", "-H") or _git(
                host, "rev-parse", "--abbrev-ref", "HEAD"
            )
            sha = _git(host, "rev-parse", f"{branch}^{{commit}}")
            found.append(PrCreate(sha, _body(words[3:], sandbox_dir, env), branch))
        elif words[:1] == ["git"]:
            elsewhere, rest = _git_subcommand(words[1:])
            if rest[:1] == ["push"]:
                found.extend(
                    [Unreadable(command)]
                    if elsewhere
                    else _pushes(rest[1:], host, reviews, command)
                )
    if not found and _GATED.search(command) and _looks_wrapped(command):
        found.append(Unreadable(command))
    return found


def _standing(reviews: records.Store, action: Action) -> records.Standing | None:
    sha = getattr(action, "sha", None)
    if sha is None:
        return None
    try:
        return reviews.standing(sha)
    except records.RecordError:
        return None


def _segments(command: str) -> list[list[str]]:
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|")
        lexer.whitespace_split = True
        words = list(lexer)
    except ValueError:
        return []
    segments: list[list[str]] = [[]]
    for word in words:
        if word in _SEPARATORS:
            segments.append([])
        else:
            segments[-1].append(word)
    return [_without_env_assignments(s) for s in segments if s]


def _without_env_assignments(words: list[str]) -> list[str]:
    index = 0
    while index < len(words) and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", words[index]):
        index += 1
    return words[index:]


def _git_subcommand(words: list[str]) -> tuple[bool, list[str]]:
    """Whether `-C` moves git to another folder, and the words from the subcommand on."""
    index, elsewhere = 0, False
    while index < len(words) and words[index].startswith("-"):
        elsewhere = elsewhere or words[index] == "-C"
        index += 2 if words[index] in ("-c", "-C") else 1
    return elsewhere, words[index:]


def _option(words: list[str], *names: str) -> str | None:
    for index, word in enumerate(words):
        flag, eq, inline = word.partition("=")
        if flag in names:
            if eq:
                return inline
            return words[index + 1] if index + 1 < len(words) else None
    return None


def _looks_wrapped(command: str) -> bool:
    return bool(re.search(r"\b(bash|sh|zsh|eval|xargs)\b", command))


def _body(words: list[str], sandbox_dir: str, env: Any) -> str | None:
    """The PR body as gh would send it, or None when the gate cannot read it."""
    text = _option(words, "--body", "-b")
    if text is not None:
        return text
    path = _option(words, "--body-file", "-F")
    if path is None or path == "-":
        return None
    host_file = locate.to_host(locate.normal(str(PurePosixPath(sandbox_dir) / path)), env)
    try:
        return Path(host_file).read_text(encoding="utf-8") if host_file else None
    except OSError:
        return None


def _pushes(words: list[str], host: Path, reviews: records.Store, command: str) -> list[Action]:
    if any(flag in words for flag in ("--all", "--mirror", "--tags")):
        return [Unreadable(command)]
    if any(flag in words for flag in ("-d", "--delete")):
        return []
    positional: list[str] = []
    skip = False
    for word in words:
        if skip:
            skip = False
        elif word in _PUSH_FLAGS_WITH_VALUE:
            skip = True
        elif not word.startswith("-"):
            positional.append(word)
    refspecs = positional[1:] or [_git(host, "rev-parse", "--abbrev-ref", "HEAD")]
    remote = positional[0] if positional else "origin"
    guarded = _git(host, "config", "--bool", GUARD_KEY, check=False) == "true"
    pushes: list[Action] = []
    for spec in refspecs:
        source, _, target = spec.lstrip("+").partition(":")
        if not source:
            continue
        branch = (target or source).removeprefix("refs/heads/")
        sha = _git(host, "rev-parse", f"{source}^{{commit}}")
        pushes.append(
            Push(
                branch=branch,
                sha=sha,
                has_pr=branch in reviews.pr_branches(),
                guarded=guarded,
                lverify_paths=_lverify_paths(host, sha, remote),
            )
        )
    return pushes


def _lverify_paths(host: Path, sha: str, remote: str) -> tuple[str, ...]:
    """lverify files in the commits this push would add to `remote`."""
    names = _git(host, "log", "--format=", "--name-only", sha, "--not", f"--remotes={remote}")
    return tuple(sorted({p for p in names.splitlines() if p and _is_lverify_path(p)}))


def _is_lverify_path(path: str) -> bool:
    parts = PurePosixPath(path).parts
    return ".lverify" in parts or PurePosixPath(path).name.startswith("lverify")


def _connected(host: Path) -> bool:
    return bool(_git(host, "config", "--get", STATE_KEY, check=False))


def _git(repo: Path, *args: str, check: bool = True) -> str:
    done = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        timeout=GIT_TIMEOUT_SECONDS,
        check=False,
    )
    if check and done.returncode != 0:
        raise records.RecordError(f"git {' '.join(args)} failed: {done.stderr.strip()}")
    return done.stdout.strip()


def _block(message: str) -> dict[str, str]:
    return {"action": "block", "message": message}
