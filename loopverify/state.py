"""Where lverify keeps a project's state: a folder in the user's private state repo.

Every project repo keeps a small clone of the state repo in its own git dir, so the state is
reachable wherever the repo is (a sandbox mounts only the repo) and never lands in the repo's
tree, branches or pushes. The clone is partial (commits and trees only; file contents are
fetched for this project's folder alone) and is refreshed at most once per FETCH_TTL_SECONDS.
"""

import os
import re
import time
from dataclasses import dataclass
from pathlib import Path

from . import git
from .errors import SetupError

STATE_REPO_KEY = "lverify.stateRepo"
FETCH_TTL_SECONDS = 3600
CONFIG_FILE = "config.toml"

_URL_FORMS = (
    re.compile(r"^(?:https?|ssh|git)://(?:[^@/]+@)?(?P<host>[^/:]+)(?::\d+)?/(?P<path>.+)$"),
    re.compile(r"^(?:[^@/]+@)?(?P<host>[^/:]+):(?!/)(?P<path>.+)$"),
)


@dataclass(frozen=True)
class ProjectState:
    key: str
    clone: Path
    folder: Path
    refreshed: bool
    warning: str | None

    @property
    def config_file(self) -> Path:
        return self.folder / CONFIG_FILE


def project_key(origin_url: str) -> str:
    """`host/owner/repo` for a remote URL: the project's folder name in the state repo."""
    for form in _URL_FORMS:
        match = form.match(origin_url.strip())
        if match:
            path = match["path"].strip("/").removesuffix(".git").strip("/")
            if path and ".." not in path.split("/"):
                return f"{match['host'].lower()}/{path}"
    raise SetupError(f"cannot derive a project name from the remote URL {origin_url!r}")


def open_state(
    repo: Path, *, force_refresh: bool = False, now: float | None = None
) -> ProjectState:
    """The repo's project folder in the state repo, cloning or refreshing the clone if due."""
    url = git.config_get(repo, STATE_REPO_KEY)
    if url is None:
        raise SetupError(
            f"no state repo configured; run `lverify init --state-repo <url>` "
            f"(it sets git config {STATE_REPO_KEY})"
        )
    key = project_key(git.origin_url(repo))
    home = git.common_dir(repo) / "lverify"
    clone = home / "state"
    marker = home / "state-fetched"
    now = time.time() if now is None else now

    refreshed, warning = False, None
    if not (clone / ".git").exists():
        _clone(url, clone, key)
        refreshed = True
    elif force_refresh or _is_due(marker, now):
        refreshed, warning = _refresh(clone)
    if refreshed or warning:
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.touch()
        os.utime(marker, (now, now))
    return ProjectState(key, clone, clone / key, refreshed, warning)


def _clone(url: str, clone: Path, key: str) -> None:
    clone.parent.mkdir(parents=True, exist_ok=True)
    clone_args = ["--quiet", "--filter=blob:none", "--sparse", url, str(clone)]
    git.git(clone.parent, "clone", *clone_args)
    git.git(clone, "sparse-checkout", "set", key)


def _refresh(clone: Path) -> tuple[bool, str | None]:
    try:
        git.git(clone, "fetch", "--quiet", "origin")
        git.git(clone, "merge", "--quiet", "--ff-only", "@{u}")
    except git.GitError as exc:
        return False, f"could not refresh the state repo, using the cached copy: {exc}"
    return True, None


def _is_due(marker: Path, now: float) -> bool:
    try:
        return now - marker.stat().st_mtime >= FETCH_TTL_SECONDS
    except FileNotFoundError:
        return True
