"""Thin wrapper around the git CLI."""

import subprocess
from pathlib import Path

GIT_TIMEOUT_SECONDS = 300


class GitError(Exception):
    """A git command failed; the message is safe to show to the user."""


def git(repo: Path, *args: str, input_bytes: bytes | None = None) -> str:
    return git_bytes(repo, *args, input_bytes=input_bytes).decode("utf-8", "replace")


def git_bytes(repo: Path, *args: str, input_bytes: bytes | None = None) -> bytes:
    try:
        done = subprocess.run(
            ["git", "-C", str(repo), *args],
            input=input_bytes,
            capture_output=True,
            timeout=GIT_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise GitError(f"could not run git in {repo}: {exc}") from exc
    if done.returncode != 0:
        stderr = done.stderr.decode("utf-8", "replace").strip()
        raise GitError(f"git {' '.join(args)} failed in {repo}: {stderr}")
    return done.stdout


def toplevel(path: Path) -> Path:
    return Path(git(path, "rev-parse", "--show-toplevel").strip())


def common_dir(repo: Path) -> Path:
    """The git dir shared by all worktrees; never pushed, so lverify keeps its files here."""
    return Path(git(repo, "rev-parse", "--path-format=absolute", "--git-common-dir").strip())


def head_sha(repo: Path) -> str:
    return git(repo, "rev-parse", "HEAD").strip()


def merge_base(repo: Path, ref: str) -> str:
    return git(repo, "merge-base", "HEAD", ref).strip()


def is_clean(repo: Path) -> bool:
    return git(repo, "status", "--porcelain").strip() == ""


def config_get(repo: Path, key: str) -> str | None:
    try:
        value = git(repo, "config", "--get", key).strip()
    except GitError:
        return None
    return value or None


def origin_url(repo: Path) -> str:
    return git(repo, "remote", "get-url", "origin").strip()
