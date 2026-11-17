"""Reads the state of a git repository on the Hermes host."""

import subprocess
from pathlib import Path

GIT_TIMEOUT_SECONDS = 60


class RepoError(Exception):
    """A git command failed; the message is safe to show to the user."""


def _git(repo: Path, *args: str) -> str:
    try:
        done = subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RepoError(f"could not run git in {repo}: {exc}") from exc
    if done.returncode != 0:
        raise RepoError(f"git {' '.join(args)} failed in {repo}: {done.stderr.strip()}")
    return done.stdout


def head_sha(repo: Path) -> str:
    """The full SHA of HEAD."""
    return _git(repo, "rev-parse", "HEAD").strip()


def diff_since(repo: Path, base: str) -> str:
    """What HEAD changed since it forked from `base` (committed changes only)."""
    return _git(repo, "diff", "--no-color", "--no-ext-diff", f"{base}...HEAD")


def lverify_dir(repo: Path) -> Path:
    """lverify's folder in the repo's git dir: shared with the sandbox, never pushed."""
    common = _git(repo, "rev-parse", "--path-format=absolute", "--git-common-dir").strip()
    return Path(common) / "lverify"
