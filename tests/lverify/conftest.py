import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

ORIGIN = "https://github.com/Owner/proj.git"
KEY = "github.com/Owner/proj"


def git(repo: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    )
    return done.stdout.strip()


def commit_all(repo: Path, message: str) -> str:
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", message)
    return git(repo, "rev-parse", "HEAD")


def write(repo: Path, files: dict[str, str]) -> None:
    for name, text in files.items():
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(text))


def ruff_check(cwd: str = ".") -> str:
    command = [sys.executable, "-m", "ruff", "check", "--output-format", "json", "."]
    return f"""
[[check]]
name = "lint"
parser = "ruff"
cwd = "{cwd}"
command = {command!r}
""".replace("'", '"')


class StateRemote:
    """A bare state repo reachable over file://, like a private GitHub repo would be."""

    def __init__(self, root: Path) -> None:
        self.bare = root / "state.git"
        self.work = root / "state-work"
        git(root, "init", "-q", "--bare", "-b", "main", str(self.bare))
        git(self.bare, "config", "uploadpack.allowFilter", "true")
        git(root, "init", "-q", "-b", "main", str(self.work))
        git(self.work, "config", "user.name", "test")
        git(self.work, "config", "user.email", "test@example.com")
        git(self.work, "remote", "add", "origin", str(self.bare))

    @property
    def url(self) -> str:
        return self.bare.as_uri()

    def publish(self, files: dict[str, str]) -> None:
        write(self.work, files)
        commit_all(self.work, "state")
        git(self.work, "push", "-q", "origin", "main")

    def set_config(self, text: str, key: str = KEY) -> None:
        self.publish({f"{key}/config.toml": text})


@pytest.fixture
def remote(tmp_path: Path) -> StateRemote:
    state = StateRemote(tmp_path)
    state.publish({"github.com/Other/elsewhere/config.toml": 'base = "main"\n'})
    return state


@pytest.fixture
def project(tmp_path: Path, remote: StateRemote) -> Path:
    """A project repo on branch `feat`, forked from `main`, connected to the state repo."""
    repo = tmp_path / "proj"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.name", "test")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "remote", "add", "origin", ORIGIN)
    git(repo, "config", "lverify.stateRepo", remote.url)
    write(repo, {"README.md": "proj\n"})
    commit_all(repo, "base")
    git(repo, "checkout", "-qb", "feat")
    return repo
