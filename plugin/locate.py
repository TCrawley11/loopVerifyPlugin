"""Where a terminal command runs: the folder in the sandbox and the same folder on the host.

The gate hook and the review tool run in the Hermes gateway on the host, while terminal commands
run in the docker sandbox, which mounts one host folder (usually at /workspace). A command runs
in its `workdir`, else in a leading `cd <dir> &&`, else in the sandbox's current folder.
"""

import shlex
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any


@dataclass(frozen=True)
class Place:
    sandbox: str
    host: str


def command_dir(command: str, workdir: str | None, env: Any) -> str | None:
    """The sandbox folder `command` runs in, or None when it cannot be told."""
    current = workdir or getattr(env, "cwd", None) or None
    words = _words(command)
    if len(words) >= 3 and words[0] == "cd" and words[2] in ("&&", ";"):
        target = words[1]
        if target.startswith("/"):
            return normal(target)
        if current is None:
            return None
        return normal(f"{current}/{target}")
    return normal(current) if current else None


def active_env(task_id: str | None) -> Any:
    """The session's terminal environment, from a Hermes internal pinned by an integration test.
    Without a task id (a slash command) this is the CLI's shared "default" sandbox. None when
    there is none yet or this Hermes lacks the function; callers then fail closed."""
    try:
        from tools.terminal_tool_lifecycle import get_active_env
    except ImportError:
        return None
    return get_active_env(task_id or "default")


def to_host(sandbox_dir: str, env: Any) -> str | None:
    """The host folder for `sandbox_dir`: through a container env's mount (docker sets
    `host_cwd_mount`), or unchanged on a local backend. None without an env, or when the folder
    is not under the mounted host folder."""
    if env is None:
        return None
    host_root = getattr(env, "host_cwd", None)
    mount = getattr(env, "host_cwd_mount", None)
    if not hasattr(env, "host_cwd_mount"):
        return sandbox_dir
    if not (host_root and mount):
        return None
    path, root = PurePosixPath(sandbox_dir), PurePosixPath(mount)
    if path != root and root not in path.parents:
        return None
    return str(PurePosixPath(host_root) / path.relative_to(root))


def to_sandbox(host_dir: str, env: Any) -> str | None:
    """The sandbox folder for `host_dir`: the inverse of `to_host`."""
    if env is None:
        return None
    if not hasattr(env, "host_cwd_mount"):
        return host_dir
    host_root, mount = getattr(env, "host_cwd", None), getattr(env, "host_cwd_mount", None)
    if not (host_root and mount):
        return None
    path, root = PurePosixPath(normal(host_dir)), PurePosixPath(host_root)
    if path != root and root not in path.parents:
        return None
    return str(PurePosixPath(mount) / path.relative_to(root))


def place(command: str, workdir: str | None, env: Any) -> Place | None:
    sandbox = command_dir(command, workdir, env)
    if sandbox is None:
        return None
    host = to_host(sandbox, env)
    return Place(sandbox, host) if host else None


def _words(command: str) -> list[str]:
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        return list(lexer)
    except ValueError:
        return command.split()


def normal(path: str) -> str:
    parts: list[str] = []
    for part in PurePosixPath(path).parts:
        if part == "..":
            if len(parts) > 1:
                parts.pop()
        elif part != ".":
            parts.append(part)
    return str(PurePosixPath(*parts)) if parts else "/"
